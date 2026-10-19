import json
import logging
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    def __init__(self, code=0, retry_after=10):
        messages = {401: "Токен бота не принят Telegram.", 409: "Этот бот уже запущен другим процессом.",
                    429: "Telegram просит подождать перед следующим запросом."}
        super().__init__(messages.get(code, "Telegram API временно недоступен."))
        self.code = code
        self.retry_after = min(max(retry_after, 1), 300)


def telegram(token, method, payload, timeout=35):
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            details = json.loads(error.read())
            retry = int(details.get("parameters", {}).get("retry_after", 10))
        except (ValueError, TypeError, AttributeError):
            retry = 10
        raise TelegramError(error.code, retry) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        # Network exceptions can include the credential-bearing request URL.
        raise TelegramError() from None
    if not isinstance(result, dict) or not result.get("ok"):
        raise TelegramError(result.get("error_code", 0) if isinstance(result, dict) else 0)
    return result.get("result")


def app_button(public_url):
    if not public_url:
        return {}
    return {"reply_markup": {"inline_keyboard": [[{"text": "Открыть ежедневник", "web_app": {"url": public_url}}]]}}


def check_bot(token, sender=telegram, expected_webhook=""):
    bot = sender(token, "getMe", {})
    if not isinstance(bot, dict) or not bot.get("is_bot") or not bot.get("username"):
        raise RuntimeError("Telegram не подтвердил бота.")
    webhook = sender(token, "getWebhookInfo", {}).get("url")
    if webhook and webhook != expected_webhook:
        raise RuntimeError("У бота уже настроен webhook другого приложения. Сначала выбери, какое приложение должно использовать бота.")
    return bot


def configure_bot(token, public_url, sender=telegram, expected_webhook=""):
    parsed = urlparse(public_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Для Mini App нужен HTTPS-адрес без параметров и пароля.")
    bot = check_bot(token, sender, expected_webhook)
    commands = [
        {"command": "start", "description": "Начать и включить напоминания"},
        {"command": "app", "description": "Открыть ежедневник"},
        {"command": "help", "description": "Как пользоваться"},
        {"command": "stop", "description": "Отключить напоминания"},
        {"command": "privacy", "description": "О хранении данных"},
    ]
    sender(token, "setMyCommands", {"commands": commands})
    sender(token, "setMyName", {"name": "Ser Ритм"})
    sender(token, "setMyShortDescription", {"short_description": "Личный ежедневник: задачи, календарь, повторения и напоминания."})
    sender(token, "setMyDescription", {"description": "Ser Ритм — ежедневник внутри Telegram. Планируй дела, выбирай время и дни повторения, отмечай важное и смотри загрузку недели. Нажми «Начать», чтобы открыть приложение."})
    sender(token, "setChatMenuButton", {"menu_button": {"type": "web_app", "text": "Ежедневник", "web_app": {"url": public_url}}})
    return bot


def handle_message(store, token, public_url, message, sender=telegram, hosted=False):
    chat, user = message.get("chat", {}), message.get("from", {})
    user_id = user.get("id")
    if chat.get("type") != "private" or type(user_id) is not int or user_id <= 0 or chat.get("id") != user_id or user.get("is_bot"):
        return False
    text = message.get("text", "")
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
    if command == "/start":
        store.link_chat(user_id, user.get("first_name", "Друг"))
        reply = "Привет! Это Ser Ритм.\n\nЗдесь можно планировать дела, вести заметки и смотреть загрузку недели. Напоминания будут приходить в этот чат.\n\nНажми кнопку ниже, чтобы открыть ежедневник. Отключить напоминания: /stop."
    elif command == "/app":
        reply = "Твой ежедневник — по кнопке ниже. Для напоминаний сначала отправь /start."
    elif command == "/help":
        reply = "Добавь задачу в ежедневнике: название, дата и, если нужно, время «С» и «До».\n\nВажность и повторения выбираются в форме. Напоминание — в «Дополнительных настройках». Графики находятся под расписанием.\n\n/start — включить напоминания\n/stop — отключить напоминания\n/privacy — о данных\n\nСейчас приложение работает с компьютера владельца. Когда он выключен, приложение и напоминания недоступны."
    elif command == "/stop":
        store.unlink_chat(user_id)
        reply = "Напоминания отключены. Твои записи сохранены. Включить снова: /start."
    elif command == "/privacy":
        reply = "Для профиля хранятся твой Telegram ID, имя и созданные тобой задачи, события и заметки. Данные находятся в базе ежедневника на компьютере владельца сервиса. У разных пользователей отдельные записи.\n\nВыгрузка своих записей доступна в приложении: «Помощь» → «Скачать мои записи». /stop отключает напоминания и не удаляет записи."
    else:
        reply = "Задачи и заметки добавляются в приложении. Нажми кнопку ниже. Инструкция: /help."
    if hosted:
        reply = reply.replace(
            "Сейчас приложение работает с компьютера владельца. Когда он выключен, приложение и напоминания недоступны.",
            "Приложение работает на сервере. Напоминания проверяются примерно раз в минуту; при сбоях хостинга возможны задержки.")
        reply = reply.replace("на компьютере владельца сервиса", "на сервере владельца сервиса")
    if not public_url and command not in ("/stop", "/privacy"):
        reply = "Бот подключён. HTTPS-адрес приложения ещё настраивается. Попробуй /app после завершения настройки."
    sender(token, "sendMessage", {"chat_id": user_id, "text": reply, **app_button(public_url)})
    return True


def deliver_reminders(store, token, sender=telegram, now=None, public_url="", limit=None):
    now = time.time() if now is None else now
    count = 0
    for entry in store.pending(now)[:limit]:
        # Avoid sending a burst of stale reminders after a long shutdown.
        if now - entry["reminder_at"] > 86400:
            store.mark_notification(entry["id"], True, now)
            continue
        try:
            sender(token, "sendMessage", {"chat_id": entry["chat_id"],
                "text": f"Ser Ритм · напоминание\n\n{entry['title']}", **app_button(public_url)})
            store.mark_notification(entry["id"], True, now)
            count += 1
        except RuntimeError:
            store.mark_notification(entry["id"], False, now)
            log.warning("Не удалось доставить напоминание; повторим позже.")
    return count


def reminder_loop(store, token, stop, public_url=""):
    while not stop.is_set():
        try:
            deliver_reminders(store, token, public_url=public_url)
        except Exception:
            log.warning("Ошибка обработки напоминаний.")
        stop.wait(15)


def polling_loop(store, token, public_url, stop):
    offset = 0
    while not stop.is_set():
        try:
            updates = telegram(token, "getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ["message"]})
            for update in updates:
                handle_message(store, token, public_url, update.get("message", {}))
                offset = update["update_id"] + 1
        except TelegramError as error:
            log.warning("%s", error)
            if error.code in (401, 409):
                stop.set()
                return
            stop.wait(error.retry_after)
        except RuntimeError:
            log.warning("Бот временно не получает обновления.")
            stop.wait(10)
