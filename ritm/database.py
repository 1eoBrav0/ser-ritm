"""Select persistent cloud storage without changing the local SQLite setup."""

import os
from .store import Store


def open_store(settings, environ=None):
    values = os.environ if environ is None else environ
    dsn = values.get('DATABASE_URL', '')
    if dsn:
        from .postgres import PostgresStore
        return PostgresStore(dsn)
    if values.get('RENDER') == 'true':
        raise ValueError('Для Render нужен DATABASE_URL: локальная база не сохранится при перезапуске.')
    return Store(settings.database_path)
