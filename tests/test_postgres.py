import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from migrate_to_postgres import migrate
from ritm.database import open_store
from ritm.hosting import DeliveryState, Settings, create_application, load_settings
from ritm.postgres import PostgresStore
from ritm.store import Store
from test_app import entry


class CloudSelectionTests(unittest.TestCase):
    def test_render_cannot_fall_back_to_an_ephemeral_database(self):
        settings = Settings('https://ser.example',Path('unused.sqlite3'),'fake','w'*40,'c'*40)
        with self.assertRaises(ValueError):
            open_store(settings,{'RENDER':'true'})

    def test_render_url_is_used_only_when_public_url_is_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            env = dict(BOT_TOKEN='fake',WEBHOOK_SECRET='w'*40,CRON_SECRET='c'*40,
                       RENDER_EXTERNAL_URL='https://ser.onrender.com')
            self.assertEqual(load_settings(folder,env).public_url,env['RENDER_EXTERNAL_URL'])
            self.assertEqual(load_settings(folder,{**env,'PUBLIC_URL':'https://own.example'}).public_url,'https://own.example')

    def test_invalid_postgres_address_and_schema_are_rejected(self):
        for dsn in ('https://wrong.example','postgresql://'):
            with self.assertRaises(ValueError):
                PostgresStore(dsn)
        with self.assertRaises(ValueError):
            PostgresStore('postgresql://localhost/test',schema='bad;DROP SCHEMA public')


@unittest.skipUnless(os.environ.get('RITM_TEST_DATABASE_URL'),'PostgreSQL integration requires a separate test database')
class PostgresTests(unittest.TestCase):
    def setUp(self):
        self.dsn=os.environ['RITM_TEST_DATABASE_URL']
        self.schema='ritm_test_'+uuid.uuid4().hex
        self.store=PostgresStore(self.dsn,self.schema)
        self.store.session('alice','Алиса')
        self.store.session('bob','Боб')

    def tearDown(self):
        from psycopg import sql
        assert self.schema.startswith('ritm_test_') and len(self.schema)==42
        with self.store._connection() as connection:
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def test_crud_sessions_and_owner_isolation(self):
        token=self.store.session('alice','Алиса')
        self.assertEqual(self.store.user(token)['id'],'alice')
        saved=self.store.save('alice',entry())
        self.assertEqual(self.store.entries('bob'),[])
        self.assertIsNone(self.store.get('bob',saved['id']))
        with self.assertRaises(LookupError):
            self.store.save('bob',entry(),saved['id'])
        self.assertFalse(self.store.delete('bob',saved['id']))
        self.assertTrue(self.store.save('alice',{**saved,'done':True},saved['id'])['done'])
        self.assertEqual(PostgresStore(self.dsn,self.schema).get('alice',saved['id'])['title'],saved['title'])
        self.assertTrue(self.store.delete('alice',saved['id']))

    def test_repeating_hours_completion_and_future_changes(self):
        rule=dict(frequency='weekly',weekdays=[0],timezone='Asia/Yakutsk',until=None)
        first=self.store.save('alice',entry(title='Работа',day='2026-10-19',
            starts_at='2026-10-19T15:00:00+09:00',ends_at='2026-10-19T17:00:00+09:00',recurrence=rule))
        self.assertEqual([e['day'] for e in self.store.entries('alice','2026-10-01','2026-11-01')],['2026-10-19','2026-10-26'])
        self.store.save('alice',{**first,'done':True},first['id'])
        self.assertEqual([e['done'] for e in self.store.entries('alice','2026-10-01','2026-11-01')],[1,0])
        later=self.store.entries('alice','2026-10-26','2026-10-26')[0]
        self.store.save('alice',{**later,'title':'Новый график','scope':'future'},later['id'])
        self.assertEqual(self.store.get('alice',first['id'])['title'],'Работа')
        self.assertEqual(self.store.entries('alice','2026-11-01','2026-11-30')[0]['title'],'Новый график')

    def test_delivery_leases_survive_connections_and_failed_transactions_roll_back(self):
        state=DeliveryState(self.store)
        self.assertEqual(state.claim_update(123),'claimed')
        self.assertEqual(DeliveryState(self.store).claim_update(123),'busy')
        state.finish_update(123,True)
        self.assertEqual(state.claim_update(123),'done')
        owner=state.claim_job()
        self.assertIsNotNone(owner)
        self.assertIsNone(DeliveryState(self.store).claim_job())
        state.release_job('not-owner')
        self.assertIsNone(state.claim_job())
        state.release_job(owner)
        self.assertIsNotNone(state.claim_job())
        with self.assertRaises(RuntimeError):
            with self.store.connect() as db:
                db.execute("UPDATE users SET name='Changed' WHERE id='alice'")
                db.execute('INSERT INTO users(id,name) VALUES(?,?)',('alice','duplicate'))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT name FROM users WHERE id='alice'").fetchone()['name'],'Алиса')

    def test_transfer_keeps_records_and_refuses_a_second_import(self):
        # Import requires an empty target, including users.
        with self.store.connect() as db:
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM users')
        with tempfile.TemporaryDirectory() as folder:
            local=Store(Path(folder)/'local.sqlite3')
            local.session('alice','Алиса')
            saved=local.save('alice',entry())
            counts=migrate(local.path,self.store)
            self.assertEqual(counts,{'users':1,'series':0,'entries':1})
            self.assertEqual(self.store.get('alice',saved['id'])['title'],saved['title'])
            with self.store.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM sessions').fetchone()['n'],0)
            with self.assertRaises(ValueError):
                migrate(local.path,self.store)
            self.assertEqual(len(self.store.entries('alice')),1)

    def test_transfer_error_rolls_back_previously_inserted_users(self):
        with self.store.connect() as db:
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM users')
        with tempfile.TemporaryDirectory() as folder:
            local=Store(Path(folder)/'local.sqlite3')
            local.session('alice','Алиса')
            local.save('alice',entry(title='Unsupported\x00text'))
            with self.assertRaises(RuntimeError):
                migrate(local.path,self.store)
            with self.store.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n'],0)

    def test_signed_wsgi_login_and_isolation(self):
        import json
        from wsgiref.validate import validator
        from test_app import signed_data
        from test_hosting import HostingTests
        bridge=HostingTests()
        settings=Settings('https://ser.example',Path('unused'),'test-token','w'*40,'c'*40)
        bridge.app=validator(create_application(self.store,settings,lambda *args: True))
        first=bridge.request('/api/session','POST',{'init_data':signed_data('test-token')},{'X-Ritm-Request':'1'})
        self.assertEqual(first['status'],200)
        self.assertIn('Secure',first['headers']['Set-Cookie'])
        cookie=first['headers']['Set-Cookie'].split(';')[0]
        self.assertEqual(bridge.request('/api/entries','POST',entry(),{'Cookie':cookie,'X-Ritm-Request':'1'})['status'],201)
        second=bridge.request('/api/session','POST',{'init_data':signed_data('test-token',user_id=55)},{'X-Ritm-Request':'1'})
        other=second['headers']['Set-Cookie'].split(';')[0]
        self.assertEqual(json.loads(bridge.request('/api/entries',headers={'Cookie':other})['body'])['entries'],[])
        self.assertEqual(len(json.loads(bridge.request('/api/entries',headers={'Cookie':cookie})['body'])['entries']),1)
