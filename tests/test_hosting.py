import io
import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from wsgiref.validate import validator

from backup_database import backup
from deploy_pythonanywhere import activate
from ritm.hosting import DeliveryState, Settings, create_application, load_settings
from ritm.store import Store
from test_app import entry, signed_data


class HostingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = Settings('https://ser.example', self.root/'data.sqlite3', 'test-token', 'w'*40, 'c'*40)
        self.store = Store(self.settings.database_path)
        self.calls = []
        self.sender_error = False
        self.app = validator(create_application(self.store, self.settings, self.sender))

    def tearDown(self):
        self.temp.cleanup()

    def sender(self, token, method, payload):
        if self.sender_error:
            raise RuntimeError('temporary send error')
        self.calls.append((method, payload))
        return True

    def request(self, path='/', method='GET', payload=None, headers=None, raw=None, scheme='https'):
        data = raw if raw is not None else json.dumps(payload).encode() if payload is not None else b''
        environ = dict(REQUEST_METHOD=method, PATH_INFO=path, QUERY_STRING='', SERVER_NAME='ser.example',
                       SERVER_PORT='443', SERVER_PROTOCOL='HTTP/1.1', SCRIPT_NAME='', HTTP_HOST='ser.example',
                       CONTENT_LENGTH=str(len(data)), CONTENT_TYPE='application/json')
        environ.update({'wsgi.version': (1, 0), 'wsgi.url_scheme': scheme, 'wsgi.input': io.BytesIO(data),
                        'wsgi.errors': io.StringIO(), 'wsgi.multithread': False, 'wsgi.multiprocess': True,
                        'wsgi.run_once': False})
        for key, value in (headers or {}).items():
            environ['HTTP_'+key.upper().replace('-', '_')] = value
        captured = {}
        def start_response(status, values, exc_info=None):
            captured.update(status=int(status.split()[0]), headers=dict(values))
        result = self.app(environ, start_response)
        try:
            captured['body'] = b''.join(result)
        finally:
            result.close()
        return captured

    def webhook(self, update_id=1, text='/start', **kwargs):
        return self.request('/telegram/webhook', 'POST', {
            'update_id': update_id, 'message': {'text': text, 'chat': {'id': 42, 'type': 'private'},
                                             'from': {'id': 42, 'first_name': 'Серафим'}}},
            {'X-Telegram-Bot-Api-Secret-Token': self.settings.webhook_secret}, **kwargs)

    def test_wsgi_static_health_and_no_anonymous_login(self):
        self.assertIn(b'Ser', self.request()['body'])
        self.assertEqual(self.request('/api/health')['status'], 200)
        self.assertEqual(self.request('/api/entries')['status'], 401)
        self.assertEqual(self.request('/api/session', 'POST', {}, {'X-Ritm-Request': '1'})['status'], 401)

    def test_signed_session_cookie_crud_and_isolation_through_wsgi(self):
        first = self.request('/api/session', 'POST', {'init_data': signed_data('test-token')},
                             {'X-Ritm-Request': '1', 'Origin': 'https://ser.example'})
        self.assertEqual(first['status'], 200)
        self.assertIn('Secure', first['headers']['Set-Cookie'])
        cookie = first['headers']['Set-Cookie'].split(';')[0]
        saved = self.request('/api/entries', 'POST', entry(), {'Cookie': cookie, 'X-Ritm-Request': '1'})
        self.assertEqual(saved['status'], 201)
        own = self.request('/api/entries', headers={'Cookie': cookie})
        self.assertEqual(len(json.loads(own['body'])['entries']), 1)
        second = self.request('/api/session', 'POST', {'init_data': signed_data('test-token', user_id=55)},
                              {'X-Ritm-Request': '1'})
        other_cookie = second['headers']['Set-Cookie'].split(';')[0]
        self.assertEqual(json.loads(self.request('/api/entries', headers={'Cookie': other_cookie})['body'])['entries'], [])

    def test_wsgi_rejects_foreign_host_origin_and_http(self):
        self.assertEqual(self.request(headers={'Host': 'evil.example'})['status'], 403)
        self.assertEqual(self.request(scheme='http')['status'], 403)
        self.assertEqual(self.request('/api/session', 'POST', {},
                                     {'X-Ritm-Request': '1', 'Origin': 'https://evil.example'})['status'], 403)

    def test_internal_routes_require_post_and_distinct_secrets(self):
        for path in ('/telegram/webhook', '/internal/reminders'):
            self.assertEqual(self.request(path)['status'], 405)
            self.assertEqual(self.request(path, 'POST', {})['status'], 403)
            self.assertEqual(self.request(path, 'POST', {}, {'Authorization': 'Bearer '+self.settings.webhook_secret})['status'], 403)
        self.assertEqual(self.calls, [])

    def test_webhook_deduplicates_persistently_and_says_hosted(self):
        self.assertEqual(self.webhook()['status'], 200)
        self.assertEqual(len(self.calls), 1)
        self.app = validator(create_application(self.store, self.settings, self.sender))
        self.assertEqual(self.webhook()['status'], 200)
        self.assertEqual(len(self.calls), 1)
        self.webhook(2, '/help')
        self.assertIn('на сервере', self.calls[-1][1]['text'])
        self.assertNotIn('компьютера владельца', self.calls[-1][1]['text'])

    def test_failed_webhook_can_retry_without_repeating_success(self):
        self.sender_error = True
        self.assertEqual(self.webhook()['status'], 503)
        self.sender_error = False
        self.assertEqual(self.webhook()['status'], 200)
        self.assertEqual(len(self.calls), 1)

    def test_busy_update_retries_and_malformed_update_is_rejected(self):
        state = DeliveryState(self.store)
        self.assertEqual(state.claim_update(1), 'claimed')
        self.assertEqual(self.webhook()['status'], 503)
        for payload in ({'update_id': True}, {'update_id': 2, 'message': []}, {'update_id': 2, 'message': {'text': []}}):
            self.assertEqual(self.request('/telegram/webhook', 'POST', payload,
                                         {'X-Telegram-Bot-Api-Secret-Token': self.settings.webhook_secret})['status'], 400)
        self.assertEqual(self.request('/telegram/webhook', 'POST', raw=b'bad-json',
                                     headers={'X-Telegram-Bot-Api-Secret-Token': self.settings.webhook_secret})['status'], 400)

    def test_cron_is_bounded_idempotent_and_serialized(self):
        self.store.link_chat(42, 'Серафим')
        for index in range(3):
            task = self.store.save('tg:42', entry(title=f'Дело {index}', day='2026-10-19',
                                                starts_at='2026-10-19T01:00:00Z', reminder=0))
            with self.store.connect() as db:
                db.execute('UPDATE entries SET reminder_at=? WHERE id=?', (time.time()-1, task['id']))
        headers = {'Authorization': 'Bearer '+self.settings.cron_secret}
        first = self.request('/internal/reminders', 'POST', {}, headers)
        self.assertEqual(json.loads(first['body'])['sent'], 2)
        self.assertEqual(json.loads(self.request('/internal/reminders', 'POST', {}, headers)['body'])['sent'], 1)
        self.assertEqual(json.loads(self.request('/internal/reminders', 'POST', {}, headers)['body'])['sent'], 0)
        state = DeliveryState(self.store)
        owner = state.claim_job()
        self.assertIsNotNone(owner)
        self.assertIsNone(state.claim_job())
        self.assertEqual(self.request('/internal/reminders', 'POST', {}, headers)['status'], 202)
        state.release_job('wrong-owner')
        self.assertIsNone(state.claim_job())
        state.release_job(owner)
        self.assertIsNotNone(state.claim_job())

    def test_settings_fail_closed_and_ignore_local_demo_file(self):
        (self.root/'.env').write_text('BOT_TOKEN=local-token\nDEMO_MODE=1\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            load_settings(self.root, {})
        for changes in ({'public_url': 'http://ser.example'}, {'webhook_secret': 'short'},
                        {'cron_secret': 'w'*40}, {'token': ''}, {'public_url': 'https://x.example/path'}):
            with self.assertRaises(ValueError):
                replace(self.settings, **changes)
        self.assertNotIn(self.settings.token, repr(self.settings))

    def test_activation_preserves_foreign_webhook_and_pending_updates(self):
        calls = []
        foreign = True
        def sender(token, method, payload):
            calls.append((method, payload))
            if method == 'getMe': return {'is_bot': True, 'username': 'serritmbot'}
            if method == 'getWebhookInfo':
                return {'url': 'https://foreign.example/hook' if foreign else self.settings.webhook_url}
            return True
        with self.assertRaises(RuntimeError):
            activate(self.settings, sender)
        self.assertFalse(any(method == 'setWebhook' for method, payload in calls))
        foreign = False
        self.assertEqual(activate(self.settings, sender), 'serritmbot')
        hook = next(payload for method, payload in calls if method == 'setWebhook')
        self.assertFalse(hook['drop_pending_updates'])
        self.assertEqual(hook['secret_token'], self.settings.webhook_secret)
        self.assertEqual(hook['max_connections'], 1)

    def test_private_database_backup_keeps_records_and_never_overwrites(self):
        token = self.store.session('alice', 'Алиса')
        saved = self.store.save('alice', entry())
        target = self.root/'backups/copy.sqlite3'
        backup(self.store.path, target)
        copied = Store(target)
        self.assertEqual(copied.user(token)['id'], 'alice')
        self.assertEqual(copied.get('alice', saved['id'])['title'], saved['title'])
        with self.assertRaises(ValueError):
            backup(self.store.path, target)
        with self.assertRaises(ValueError):
            backup(self.root/'missing.sqlite3', self.root/'new.sqlite3')
        self.assertFalse((self.root/'missing.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
