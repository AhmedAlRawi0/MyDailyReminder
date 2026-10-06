"""Integration tests use an ephemeral local mongod; no production DB or SMTP."""
import copy
import importlib
import os
from pathlib import Path
import shutil
import smtplib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

# Override every production credential before config/dotenv is imported.
os.environ.update(MONGO_URI='mongodb://127.0.0.1:1', MONGO_HADEETH_DB_NAME='test_hadith',
                  MONGO_QURAAN_DB_NAME='test_verse', MONGO_SUBSCRIBERS_DB_NAME='test_subscribers',
                  EMAIL_ADDRESS='test@example.invalid', EMAIL_PASSWORD='test-only',
                  EMAIL_JOB_START_DATE='')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient
from models.budget import Budget
from models.campaigns import CampaignSender, campaign_date
from models.daily_content import DailyContent
from models.email import DeliveryResult, MailSession
from models.hadeeth import render_reminder

NOW = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)
HADITH = {'hadeeth': 'Hadith', 'explanation': 'Explanation',
          'hadeeth_ar': 'حديث', 'explanation_ar': 'شرح'}
VERSE = {'result': {'arabic_text': 'آية', 'translation': 'Verse', 'sura': 1, 'aya': 1}}
CONTENT = {'hadith': {'en': HADITH, 'fr': HADITH}, 'verse': {'en': VERSE, 'fr': VERSE}}


def setUpModule():
    global mongo_process, mongo_directory, mongo_client
    if not shutil.which('mongod'):
        raise unittest.SkipTest('Install MongoDB locally to run isolated integration tests')
    mongo_directory = tempfile.TemporaryDirectory(prefix='reminder-tests-')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    mongo_process = subprocess.Popen([
        'mongod', '--dbpath', mongo_directory.name, '--bind_ip', '127.0.0.1',
        '--port', str(port), '--logpath', str(Path(mongo_directory.name) / 'mongo.log'),
        '--nounixsocket', '--quiet',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mongo_client = MongoClient(f'mongodb://127.0.0.1:{port}', serverSelectionTimeoutMS=300, timeoutMS=2000)
    for _ in range(40):
        try:
            mongo_client.admin.command('ping')
            return
        except Exception:
            if mongo_process.poll() is not None:
                break
            time.sleep(.1)
    tearDownModule()
    raise RuntimeError('Could not start isolated local MongoDB')


def tearDownModule():
    mongo_client.close()
    mongo_process.terminate()
    mongo_process.wait(timeout=10)
    mongo_directory.cleanup()


class FakeContent:
    def prepare(self, kind, date, budget):
        return copy.deepcopy(CONTENT[kind])


class FakeMail:
    def __init__(self):
        self.sent = []
        self.sessions = 0
        self.result = DeliveryResult('sent')
        self.effect = None

    def __call__(self, budget):
        self.sessions += 1
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def send(self, *args):
        self.sent.append(args)
        if self.effect:
            return self.effect(*args)
        return self.result


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.db = mongo_client['reminder_test_' + uuid4().hex]
        self.now = NOW
        self.mail = FakeMail()
        self.sender = CampaignSender(self.db, self.db.subscribers, FakeContent(),
                                     self.mail, lambda: self.now)

    def tearDown(self):
        mongo_client.drop_database(self.db.name)

    def subscribers(self, count):
        if count:
            self.db.subscribers.insert_many([{'email': f'user{i}@example.invalid'} for i in range(count)])

    def run_batch(self, budget=None):
        return self.sender.run(budget or Budget())

    def test_batches_resume_and_reuse_connection(self):
        self.subscribers(12)
        self.assertEqual(self.run_batch()['counts']['sent'], 5)
        self.assertEqual(self.run_batch()['counts']['sent'], 10)
        result = self.run_batch()
        self.assertEqual(result['counts']['sent'], 12)
        self.assertEqual(result['status'], 'complete')
        self.run_batch()
        self.assertEqual(len(self.mail.sent), 12)
        self.assertEqual(self.mail.sessions, 3)

    def test_freezes_audience_and_content(self):
        self.subscribers(6)
        self.run_batch()
        self.db.subscribers.insert_one({'email': 'late@example.invalid'})
        self.sender.content = MagicMock()
        self.sender.content.prepare.side_effect = AssertionError('Must use frozen content')
        self.run_batch()
        self.assertEqual(len(self.mail.sent), 6)
        self.assertEqual(self.mail.sent[0][2].replace('user0', 'user5'), self.mail.sent[5][2])

    def test_unsubscribed_and_resubscribed_address_is_skipped(self):
        self.subscribers(6)
        self.run_batch()
        self.db.subscribers.delete_one({'email': 'user5@example.invalid'})
        self.db.subscribers.insert_one({'email': 'user5@example.invalid'})
        result = self.run_batch()
        self.assertEqual(result['counts']['unsubscribed'], 1)
        self.assertEqual(len(self.mail.sent), 5)

    def test_empty_audience_completes_without_smtp(self):
        self.assertEqual(self.run_batch()['status'], 'complete')
        self.assertEqual(self.mail.sessions, 0)

    def test_overlap_cannot_send_twice(self):
        self.subscribers(6)
        started, release = threading.Event(), threading.Event()
        errors = []
        def hold(*args):
            started.set()
            release.wait(4)
            return DeliveryResult('sent')
        self.mail.effect = hold
        def run():
            try:
                self.run_batch()
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(started.wait(4))
            self.assertEqual(self.run_batch()['status'], 'active')
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(errors)
        self.assertEqual(len(self.mail.sent), 5)

    def test_interrupted_submission_becomes_uncertain(self):
        self.subscribers(1)
        self.mail.effect = lambda *args: (_ for _ in ()).throw(RuntimeError('process interrupted'))
        with self.assertRaises(RuntimeError):
            self.run_batch()
        self.mail.effect = None
        result = self.run_batch()
        self.assertEqual(result['counts']['uncertain'], 1)
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(len(self.mail.sent), 1)

    def test_dead_process_lease_must_expire(self):
        self.subscribers(6)
        self.run_batch()
        self.db.email_campaigns.update_one({}, {'$set': {'owner': 'dead', 'lease_until': NOW + timedelta(seconds=30)}})
        self.assertEqual(self.run_batch()['status'], 'active')
        self.now += timedelta(seconds=31)
        self.assertEqual(self.run_batch()['counts']['sent'], 6)

    def test_stale_recipient_selection_cannot_claim_a_completed_delivery(self):
        self.subscribers(1)
        def concurrent_completion(*args):
            self.db.email_deliveries.update_one({}, {'$set': {'status': 'sent', 'attempts': 1}})
            return render_reminder(*args)
        with patch('models.campaigns.render_reminder', side_effect=concurrent_completion):
            self.assertEqual(self.run_batch()['counts']['sent'], 1)
        self.assertEqual(len(self.mail.sent), 0)

    def test_transient_failures_retry_three_times_with_backoff(self):
        self.subscribers(1)
        self.mail.result = DeliveryResult('retryable_failure', '451')
        for attempt in range(1, 5):
            result = self.run_batch()
            self.assertEqual(len(self.mail.sent), attempt)
            self.run_batch()
            self.assertEqual(len(self.mail.sent), attempt)
            self.now += timedelta(minutes=2 ** attempt)
        self.assertEqual(result['counts']['permanent_failure'], 1)
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(self.db.email_deliveries.find_one()['attempts'], 4)

    def test_permanent_and_uncertain_are_not_retried(self):
        self.subscribers(2)
        self.mail.result = DeliveryResult('permanent_failure', '550')
        self.run_batch()
        self.mail.result = DeliveryResult('uncertain', 'connection_interrupted')
        self.run_batch()
        self.run_batch()
        self.assertEqual(len(self.mail.sent), 2)

    def test_next_10am_expires_pending_and_preserves_uncertain(self):
        self.subscribers(7)
        self.run_batch()
        self.db.email_deliveries.update_one({'email': 'user6@example.invalid'}, {'$set': {'status': 'sending'}})
        self.now += timedelta(days=1)
        self.run_batch()
        old = list(self.db.email_deliveries.find({'campaign': '2026-10-05'}))
        self.assertEqual(sum(row['status'] == 'expired' for row in old), 1)
        self.assertEqual(sum(row['status'] == 'uncertain' for row in old), 1)
        self.assertEqual(self.db.email_campaigns.find_one({'_id': '2026-10-05'})['status'], 'expired')

    def test_before_10am_does_not_create_campaign_but_resumes_existing(self):
        self.now = NOW - timedelta(minutes=1)
        self.subscribers(6)
        self.assertEqual(self.run_batch()['status'], 'not_due')
        self.now = NOW
        self.run_batch()
        self.now = NOW + timedelta(hours=23)
        self.assertEqual(self.run_batch()['counts']['sent'], 6)

    def test_budget_stops_new_submissions(self):
        self.subscribers(10)
        elapsed = [0]
        budget = Budget(clock=lambda: elapsed[0])
        def slow(*args):
            elapsed[0] += 4
            return DeliveryResult('sent')
        self.mail.effect = slow
        result = self.run_batch(budget)
        self.assertEqual(len(self.mail.sent), 2)
        self.assertEqual(result['counts']['pending'], 8)
        self.assertEqual(budget.remaining(), 2)

    def test_elapsed_time_with_delayed_delivery_stays_under_budget(self):
        self.subscribers(10)
        def slow(*args):
            time.sleep(2.5)
            return DeliveryResult('sent')
        self.mail.effect = slow
        start = time.monotonic()
        result = self.run_batch()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 10)
        self.assertGreaterEqual(elapsed, 5)
        self.assertLessEqual(result['counts']['sent'], 3)

    def test_failure_to_record_acceptance_does_not_resend(self):
        self.subscribers(1)
        original = self.sender.deliveries.update_one
        def fail_on_success(query, update, **kwargs):
            if update.get('$set', {}).get('status') == 'sent':
                raise RuntimeError('database unavailable after SMTP accepted')
            return original(query, update, **kwargs)
        with patch.object(self.sender.deliveries, 'update_one', side_effect=fail_on_success):
            with self.assertRaises(RuntimeError):
                self.run_batch()
        self.assertEqual(self.run_batch()['counts']['uncertain'], 1)
        self.assertEqual(len(self.mail.sent), 1)

    def test_recipient_initialization_resumes_after_partial_write(self):
        self.subscribers(205)
        original = self.sender.deliveries.bulk_write
        calls = [0]
        def interrupted(*args, **kwargs):
            calls[0] += 1
            result = original(*args, **kwargs)
            if calls[0] == 1:
                raise RuntimeError('crash before offset checkpoint')
            return result
        with patch.object(self.sender.deliveries, 'bulk_write', side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.run_batch()
        result = self.run_batch()
        self.assertEqual(result['initialized'], 205)
        self.assertEqual(self.db.email_deliveries.count_documents({}), 205)

    def test_smtp_auth_failure_keeps_recipients_pending(self):
        self.subscribers(1)
        self.sender.mail_factory = MagicMock(side_effect=smtplib.SMTPAuthenticationError(535, b'private'))
        with self.assertRaises(smtplib.SMTPAuthenticationError):
            self.run_batch()
        self.assertEqual(self.db.email_deliveries.find_one()['status'], 'pending')

    def test_progress_logs_do_not_include_recipients(self):
        self.subscribers(1)
        with self.assertLogs('models.campaigns', level='INFO') as logs:
            self.run_batch()
        self.assertNotIn('example.invalid', ''.join(logs.output))


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.db = mongo_client['reminder_test_' + uuid4().hex]
        self.calls = []
        self.fetch = MagicMock(side_effect=self.answer)
        self.content = DailyContent(self.db.content, self.db.hadith, self.db.verse, self.fetch)

    def tearDown(self):
        mongo_client.drop_database(self.db.name)

    def answer(self, url, budget):
        self.calls.append(url)
        if 'quranenc' in url:
            return copy.deepcopy(VERSE)
        if 'language=ar' in url:
            return {'reference': 'Arabic reference'}
        return copy.deepcopy(HADITH)

    def test_shared_preparation_publishes_once_without_email_state(self):
        en = self.content.prepare('hadith', '2026-10-05', Budget())
        again = self.content.prepare('hadith', '2026-10-05', Budget())
        self.assertEqual(en, again)
        self.assertEqual(self.fetch.call_count, 3)
        state = self.db.hadith.find_one()
        self.assertEqual(state['current_index'], 1)
        self.assertNotIn('last_updated_syd', state)
        self.assertEqual(self.db.email_campaigns.count_documents({}), 0)

    def test_each_translation_is_checkpointed(self):
        elapsed = [0]
        def slow(url, budget):
            elapsed[0] += 7
            return self.answer(url, budget)
        self.content.fetch = slow
        self.assertIsNone(self.content.prepare('hadith', '2026-10-05', Budget(clock=lambda: elapsed[0])))
        self.assertIn('en', self.db.content.find_one()['parts'])
        self.content.fetch = self.fetch
        self.assertIsNotNone(self.content.prepare('hadith', '2026-10-05', Budget()))
        self.assertEqual(len(self.calls), 3)

    def test_missing_hadith_skips_to_next_and_persists_progress(self):
        responses = [None, copy.deepcopy(HADITH), copy.deepcopy(HADITH), {'reference': 'ref'}]
        self.content.fetch = lambda url, budget: responses.pop(0)
        self.assertIsNotNone(self.content.prepare('hadith', '2026-10-05', Budget()))
        self.assertEqual(self.db.hadith.find_one()['current_index'], 2)

    def test_legacy_today_content_is_reused(self):
        self.db.hadith.insert_one({'last_updated': '2026-10-05', 'current_index': 50,
                                  'last_hadeeth': HADITH, 'last_hadeeth_fr': HADITH})
        self.content.prepare('hadith', '2026-10-05', Budget())
        self.fetch.assert_not_called()
        self.assertEqual(self.db.hadith.find_one()['current_index'], 50)

    def test_old_preparation_cannot_overwrite_newer_state(self):
        self.content._seed('hadith', '2026-10-05')
        self.db.hadith.insert_one({'last_updated': '2026-10-06', 'current_index': 70})
        self.content.prepare('hadith', '2026-10-05', Budget())
        self.assertEqual(self.db.hadith.find_one()['current_index'], 70)
        self.assertIsNone(self.content.prepare('hadith', '2026-10-04', Budget()))

    def test_verse_advances_once(self):
        self.content.prepare('verse', '2026-10-05', Budget())
        self.content.prepare('verse', '2026-10-05', Budget())
        self.assertEqual(self.db.verse.find_one()['current_verse'], 2)
        self.assertEqual(self.fetch.call_count, 2)

    def test_content_lease_prevents_overlapping_preparation(self):
        started, release = threading.Event(), threading.Event()
        errors = []
        def hold(url, budget):
            started.set()
            release.wait(4)
            return self.answer(url, budget)
        self.content.fetch = hold
        def run():
            try:
                self.content.prepare('hadith', '2026-10-05', Budget())
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(started.wait(4))
            self.assertIsNone(self.content.prepare('hadith', '2026-10-05', Budget()))
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(errors)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.db.hadith.find_one()['current_index'], 1)


class BoundaryAndSMTPTests(unittest.TestCase):
    def test_mail_session_preserves_hostname_for_verified_starttls(self):
        session = MailSession(Budget())
        context = MagicMock()
        def wrap_socket(sock, *, server_hostname):
            if not server_hostname:
                raise ValueError('check_hostname requires server_hostname')
            return MagicMock()
        context.wrap_socket.side_effect = wrap_socket
        with patch('models.email.SMTP_SERVER', 'smtp.gmail.com'), \
             patch.object(smtplib.SMTP, 'connect', return_value=(220, b'ready')) as connect, \
             patch.object(session.server, 'ehlo_or_helo_if_needed'), \
             patch.object(session.server, 'has_extn', return_value=True), \
             patch.object(session.server, 'docmd', return_value=(220, b'ready')), \
             patch.object(session.server, 'login') as login, \
             patch('models.email.ssl.create_default_context', return_value=context):
            with session:
                pass
        connect.assert_called_once_with('smtp.gmail.com', 587, None)
        self.assertEqual(context.wrap_socket.call_args.kwargs['server_hostname'], 'smtp.gmail.com')
        login.assert_called_once()

    def test_toronto_dst_boundaries(self):
        for day, utc_hour in [('2026-03-07', 15), ('2026-03-08', 14), ('2026-11-01', 15)]:
            start = datetime.fromisoformat(day).replace(hour=utc_hour, tzinfo=timezone.utc)
            self.assertEqual(campaign_date(start), day)
            self.assertEqual(campaign_date(start - timedelta(seconds=1)), (start.date() - timedelta(days=1)).isoformat())

    def test_transport_classification(self):
        cases = [(smtplib.SMTPDataError(451, b'try later'), False, 'retryable_failure'),
                 (smtplib.SMTPDataError(550, b'no'), True, 'permanent_failure'),
                 (smtplib.SMTPRecipientsRefused({'x': (550, b'no')}), False, 'permanent_failure'),
                 (smtplib.SMTPServerDisconnected(), True, 'uncertain'),
                 (TimeoutError(), False, 'retryable_failure')]
        for exception, submitting, expected in cases:
            with self.subTest(expected=expected, error=type(exception).__name__):
                session = MailSession(Budget())
                session.server = MagicMock()
                def fail(*args):
                    session.server.submitting = submitting
                    raise exception
                session.server.sendmail.side_effect = fail
                result = session.send('x@example.invalid', 'Subject', '<p>Body</p>', '<test@invalid>')
                self.assertEqual(result.status, expected)

    def test_smtp_io_timeout_respects_remaining_budget(self):
        elapsed = [0]
        budget = Budget(clock=lambda: elapsed[0])
        session = MailSession(budget)
        session.server.sock = MagicMock()
        session.server._set_timeout()
        session.server.sock.settimeout.assert_called_with(2)
        elapsed[0] = 7
        session.server._set_timeout()
        session.server.sock.settimeout.assert_called_with(1)

    def test_unsubscribe_url_encodes_email(self):
        html = render_reminder(CONTENT, 'a+b@example.invalid', '2026-10-05')
        self.assertIn('a%2Bb%40example.invalid', html)


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # App uses this isolated client even though production enables TLS.
        with patch('pymongo.MongoClient', return_value=mongo_client):
            cls.module = importlib.import_module('app')
        cls.client = cls.module.app.test_client()

    def test_cutover_date_prevents_same_day_duplicates(self):
        with patch.dict(os.environ, {'EMAIL_JOB_START_DATE': '9999-01-01'}):
            with patch.object(self.module.sender, 'run') as run:
                response = self.client.get('/send-email')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['status'], 'not_due')
                run.assert_not_called()

    def test_success_and_sanitized_failure(self):
        with patch.object(self.module.sender, 'run', return_value={'status': 'running', 'counts': {'sent': 5}}):
            response = self.client.get('/send-email')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with patch.object(self.module.sender, 'run', side_effect=RuntimeError('private@example.invalid')):
            with self.assertLogs('app', level='ERROR') as logs:
                response = self.client.get('/send-email')
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private@example.invalid', ''.join(logs.output) + response.get_data(as_text=True))

    def test_daily_endpoints_share_preparer_without_triggering_email(self):
        with patch.object(self.module.content, 'prepare', return_value={'en': {'text': 'English'}, 'fr': {'text': 'French'}}) as prepare:
            with patch.object(self.module.sender, 'run') as send:
                self.assertEqual(self.client.get('/daily-hadeeth?Language=French').json, {'text': 'French'})
                self.assertEqual(self.client.get('/daily-verse').json, {'text': 'English'})
                self.assertEqual(prepare.call_count, 2)
                send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
