import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from ritm.bot import TelegramError, check_bot, configure_bot, deliver_reminders, handle_message, telegram
from ritm.store import Store
from run_telegram import URL_PATTERN
from test_app import entry


class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'bot.db')
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def sender(self, token, method, payload):
        self.calls.append((method,payload))
        return True

    def message(self, text, **changes):
        result = {'chat':{'type':'private','id':42}, 'from':{'id':42,'first_name':'Серафим'}, 'text':text}
        result.update(changes)
        return result

    def test_start_app_and_stop_keep_records(self):
        handle_message(self.store,'fake','https://app.example',self.message('/start'),self.sender)
        token = self.store.session('tg:42','Серафим')
        self.assertEqual(self.store.user(token)['chat_id'],42)
        button = self.calls[-1][1]['reply_markup']['inline_keyboard'][0][0]
        self.assertEqual(button['web_app']['url'],'https://app.example')
        saved = self.store.save('tg:42',entry(starts_at='2026-10-19T12:00:00Z',day='2026-10-19',reminder=0))
        handle_message(self.store,'fake','https://app.example',self.message('/stop'),self.sender)
        self.assertIsNone(self.store.user(token)['chat_id'])
        self.assertIsNotNone(self.store.get('tg:42',saved['id']))
        self.assertEqual(self.store.pending(saved['reminder_at']+1),[])
        handle_message(self.store,'fake','https://app.example',self.message('/app'),self.sender)
        self.assertIsNone(self.store.user(token)['chat_id'])
        handle_message(self.store,'fake','https://app.example',self.message('/start test'),self.sender)
        self.assertEqual(len(self.store.pending(saved['reminder_at']+1)),1)

    def test_private_commands_and_no_text_storage(self):
        for text in ['/app','/help','/privacy','Обычное сообщение','/start@serritmbot']:
            self.assertTrue(handle_message(self.store,'fake','https://app.example',self.message(text),self.sender))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM entries').fetchone()[0],0)
        self.assertEqual(len(self.calls),5)

    def test_ignore_groups_forged_ids_and_bot_messages(self):
        for changes in [dict(chat={'type':'group','id':-1}),dict(chat={'type':'private','id':99}),
                        dict(**{'from':{'id':True}}),dict(**{'from':{'id':42,'is_bot':True}}),dict(**{'from':{}})]:
            self.assertFalse(handle_message(self.store,'fake','',self.message('/start',**changes),self.sender))
        self.assertEqual(self.calls,[])

    def test_unconfigured_app_does_not_send_invalid_webapp_button(self):
        handle_message(self.store,'fake','',self.message('/start'),self.sender)
        self.assertNotIn('reply_markup',self.calls[-1][1])
        self.assertIn('HTTPS',self.calls[-1][1]['text'])

    def test_reminder_contains_open_button_and_is_sent_once(self):
        self.store.link_chat(42,'Серафим')
        saved=self.store.save('tg:42',entry(starts_at='2026-10-19T12:00:00Z',day='2026-10-19',reminder=0))
        now=saved['reminder_at']+1
        self.assertEqual(deliver_reminders(self.store,'fake',self.sender,now,public_url='https://app.example'),1)
        self.assertEqual(self.calls[-1][1]['reply_markup']['inline_keyboard'][0][0]['web_app']['url'],'https://app.example')
        self.assertEqual(deliver_reminders(self.store,'fake',self.sender,now+1),0)

    def test_configure_uses_correct_api_and_preserves_foreign_webhook(self):
        def api(token,method,payload):
            self.calls.append((method,payload))
            return {'is_bot':True,'username':'testbot'} if method=='getMe' else {} if method=='getWebhookInfo' else True
        configure_bot('fake','https://app.example',api)
        menu = next(p for m,p in self.calls if m=='setChatMenuButton')
        self.assertEqual(menu['menu_button']['web_app']['url'],'https://app.example')
        commands = next(p['commands'] for m,p in self.calls if m=='setMyCommands')
        self.assertEqual({c['command'] for c in commands},{'start','app','help','privacy','stop'})
        calls=[]
        def existing(token,method,payload):
            calls.append(method)
            return {'is_bot':True,'username':'testbot'} if method=='getMe' else {'url':'https://another.example/hook'}
        with self.assertRaises(RuntimeError):
            configure_bot('fake','https://app.example',existing)
        self.assertEqual(calls,['getMe','getWebhookInfo'])

    def test_invalid_public_url_is_rejected_before_network_calls(self):
        for url in ['http://example.org','https://user:pass@example.org','https://example.org/?token=x','https://example.org/#data']:
            with self.assertRaises(ValueError):
                configure_bot('fake',url,self.sender)
        self.assertEqual(self.calls,[])

    def test_api_errors_do_not_expose_token_or_response(self):
        secret='123:do-not-log'
        response=io.BytesIO(json.dumps({'parameters':{'retry_after':20},'description':secret}).encode())
        error=urllib.error.HTTPError('https://api.telegram.org/bot'+secret+'/getMe',429,'secret',{},response)
        with patch('urllib.request.urlopen',side_effect=error),self.assertRaises(TelegramError) as caught:
            telegram(secret,'getMe',{})
        self.assertEqual(caught.exception.retry_after,20)
        self.assertNotIn(secret,str(caught.exception))
        with patch('urllib.request.urlopen',side_effect=urllib.error.URLError(secret)),self.assertRaises(TelegramError) as caught:
            telegram(secret,'getMe',{})
        self.assertNotIn(secret,str(caught.exception))

    def test_quick_tunnel_accepts_only_expected_hostname(self):
        self.assertEqual(URL_PATTERN.search('URL https://calm-green-house.trycloudflare.com ready').group(),'https://calm-green-house.trycloudflare.com')
        self.assertIsNone(URL_PATTERN.search('https://example.org'))
        self.assertIsNone(URL_PATTERN.search('https://green.trycloudflare.com.evil.org'))
