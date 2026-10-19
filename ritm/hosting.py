"""WSGI deployment: shared app routes, Telegram webhook and scheduled reminders."""

import hmac
import io
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from ritm.bot import deliver_reminders, handle_message, telegram
from server import make_handler

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    public_url: str
    database_path: Path
    token: str = field(repr=False)
    webhook_secret: str = field(repr=False)
    cron_secret: str = field(repr=False)

    def __post_init__(self):
        url = urlsplit(self.public_url)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ('', '/')):
            raise ValueError('PUBLIC_URL должен быть корневым HTTPS-адресом сайта.')
        if not self.token.strip():
            raise ValueError('Укажи BOT_TOKEN в .env.cloud на сервере.')
        for key, value in [('WEBHOOK_SECRET', self.webhook_secret), ('CRON_SECRET', self.cron_secret)]:
            if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', value):
                raise ValueError(f'{key}: нужен отдельный случайный ключ длиной от 32 символов.')
        if self.webhook_secret == self.cron_secret or self.token in (self.webhook_secret, self.cron_secret):
            raise ValueError('Ключи webhook, расписания и токен бота должны различаться.')

    @property
    def webhook_url(self):
        return self.public_url.rstrip('/') + '/telegram/webhook'


def load_settings(root, environ=None):
    root = Path(root)
    values = {}
    file = root / '.env.cloud'
    if file.exists():
        for line in file.read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('\"\'')
    environment = os.environ if environ is None else environ
    keys = ('PUBLIC_URL', 'BOT_TOKEN', 'WEBHOOK_SECRET', 'CRON_SECRET', 'DATA_DIR', 'RENDER_EXTERNAL_URL')
    values.update({key: environment[key] for key in keys if key in environment})
    data = Path(values.get('DATA_DIR') or 'data')
    if not data.is_absolute():
        data = root / data
    return Settings((values.get('PUBLIC_URL') or values.get('RENDER_EXTERNAL_URL', '')).rstrip('/'), data/'telegram.sqlite3',
                    values.get('BOT_TOKEN', ''), values.get('WEBHOOK_SECRET', ''), values.get('CRON_SECRET', ''))


class DeliveryState:
    """Small durable leases prevent overlapping cron runs and webhook retries."""

    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS hosting_updates (
                    id INTEGER PRIMARY KEY, finished_at REAL, lease_until REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS hosting_leases (
                    name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL
                );
            ''')

    def claim_update(self, update_id):
        now = time.time()
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO hosting_updates(id) VALUES(?)', (update_id,))
            if db.execute('UPDATE hosting_updates SET lease_until=? WHERE id=? AND finished_at IS NULL AND lease_until<=?',
                          (now+45, update_id, now)).rowcount:
                return 'claimed'
            row = db.execute('SELECT finished_at FROM hosting_updates WHERE id=?', (update_id,)).fetchone()
            return 'done' if row['finished_at'] is not None else 'busy'

    def finish_update(self, update_id, success):
        with self.store.connect() as db:
            db.execute('UPDATE hosting_updates SET finished_at=?,lease_until=0 WHERE id=?',
                       (time.time() if success else None, update_id))

    def claim_job(self):
        now, owner = time.time(), secrets.token_hex(16)
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO hosting_leases VALUES('reminders','',0)")
            claimed = db.execute("UPDATE hosting_leases SET owner=?,expires=? WHERE name='reminders' AND expires<=?",
                                 (owner, now+45, now)).rowcount
            db.execute('DELETE FROM hosting_updates WHERE finished_at IS NOT NULL AND finished_at<?', (now-7*86400,))
        return owner if claimed else None

    def release_job(self, owner):
        with self.store.connect() as db:
            db.execute("UPDATE hosting_leases SET expires=0 WHERE name='reminders' AND owner=?", (owner,))


def create_application(store, settings, sender=None):
    handler_class = make_handler(store, token=settings.token, public_url=settings.public_url, demo=False)
    state = DeliveryState(store)

    def send(token, method, payload):
        # Keep one request below the free web worker / scheduler timeout.
        return telegram(token, method, payload, timeout=8)

    send = sender or send

    class WsgiHandler(handler_class):
        def __init__(self, environ):
            self.command = environ.get('REQUEST_METHOD', 'GET')
            self.path = environ.get('PATH_INFO', '/')
            if environ.get('QUERY_STRING'):
                self.path += '?' + environ['QUERY_STRING']
            self.headers = Message()
            for key, value in environ.items():
                if key.startswith('HTTP_'):
                    self.headers[key[5:].replace('_', '-')] = value
            for key in ('CONTENT_LENGTH', 'CONTENT_TYPE'):
                if environ.get(key):
                    self.headers[key.replace('_', '-')] = environ[key]
            self.rfile = environ['wsgi.input']
            self.wfile = io.BytesIO()
            self.server = SimpleNamespace(server_port=int(environ.get('SERVER_PORT', 443)))
            self.status = 200
            self.response_headers = []

        def send_response(self, code, message=None):
            self.status = code

        def send_header(self, name, value):
            self.response_headers.append((name, str(value)))

        def end_headers(self):
            pass

    def webhook(request):
        data = request.body()
        update_id = data.get('update_id')
        message = data.get('message', {})
        if type(update_id) is not int or update_id < 0 or not isinstance(message, dict):
            raise ValueError('Некорректное обновление Telegram.')
        for key in ('chat', 'from'):
            if key in message and not isinstance(message[key], dict):
                raise ValueError('Некорректное сообщение Telegram.')
        if not isinstance(message.get('text', ''), str):
            raise ValueError('Некорректный текст сообщения.')
        claim = state.claim_update(update_id)
        if claim == 'done':
            request.respond(200, {'ok': True})
            return
        if claim == 'busy':
            request.respond(503, {'error': 'Обновление ещё обрабатывается.'})
            return
        try:
            handle_message(store, settings.token, settings.public_url, message, send, hosted=True)
        except Exception:
            state.finish_update(update_id, False)
            raise
        state.finish_update(update_id, True)
        request.respond(200, {'ok': True})

    def reminders(request):
        owner = state.claim_job()
        if not owner:
            request.respond(202, {'ok': True, 'busy': True})
            return
        try:
            sent = deliver_reminders(store, settings.token, send, public_url=settings.public_url, limit=2)
        finally:
            state.release_job(owner)
        request.respond(200, {'ok': True, 'sent': sent})

    def application(environ, start_response):
        request = WsgiHandler(environ)
        route = environ.get('PATH_INFO', '/')
        expected_host = urlsplit(settings.public_url).netloc
        if request.headers.get('Host') != expected_host or environ.get('wsgi.url_scheme') != 'https':
            request.respond(403, {'error': 'Недопустимый адрес сервера.'})
        elif route in ('/telegram/webhook', '/internal/reminders'):
            if request.command != 'POST':
                request.respond(405, {'error': 'Нужен POST-запрос.'})
            else:
                expected = settings.webhook_secret if route == '/telegram/webhook' else 'Bearer '+settings.cron_secret
                header = 'X-Telegram-Bot-Api-Secret-Token' if route == '/telegram/webhook' else 'Authorization'
                supplied = request.headers.get(header, '')
                if not hmac.compare_digest(supplied.encode(), expected.encode()):
                    request.respond(403, {'error': 'Доступ запрещён.'})
                else:
                    try:
                        (webhook if route == '/telegram/webhook' else reminders)(request)
                    except (ValueError, TypeError, KeyError, UnicodeError):
                        request.respond(400, {'error': 'Некорректный запрос.'})
                    except Exception:
                        # Never include token-bearing network URLs or user messages in logs.
                        log.warning('Облачный обработчик временно недоступен.')
                        request.respond(503, {'error': 'Сервис временно недоступен.'})
        else:
            request.dispatch()
        status = f'{request.status} {HTTPStatus(request.status).phrase}'
        start_response(status, request.response_headers)
        return [request.wfile.getvalue()]

    return application
