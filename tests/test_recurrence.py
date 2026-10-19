import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ritm.store import Store
from test_app import entry


def repeating(**changes):
    values = entry(title="Работа", day="2026-10-19", starts_at="2026-10-19T15:00:00+09:00",
                   ends_at="2026-10-19T17:00:00+09:00", reminder=15,
                   recurrence=dict(frequency="weekly", weekdays=[0], timezone="Asia/Yakutsk", until=None))
    values.update(changes)
    return values


class RecurrenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.db')
        self.store.session('alice', 'Алиса')
        self.store.session('bob', 'Боб')

    def tearDown(self):
        self.temp.cleanup()

    def month(self, user='alice'):
        return self.store.entries(user, '2026-10-01', '2026-11-01')

    def test_mondays_keep_hours_and_completion_is_independent(self):
        first = self.store.save('alice', repeating())
        items = self.month()
        self.assertEqual([e['day'] for e in items], ['2026-10-19', '2026-10-26'])
        self.assertTrue(all(e['starts_at'].endswith('06:00:00+00:00') and e['duration'] == 120 for e in items))
        self.store.save('alice', {**first, 'done': True}, first['id'])
        self.assertEqual([e['done'] for e in self.month()], [1, 0])
        self.assertEqual(self.store.entries('bob', '2026-10-01', '2026-11-01'), [])
        self.assertIsNone(self.store.get('bob', first['id']))
        with self.assertRaises(LookupError):
            self.store.save('bob', {**first, 'scope': 'future'}, first['id'])
        self.assertFalse(self.store.delete('bob', first['id'], 'future'))
        self.assertEqual(len(self.month()), 2)

    def test_daily_weekdays_until_and_far_future(self):
        for frequency, days, count in [('daily', [], 7), ('weekly', [0,1,2,3,4], 5)]:
            with self.subTest(frequency=frequency):
                first = self.store.save('alice', repeating(recurrence=dict(frequency=frequency, weekdays=days,
                    timezone='Asia/Yakutsk', until='2026-10-25')))
                items = [e for e in self.month() if e['series_id'] == first['series_id']]
                self.assertEqual(len(items), count)
        self.store.save('bob', repeating())
        future = self.store.entries('bob', '2031-01-01', '2031-01-31')
        self.assertEqual(len(future), 4)

    def test_first_matching_day_and_empty_period(self):
        first = self.store.save('alice', repeating(day='2026-10-18', starts_at='2026-10-18T06:00:00Z', ends_at='2026-10-18T08:00:00Z'))
        self.assertEqual(first['day'], '2026-10-19')
        with self.assertRaises(ValueError):
            self.store.save('bob', repeating(day='2026-10-18', starts_at='2026-10-18T06:00:00Z', ends_at=None,
                recurrence=dict(frequency='weekly', weekdays=[0], timezone='Asia/Yakutsk', until='2026-10-18')))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM series WHERE user_id='bob'").fetchone()[0], 0)

    def test_deleted_occurrence_stays_deleted_and_override_moves_once(self):
        first = self.store.save('alice', repeating())
        self.assertTrue(self.store.delete('alice', first['id']))
        self.assertIsNone(Store(self.store.path).get('alice', first['id']))
        self.assertEqual([e['day'] for e in self.month()], ['2026-10-26'])
        next_item = self.month()[0]
        self.store.save('alice', {**next_item, 'title':'Разовая замена', 'day':'2026-10-27',
            'starts_at':'2026-10-27T07:00:00Z', 'ends_at':'2026-10-27T08:00:00Z'}, next_item['id'])
        self.assertEqual([(e['day'],e['title']) for e in self.month()], [('2026-10-27','Разовая замена')])

    def test_future_edit_and_stop_preserve_past_history(self):
        first = self.store.save('alice', repeating())
        self.store.save('alice', {**first, 'done':True}, first['id'])
        next_item = self.month()[1]
        replacement = self.store.save('alice', {**next_item, 'scope':'future', 'title':'Новая работа',
            'starts_at':'2026-10-26T07:00:00Z','ends_at':'2026-10-26T09:00:00Z'}, next_item['id'])
        items = self.month()
        self.assertEqual([(e['title'],e['done']) for e in items], [('Работа',1),('Новая работа',0)])
        self.store.delete('alice', replacement['id'], 'future')
        self.assertEqual([(e['day'],e['done']) for e in self.month()], [('2026-10-19',1)])
        self.assertEqual(self.store.entries('alice','2030-01-01','2030-01-31')[0]['day'], '2026-10-19')

    def test_invalid_future_change_is_atomic(self):
        first = self.store.save('alice', repeating())
        with self.assertRaises(ValueError):
            self.store.save('alice', {**first, 'scope':'future', 'recurrence':{'frequency':'weekly','weekdays':[]}}, first['id'])
        self.assertEqual(len(self.month()), 2)

    def test_existing_task_can_become_series_and_untimed_repeat(self):
        old = self.store.save('alice', entry())
        result = self.store.save('alice', {**old, 'recurrence':dict(frequency='daily',weekdays=[],timezone='UTC',until='2026-10-20')}, old['id'])
        self.assertIsNone(self.store.get('alice', old['id']))
        self.assertEqual(len(self.month()), 3)
        self.assertIsNone(result['starts_at'])

    def test_dst_keeps_wall_clock_and_skips_nonexistent_times(self):
        first = self.store.save('alice', repeating(day='2026-10-26', starts_at='2026-10-26T15:00:00-04:00',
            ends_at='2026-10-26T17:00:00-04:00', recurrence=dict(frequency='weekly',weekdays=[0],timezone='America/New_York')))
        items = self.store.entries('alice','2026-10-26','2026-11-03')
        self.assertEqual([e['starts_at'] for e in items], ['2026-10-26T19:00:00+00:00','2026-11-02T20:00:00+00:00'])
        self.store.save('bob', repeating(day='2027-03-13',starts_at='2027-03-13T02:30:00-05:00', ends_at=None,
            recurrence=dict(frequency='daily',weekdays=[],timezone='America/New_York',until='2027-03-15')))
        self.assertEqual([e['day'] for e in self.store.entries('bob','2027-03-13','2027-03-15')], ['2027-03-13','2027-03-15'])

    def test_reminders_generate_without_open_page_and_do_not_resurrect_skip(self):
        self.store.link_chat(42,'Серафим')
        first = self.store.save('tg:42', repeating())
        self.store.delete('tg:42', first['id'])
        now = datetime(2026,10,26,5,45,tzinfo=timezone.utc).timestamp()
        pending = self.store.pending(now)
        self.assertEqual([e['day'] for e in pending], ['2026-10-26'])
        self.store.mark_notification(pending[0]['id'],True,now)
        self.assertEqual(self.store.pending(now+10), [])

    def test_validation_range_rules_notes_and_user_supplied_ids(self):
        for rule in [dict(frequency='weekly',weekdays=[]),dict(frequency='weekly',weekdays=[True]),
                     dict(frequency='monthly'),dict(frequency='daily',timezone='../bad'),
                     dict(frequency='daily',timezone='UTC',until='2025-01-01')]:
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                self.store.save('alice', repeating(recurrence=rule))
        with self.assertRaises(ValueError):
            self.store.save('alice', repeating(kind='note'))
        with self.assertRaises(ValueError):
            self.store.entries('alice','2026-01-01','2028-01-01')
        first = self.store.save('alice', repeating())
        forged = self.store.save('bob', entry(id=first['id'],series_id=first['series_id']))
        self.assertIsNone(forged['series_id'])
