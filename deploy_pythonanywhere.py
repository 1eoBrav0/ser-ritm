"""Check the hosted app, then explicitly activate Telegram webhook delivery."""

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

from ritm.bot import configure_bot, telegram
from ritm.hosting import load_settings


def check_site(settings):
    try:
        with urllib.request.urlopen(settings.public_url+'/api/health', timeout=15) as response:
            if json.load(response) != {'ok': True}:
                raise RuntimeError('Сайт отвечает некорректно.')
        try:
            urllib.request.urlopen(settings.public_url+'/api/entries', timeout=15)
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise RuntimeError('Проверь защиту сайта и настройки PUBLIC_URL.') from None
        else:
            raise RuntimeError('Нельзя подключать бота: сайт разрешает вход без Telegram.')
    except (urllib.error.URLError, ValueError, OSError):
        raise RuntimeError('Не удалось проверить HTTPS-сайт. Сначала запусти WSGI-приложение.') from None


def activate(settings, sender=telegram):
    # Only an empty webhook or the same application can be configured automatically.
    bot = configure_bot(settings.token, settings.public_url, sender, expected_webhook=settings.webhook_url)
    sender(settings.token, 'setWebhook', {
        'url': settings.webhook_url, 'secret_token': settings.webhook_secret,
        'allowed_updates': ['message'], 'max_connections': 1, 'drop_pending_updates': False,
    })
    info = sender(settings.token, 'getWebhookInfo', {})
    if info.get('url') != settings.webhook_url:
        raise RuntimeError('Telegram не подтвердил новый адрес.')
    return bot['username']


def main():
    parser = argparse.ArgumentParser(description='Проверка сервера и подключение Telegram.')
    parser.add_argument('--activate', action='store_true', help='Изменить меню бота и установить webhook после проверки сайта.')
    args = parser.parse_args()
    try:
        settings = load_settings(Path(__file__).resolve().parent)
        check_site(settings)
        if args.activate:
            print('Подключён @'+activate(settings)+'. Проверка напоминаний настраивается отдельно в cron-job.org.')
        else:
            print('Сайт доступен, анонимный вход закрыт. Бот не переключён. Для переключения: --activate.')
    except (ValueError, RuntimeError) as error:
        parser.exit(1, str(error)+'\n')


if __name__ == '__main__':
    main()
