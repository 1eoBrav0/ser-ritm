"""Create a consistent private SQLite backup without copying session secrets to Git."""

import argparse
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def backup(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if not source.is_file():
        raise ValueError('Исходная база не найдена.')
    if target.exists() or target == source:
        raise ValueError('Резервная копия уже существует; выбери новый путь.')
    target.parent.mkdir(parents=True, exist_ok=True)
    # mode=ro ensures a typo cannot create an empty source database.
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as original:
        with closing(sqlite3.connect(target)) as copy:
            original.backup(copy)
            if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Проверка резервной копии не прошла.')
    return target


def main():
    parser = argparse.ArgumentParser(description='Резервная копия базы Ритм.')
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = args.output or args.source.parent/'backups'/f'{args.source.stem}-{stamp}.sqlite3'
    try:
        print('Резервная копия: '+str(backup(args.source, target)))
    except (ValueError, RuntimeError, sqlite3.Error) as error:
        parser.exit(1, str(error)+'\n')


if __name__ == '__main__':
    main()
