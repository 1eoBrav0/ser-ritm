"""Local Telegram launch: authenticated app on a separate port and a temporary HTTPS tunnel."""
import argparse
import json
import logging
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from ritm.bot import check_bot, configure_bot, polling_loop, reminder_loop
from ritm.store import Store
from server import load_env, make_server

ROOT = Path(__file__).resolve().parent
URL_PATTERN = re.compile(r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com(?=[/\s]|$)")


class LaunchLock:
    def __enter__(self):
        (ROOT / 'data').mkdir(exist_ok=True)
        self.file = (ROOT / 'data/telegram.lock').open('a+b')
        self.file.seek(0)
        if not self.file.read(1):
            self.file.write(b'1')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('Telegram-версия уже запущена. Используй существующий процесс.') from None
        return self

    def __exit__(self, *args):
        self.file.close()


def start_tunnel(port):
    executable = ROOT / 'tools/cloudflared.exe'
    command = str(executable) if executable.is_file() else shutil.which('cloudflared')
    if not command:
        raise RuntimeError('Не найден cloudflared. Сначала запусти install_tunnel.py.')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    process = subprocess.Popen([command, 'tunnel', '--no-autoupdate', '--protocol', 'http2',
        '--url', f'http://127.0.0.1:{port}'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf8', errors='replace', creationflags=flags)
    output = queue.Queue()

    def read_output():
        for line in process.stdout:
            output.put(line)
    threading.Thread(target=read_output, daemon=True).start()
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('HTTPS-туннель завершился при запуске. Проверь подключение к интернету.')
            try:
                line = output.get(timeout=1)
            except queue.Empty:
                continue
            match = URL_PATTERN.search(line)
            if match:
                # Keep draining logs without storing request paths or other runtime data.
                def drain():
                    while process.poll() is None:
                        try:
                            output.get(timeout=1)
                        except queue.Empty:
                            pass
                threading.Thread(target=drain, daemon=True).start()
                return process, match.group()
        raise RuntimeError('Не удалось получить HTTPS-адрес за 90 секунд. Попробуй запустить ещё раз.')
    except BaseException:
        process.terminate()
        process.wait(timeout=10)
        raise


def write_status(**values):
    path = ROOT / 'data/telegram-runtime.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description='Запустить Ser Ритм в Telegram с этого компьютера.')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--public-url', default='', help='Постоянный HTTPS-адрес вместо временного туннеля')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port == 8765:
        raise ValueError('Для Telegram выбери отдельный порт от 1024 до 65535, например 8766.')
    load_env()
    token = os.getenv('BOT_TOKEN', '').strip()
    if not token:
        raise RuntimeError('Вставь токен BotFather в BOT_TOKEN локального файла .env.')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    tunnel = server = None
    stop = threading.Event()
    with LaunchLock():
        bot = check_bot(token)
        print(f"Бот: @{bot['username']}", flush=True)
        public_url = args.public_url.rstrip('/')
        try:
            # Reserve this port while the tunnel is prepared, so it cannot expose a different service.
            reservation = socket.socket()
            try:
                reservation.bind(('127.0.0.1', args.port))
                if not public_url:
                    print('Подключаю временный HTTPS-адрес…', flush=True)
                    tunnel, public_url = start_tunnel(args.port)
            finally:
                reservation.close()
            store = Store(ROOT / 'data/telegram.sqlite3')
            server = make_server(store, port=args.port, token=token, public_url=public_url, demo=False)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            configure_bot(token, public_url)
            for target, params in ((polling_loop, (store, token, public_url, stop)),
                                   (reminder_loop, (store, token, stop, public_url))):
                threading.Thread(target=target, args=params, daemon=True).start()
            write_status(running=True, pid=os.getpid(), bot_username=bot['username'],
                         public_url=public_url, port=args.port, temporary=tunnel is not None)
            print(f"Готово: https://t.me/{bot['username']}", flush=True)
            print(f'Адрес приложения: {public_url}', flush=True)
            print('Открой бота и отправь /start. Остановка — Ctrl+C.', flush=True)
            while not stop.wait(1):
                if tunnel and tunnel.poll() is not None:
                    raise RuntimeError('HTTPS-туннель отключился. Перезапусти Telegram-версию.')
        finally:
            stop.set()
            if server:
                server.shutdown()
                server.server_close()
            if tunnel and tunnel.poll() is None:
                tunnel.terminate()
                tunnel.wait(timeout=10)
            write_status(running=False, bot_username=bot['username'])


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nTelegram-версия остановлена.')
    except (RuntimeError, ValueError, OSError) as error:
        print(f'Не удалось запустить: {error}', file=sys.stderr)
        raise SystemExit(1)
