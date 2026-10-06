import logging
import os
from datetime import date as calendar_date, datetime, timezone

from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
import pymongo

from models.budget import Budget
from models.campaigns import CampaignSender, TORONTO, STATES, campaign_date
from models.daily_content import DailyContent
from models.database import (
    add_subscriber, remove_subscriber, hadeeth_collection, quraan_collection,
    subscribers_db, subscribers_collection,
)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
logger = logging.getLogger(__name__)
content = DailyContent(subscribers_db['daily_content'], hadeeth_collection, quraan_collection)
sender = CampaignSender(subscribers_db, subscribers_collection, content)


@app.route('/subscribe', methods=['POST'])
def subscribe():
    data = request.get_json() or {}
    email = data.get('email')
    if not isinstance(email, str) or not email or '\r' in email or '\n' in email:
        return jsonify({'message': 'Invalid email.'}), 400
    response = add_subscriber(email)
    return jsonify(response), 200 if 'Successfully' in response['message'] else 400


@app.route('/unsubscribe', methods=['GET'])
def unsubscribe():
    email = request.args.get('email')
    if not email:
        return jsonify({'message': 'Invalid email.'}), 400
    response = remove_subscriber(email)
    return jsonify(response), 200 if 'Successfully' in response['message'] else 400


@app.route('/send-email', methods=['GET'])
def sendEmail():
    budget = Budget()
    try:
        first_date = os.getenv('EMAIL_JOB_START_DATE', '')
        if first_date and campaign_date(datetime.now(timezone.utc)) < calendar_date.fromisoformat(first_date).isoformat():
            response = jsonify({'campaign_date': campaign_date(datetime.now(timezone.utc)),
                                'status': 'not_due', 'counts': dict.fromkeys(STATES, 0),
                                'total': 0, 'initialized': 0})
            response.headers['Cache-Control'] = 'no-store'
            return response, 200
        with pymongo.timeout(budget.remaining()):
            result = sender.run(budget)
        response = jsonify(result)
        response.headers['Cache-Control'] = 'no-store'
        return response, 200
    except Exception as exc:
        # Never log exception messages: SMTP and DB errors can contain recipients or credentials.
        logger.error('email_campaign infrastructure_failure type=%s', type(exc).__name__)
        response = jsonify({'campaign_date': campaign_date(datetime.now(timezone.utc)),
                            'status': 'infrastructure_failure', 'counts': None})
        response.headers['Cache-Control'] = 'no-store'
        return response, 503


def daily_response(kind):
    date = datetime.now(TORONTO).date().isoformat()
    budget = Budget()
    try:
        with pymongo.timeout(budget.remaining()):
            parts = content.prepare(kind, date, budget)
        if not parts:
            response = make_response(jsonify({'error': 'Daily content is being prepared. Please retry.'}), 503)
            response.headers['Retry-After'] = '5'
        else:
            language = 'fr' if request.args.get('Language') == 'French' else 'en'
            response = make_response(jsonify(parts[language]))
    except Exception as exc:
        logger.error('daily_content unavailable kind=%s type=%s', kind, type(exc).__name__)
        response = make_response(jsonify({'error': 'Daily content is temporarily unavailable.'}), 503)
        response.headers['Retry-After'] = '5'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/daily-hadeeth', methods=['GET'])
def daily_hadeeth():
    return daily_response('hadith')


@app.route('/daily-verse', methods=['GET'])
def daily_verse():
    return daily_response('verse')


if __name__ == '__main__':
    app.run(port=int(os.getenv('PORT', '8080')))
