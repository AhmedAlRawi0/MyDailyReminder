"""Resumable content preparation shared by public endpoints and campaigns."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import requests
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from hadith_ids import get_ids_list
from models.budget import BudgetExpired

ROOT = Path(__file__).resolve().parents[1]
HADITH_IDS = get_ids_list(ROOT / 'data/Hadeeths.xlsx')
with (ROOT / 'quran.json').open() as file:
    VERSE_COUNTS = json.load(file)


def fetch_json(url, budget):
    # Stream the body so repeated reads also respect the cooperative deadline.
    with requests.get(url, timeout=(budget.io_timeout(), budget.io_timeout()), stream=True) as response:
        if response.status_code == 404:
            return None
        response.raise_for_status()
        chunks = []
        size = 0
        for chunk in response.iter_content(8192):
            budget.io_timeout()
            size += len(chunk)
            if size > 2_000_000:
                raise ValueError('Content response too large')
            chunks.append(chunk)
        return json.loads(b''.join(chunks))


class DailyContent:
    def __init__(self, documents, hadith_state, verse_state, fetch=fetch_json):
        self.documents = documents
        self.states = {'hadith': hadith_state, 'verse': verse_state}
        self.fetch = fetch

    def _seed(self, kind, date):
        state = self.states[kind].find_one() or {}
        # Never reconstruct yesterday's reminder from tomorrow's sequence state.
        if state.get('last_updated', '') > date:
            return None
        doc = {'_id': f'{kind}:{date}', 'date': date, 'kind': kind,
               'parts': {}, 'ready': False, 'attempts': 0}
        if kind == 'hadith':
            doc['index'] = state.get('current_index', 0) % len(HADITH_IDS)
            en, fr = state.get('last_hadeeth'), state.get('last_hadeeth_fr')
        else:
            doc['surah'] = state.get('current_surah', 1)
            doc['verse'] = state.get('current_verse', 1)
            en, fr = state.get('last_verse'), state.get('last_verse_fr')
        if state.get('last_updated') == date and en and fr:
            doc.update(parts={'en': en, 'fr': fr}, ready=True)
        try:
            self.documents.insert_one(doc)
        except DuplicateKeyError:
            pass
        return self.documents.find_one({'_id': doc['_id']})

    def prepare(self, kind, date, budget):
        key = f'{kind}:{date}'
        doc = self.documents.find_one({'_id': key}) or self._seed(kind, date)
        if not doc:
            return None
        if doc['ready']:
            return doc['parts']
        if not budget.can_work():
            return None
        owner = uuid4().hex
        now = datetime.now(timezone.utc)
        doc = self.documents.find_one_and_update(
            {'_id': key, '$or': [{'lease_until': {'$exists': False}}, {'lease_until': {'$lte': now}}]},
            {'$set': {'owner': owner, 'lease_until': now + timedelta(seconds=30)}},
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            return None
        owned = {'_id': key, 'owner': owner}
        try:
            while budget.can_work() and not doc['ready']:
                if kind == 'hadith' and doc['attempts'] >= len(HADITH_IDS):
                    raise ValueError('No usable hadith found')
                languages = ('en', 'fr', 'ar') if kind == 'hadith' else ('en', 'fr')
                lang = next((language for language in languages if language not in doc['parts']), None)
                if lang:
                    if kind == 'hadith':
                        url = f'https://hadeethenc.com/api/v1/hadeeths/one/?id={HADITH_IDS[doc["index"]]}&language={lang}'
                    else:
                        translation = 'english_rwwad' if lang == 'en' else 'french_montada'
                        url = f'https://quranenc.com/api/v1/translation/aya/{translation}/{doc["surah"]}/{doc["verse"]}'
                    data = self.fetch(url, budget)
                    if not self._valid(kind, lang, data):
                        if kind == 'verse':
                            raise ValueError('Invalid verse content')
                        doc.update(index=(doc['index'] + 1) % len(HADITH_IDS),
                                   attempts=doc['attempts'] + 1, parts={})
                    else:
                        doc['parts'][lang] = data
                    saved = self.documents.update_one(owned, {'$set': {
                        k: doc[k] for k in ('parts', 'attempts', 'index') if k in doc
                    }})
                    if not saved.matched_count:
                        return None
                if all(language in doc['parts'] for language in languages):
                    if not self.documents.find_one({**owned, 'lease_until': {'$gt': datetime.now(timezone.utc)}}):
                        return None
                    if kind == 'hadith':
                        for language in ('en', 'fr'):
                            doc['parts'][language]['reference'] = doc['parts']['ar'].get('reference')
                    # Publish the sequence before ready: a crash can safely repeat this CAS.
                    self._publish(kind, date, doc)
                    saved = self.documents.update_one(owned, {'$set': {'ready': True, 'parts': doc['parts']}})
                    if not saved.matched_count:
                        return None
                    doc['ready'] = True
            return doc['parts'] if doc['ready'] else None
        except BudgetExpired:
            return None
        finally:
            self.documents.update_one(owned, {'$unset': {'owner': '', 'lease_until': ''}})

    @staticmethod
    def _valid(kind, lang, data):
        if not isinstance(data, dict):
            return False
        if kind == 'hadith':
            fields = ('reference',) if lang == 'ar' else ('hadeeth', 'explanation', 'hadeeth_ar', 'explanation_ar')
            return all(field in data for field in fields)
        result = data.get('result')
        return isinstance(result, dict) and all(field in result for field in ('arabic_text', 'translation', 'sura', 'aya'))

    def _publish(self, kind, date, doc):
        state = self.states[kind]
        current = state.find_one()
        if not current:
            try:
                state.insert_one({'_id': 'daily-state', 'last_updated': '1970-01-01'})
            except DuplicateKeyError:
                pass
            current = state.find_one()
        if kind == 'hadith':
            values = {'current_index': (doc['index'] + 1) % len(HADITH_IDS),
                      'last_hadeeth': doc['parts']['en'], 'last_hadeeth_fr': doc['parts']['fr']}
        else:
            surah, verse = doc['surah'], doc['verse']
            if verse < VERSE_COUNTS[str(surah)]:
                verse += 1
            else:
                surah, verse = (surah + 1 if surah < 114 else 1), 1
            values = {'current_surah': surah, 'current_verse': verse,
                      'last_verse': doc['parts']['en'], 'last_verse_fr': doc['parts']['fr']}
        values['last_updated'] = date
        state.update_one({'_id': current['_id'], '$or': [
            {'last_updated': {'$lt': date}}, {'last_updated': {'$exists': False}}
        ]}, {'$set': values})
        # If another preparer already published this date, its content wins.
        published = state.find_one({'_id': current['_id'], 'last_updated': date})
        if published:
            en_key, fr_key = ('last_hadeeth', 'last_hadeeth_fr') if kind == 'hadith' else ('last_verse', 'last_verse_fr')
            doc['parts']['en'], doc['parts']['fr'] = published[en_key], published[fr_key]
