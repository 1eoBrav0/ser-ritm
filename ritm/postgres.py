"""PostgreSQL storage for hosts without a persistent local disk."""

import re
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .store import Store


SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, chat_id BIGINT,
    onboarding_seen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL, title TEXT NOT NULL, details TEXT NOT NULL,
    day TEXT NOT NULL, starts_at TEXT, duration INTEGER NOT NULL,
    priority TEXT NOT NULL, category TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0, reminder INTEGER NOT NULL DEFAULT -1,
    reminder_at DOUBLE PRECISION, notified INTEGER NOT NULL DEFAULT 0,
    retry_at DOUBLE PRECISION NOT NULL DEFAULT 0, created_at DOUBLE PRECISION NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0, ends_at TEXT, series_id TEXT, occurrence_day TEXT
);
CREATE INDEX IF NOT EXISTS entries_user_day ON entries(user_id,day);
CREATE UNIQUE INDEX IF NOT EXISTS entries_occurrence ON entries(series_id,occurrence_day);
CREATE TABLE IF NOT EXISTS series (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
    start_day TEXT NOT NULL, until_day TEXT, timezone TEXT NOT NULL,
    rule TEXT NOT NULL, template TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS series_user ON series(user_id);
CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY);
'''


class Connection:
    """Keep the small store's parameter interface identical for both databases."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=None):
        import psycopg
        # Only fixed application SQL passes here; user values remain parameters.
        if statement.startswith('INSERT OR IGNORE INTO '):
            statement = statement.replace('INSERT OR IGNORE INTO ', 'INSERT INTO ', 1)
            statement += ' ON CONFLICT DO NOTHING'
        try:
            return self.connection.execute(statement.replace('?', '%s'), parameters)
        except psycopg.Error:
            raise RuntimeError('Не удалось выполнить запрос к базе.') from None

    def executescript(self, script):
        # Our schema scripts contain DDL only, with no values or function bodies.
        script = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', script)
        script = script.replace('id INTEGER PRIMARY KEY', 'id BIGINT PRIMARY KEY')
        for statement in script.split(';'):
            if statement.strip():
                self.execute(statement.strip())


class PostgresStore(Store):
    def __init__(self, dsn, schema='ritm'):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', schema):
            raise ValueError('Некорректное имя схемы базы.')
        try:
            address = urlsplit(dsn)
            if address.scheme not in ('postgres', 'postgresql') or not address.hostname:
                raise ValueError
        except ValueError:
            raise ValueError('DATABASE_URL должен содержать адрес PostgreSQL.') from None
        self._dsn = dsn
        certificate = parse_qs(address.query).get('sslrootcert', ['system'])[0]
        if certificate != 'system':
            certificate = str((Path(__file__).resolve().parent.parent / certificate).resolve())
        self._sslrootcert = certificate
        self.schema = schema
        self._local = address.hostname in ('localhost', '127.0.0.1', '::1')
        import psycopg
        from psycopg import sql
        with self._connection() as raw:
            raw.execute("SELECT pg_advisory_xact_lock(hashtext('ser_ritm_schema'))")
            raw.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))
            self._select_schema(raw)
            Connection(raw).executescript(SCHEMA)
            # Supabase's browser API must not expose planner records.
            roles = ['PUBLIC'] + [row['rolname'] for row in raw.execute(
                "SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")]
            for role in roles:
                target = sql.SQL('PUBLIC') if role == 'PUBLIC' else sql.Identifier(role)
                raw.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM {}').format(sql.Identifier(schema), target))
                raw.execute(sql.SQL('REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}').format(sql.Identifier(schema), target))

    @contextmanager
    def _connection(self):
        import psycopg
        from psycopg.rows import dict_row
        # verify-full validates the remote certificate and hostname, not just encryption.
        tls = {} if self._local else {'sslmode': 'verify-full', 'sslrootcert': self._sslrootcert}
        try:
            with psycopg.connect(self._dsn, connect_timeout=10, prepare_threshold=None,
                                 row_factory=dict_row, **tls) as connection:
                yield connection
        except psycopg.Error:
            raise RuntimeError('База временно недоступна. Попробуй позже.') from None

    def _select_schema(self, connection):
        connection.execute("SELECT pg_catalog.set_config('search_path', %s, true)",
                           (self.schema+',pg_catalog',))

    @contextmanager
    def connect(self):
        with self._connection() as connection:
            self._select_schema(connection)
            yield Connection(connection)
