"""Copy a private SQLite backup into an empty PostgreSQL planner schema."""

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from ritm.postgres import PostgresStore


TABLES = {
    'users': ('id','name','chat_id','onboarding_seen'),
    'series': ('id','user_id','start_day','until_day','timezone','rule','template','created_at'),
    'entries': ('id','user_id','kind','title','details','day','starts_at','duration','priority','category',
                'done','reminder','reminder_at','notified','retry_at','created_at','archived','ends_at',
                'series_id','occurrence_day'),
}


def migrate(source, store):
    source = Path(source).resolve()
    if not source.is_file():
        raise ValueError('Исходная база не найдена.')
    counts = {}
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as original:
        if original.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Проверка исходной базы не прошла.')
        if original.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('В исходной базе есть несвязанные записи.')
        with store.connect() as target:
            # Never merge or overwrite an already used remote profile database.
            for table in TABLES:
                if target.execute(f'SELECT COUNT(*) AS count FROM {table}').fetchone()['count']:
                    raise ValueError('Целевая база уже содержит записи. Перенос остановлен.')
            for table, columns in TABLES.items():
                names = ','.join(columns)
                rows = original.execute(f'SELECT {names} FROM {table}').fetchall()
                for row in rows:
                    target.execute(f"INSERT INTO {table} ({names}) VALUES ({','.join('?' for _ in columns)})", row)
                count = target.execute(f'SELECT COUNT(*) AS count FROM {table}').fetchone()['count']
                if count != len(rows):
                    raise RuntimeError('Не совпало число перенесённых записей.')
                counts[table] = count
    # Session cookies deliberately stay behind; Telegram issues a fresh signed login.
    return counts


def main():
    parser = argparse.ArgumentParser(description='Перенос закрытой SQLite-базы в PostgreSQL.')
    parser.add_argument('source',type=Path)
    args = parser.parse_args()
    if not os.environ.get('DATABASE_URL'):
        parser.exit(1,'Укажи DATABASE_URL в окружении, не в аргументах команды.\n')
    try:
        counts = migrate(args.source, PostgresStore(os.environ['DATABASE_URL']))
    except (ValueError, RuntimeError, sqlite3.Error):
        parser.exit(1,'Перенос не завершён. Проверь доступ и пустую целевую базу; исходник не изменён.\n')
    print('Перенесено: '+', '.join(f'{name}: {count}' for name,count in counts.items()))


if __name__ == '__main__':
    main()
