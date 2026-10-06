"""MongoDB-backed daily campaigns. No work survives only in process memory."""
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from pymongo import ReturnDocument, UpdateOne
from pymongo.errors import DuplicateKeyError

from models.email import MailSession
from models.hadeeth import render_reminder

logger = logging.getLogger(__name__)
TORONTO = ZoneInfo('America/Toronto')
DELIVERY_START_HOUR = 10
STATES = ('pending', 'sending', 'sent', 'retryable_failure', 'permanent_failure',
          'uncertain', 'unsubscribed', 'expired')
OPEN_STATES = ('pending', 'retryable_failure', 'sending')


def campaign_date(now):
    local = now.astimezone(TORONTO)
    return (local.date() if local.hour >= DELIVERY_START_HOUR else local.date() - timedelta(days=1)).isoformat()


class CampaignSender:
    def __init__(self, db, subscribers, content, mail_factory=MailSession,
                 now=lambda: datetime.now(timezone.utc)):
        self.campaigns = db['email_campaigns']
        self.deliveries = db['email_deliveries']
        self.subscribers = subscribers
        self.content = content
        self.mail_factory = mail_factory
        self.now = now
        self.indexes_ready = False

    def ensure_indexes(self):
        if not self.indexes_ready:
            # Campaign _id is the unique local date.
            self.deliveries.create_index([('campaign', 1), ('email', 1)], unique=True)
            self.deliveries.create_index([('campaign', 1), ('status', 1), ('retry_at', 1)])
            self.subscribers.create_index('email', unique=True)
            self.indexes_ready = True

    def run(self, budget):
        self.ensure_indexes()
        now = self.now()
        date = campaign_date(now)
        self.deliveries.update_many({'campaign': {'$lt': date}, 'status': 'sending'},
                                    {'$set': {'status': 'uncertain', 'code': 'interrupted'}})
        self.deliveries.update_many({'campaign': {'$lt': date}, 'status': {'$in': ['pending', 'retryable_failure']}},
                                    {'$set': {'status': 'expired'}})
        self.campaigns.update_many({'_id': {'$lt': date}, 'status': {'$in': ['preparing', 'running']}},
                                   {'$set': {'status': 'expired'}, '$unset': {'recipients': ''}})
        campaign = self.campaigns.find_one({'_id': date})
        if not campaign:
            if now.astimezone(TORONTO).hour < DELIVERY_START_HOUR:
                return self.summary(date, 'not_due')
            # One persisted snapshot for the current (<500 subscriber) audience.
            # Delivery documents are materialized in resumable chunks below.
            recipients = list(self.subscribers.find({}, {'email': 1}))
            try:
                self.campaigns.insert_one({'_id': date, 'status': 'preparing',
                    'created_at': now, 'recipients': recipients, 'offset': 0,
                    'total': len(recipients), 'content': {}})
            except DuplicateKeyError:
                pass
        owner = uuid4().hex
        campaign = self.campaigns.find_one_and_update(
            {'_id': date, 'status': {'$in': ['preparing', 'running']}, '$or': [
                {'lease_until': {'$exists': False}}, {'lease_until': {'$lte': now}}
            ]}, {'$set': {'owner': owner, 'lease_until': now + timedelta(seconds=30)}},
            return_document=ReturnDocument.AFTER,
        )
        if not campaign:
            current = self.campaigns.find_one({'_id': date})
            return self.summary(date, 'active' if current['status'] in ('preparing', 'running') else current['status'])
        owned = {'_id': date, 'owner': owner}
        try:
            # A previous lease ended with no recorded SMTP outcome. Never auto-resend.
            self.deliveries.update_many({'campaign': date, 'status': 'sending'},
                                        {'$set': {'status': 'uncertain', 'code': 'interrupted'}})
            if campaign['status'] == 'preparing':
                self._prepare(campaign, owned, budget)
            if campaign['status'] == 'running' and budget.can_work():
                self._send(campaign, owned, budget)
            result = self.summary(date, campaign['status'])
            if campaign['status'] == 'running' and not any(result['counts'][state] for state in OPEN_STATES):
                status = 'needs_review' if any(result['counts'][state] for state in ('uncertain', 'permanent_failure')) else 'complete'
                self.campaigns.update_one(owned, {'$set': {'status': status}})
                result['status'] = status
            logger.info('email_campaign date=%s status=%s counts=%s', date, result['status'], result['counts'])
            return result
        finally:
            self.campaigns.update_one(owned, {'$unset': {'owner': '', 'lease_until': ''}})

    def _prepare(self, campaign, owned, budget):
        date = campaign['_id']
        for kind in ('hadith', 'verse'):
            if kind not in campaign['content'] and budget.can_work():
                parts = self.content.prepare(kind, date, budget)
                if parts:
                    campaign['content'][kind] = parts
                    self.campaigns.update_one(owned, {'$set': {f'content.{kind}': parts}})
        if len(campaign['content']) != 2:
            return
        while campaign['offset'] < campaign['total'] and budget.can_work():
            batch = campaign['recipients'][campaign['offset']:campaign['offset'] + 100]
            self.deliveries.bulk_write([UpdateOne(
                {'campaign': date, 'email': recipient['email']},
                {'$setOnInsert': {'subscriber_id': recipient['_id'], 'status': 'pending', 'attempts': 0}},
                upsert=True,
            ) for recipient in batch], ordered=False)
            campaign['offset'] += len(batch)
            self.campaigns.update_one(owned, {'$set': {'offset': campaign['offset']}})
        if campaign['offset'] == campaign['total']:
            campaign['status'] = 'running'
            self.campaigns.update_one(owned, {'$set': {'status': 'running'}, '$unset': {'recipients': ''}})

    def _send(self, campaign, owned, budget):
        date = campaign['_id']
        due = {'campaign': date, '$or': [{'status': 'pending'},
            {'status': 'retryable_failure', 'retry_at': {'$lte': self.now()}}]}
        recipients = list(self.deliveries.find(due).sort('_id', 1).limit(5))
        if not recipients:
            return
        with self.mail_factory(budget) as mail:
            for recipient in recipients:
                if not budget.can_work() or campaign_date(self.now()) != date:
                    break
                # Fence a stale request before beginning another submission.
                if not self.campaigns.find_one({**owned, 'lease_until': {'$gt': self.now()}}):
                    break
                active = self.subscribers.find_one({'_id': recipient['subscriber_id'], 'email': recipient['email']})
                if not active:
                    self.deliveries.update_one({'_id': recipient['_id']}, {'$set': {'status': 'unsubscribed'}})
                    continue
                body = render_reminder(campaign['content'], recipient['email'], date)
                if not budget.can_work():
                    break
                attempt = recipient['attempts'] + 1
                claimed = self.deliveries.find_one_and_update({
                    '_id': recipient['_id'], 'status': recipient['status'],
                    'attempts': recipient['attempts'],
                }, {'$set': {
                    'status': 'sending', 'attempts': attempt, 'started_at': self.now()
                }})
                if not claimed:
                    continue
                result = mail.send(recipient['email'],
                    f'Daily Reminder - {datetime.fromisoformat(date).strftime("%A, %B %d, %Y")}',
                    body, f'<{date}.{recipient["_id"]}@mydailyreminder.ca>')
                status = result.status
                if status == 'retryable_failure' and attempt >= 4:
                    status = 'permanent_failure'
                values = {'status': status, 'code': result.code, 'updated_at': self.now()}
                if status == 'retryable_failure':
                    values['retry_at'] = self.now() + timedelta(minutes=2 ** attempt)
                self.deliveries.update_one({'_id': recipient['_id']}, {'$set': values})
                # End a failed session; use a fresh connection on the next call.
                if result.status != 'sent':
                    break

    def summary(self, date, status):
        counts = dict.fromkeys(STATES, 0)
        for row in self.deliveries.aggregate([
            {'$match': {'campaign': date}}, {'$group': {'_id': '$status', 'count': {'$sum': 1}}}
        ]):
            counts[row['_id']] = row['count']
        campaign = self.campaigns.find_one({'_id': date}, {'total': 1, 'offset': 1}) or {}
        return {'campaign_date': date, 'status': status, 'counts': counts,
                'total': campaign.get('total', 0), 'initialized': campaign.get('offset', 0)}
