import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_telegram(data, token, now=None):
    """Verify Telegram's HMAC before reading any user fields."""
    if not token or not isinstance(data, str) or len(data) > 16000:
        raise ValueError("Не удалось проверить вход через Telegram.")
    pairs = parse_qsl(data, keep_blank_values=True, strict_parsing=True)
    fields = dict(pairs)
    if len(pairs) != len(fields):
        raise ValueError("Повторяющиеся поля авторизации.")
    received = fields.pop("hash", "")
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise ValueError("Неверная подпись Telegram.")
    now = time.time() if now is None else now
    age = now - int(fields.get("auth_date", 0))
    if not -30 <= age <= 3600:
        raise ValueError("Ссылка входа устарела. Открой приложение заново.")
    user = json.loads(fields.get("user", "{}"))
    if type(user.get("id")) is not int or user["id"] <= 0:
        raise ValueError("Нет пользователя Telegram.")
    return user
