import json
import logging
import os
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from ritm.auth import validate_telegram
from ritm.bot import configure_bot, polling_loop, reminder_loop
from ritm.store import Store

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("ritm")


def load_env():
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def make_handler(store, host="127.0.0.1", token="", public_url="", demo=True):
    if demo and (host not in ("127.0.0.1", "localhost", "::1") or public_url):
        raise ValueError("Деморежим разрешён только локально без PUBLIC_URL. Установи DEMO_MODE=0.")
    if public_url and (urlparse(public_url).scheme != "https" or not urlparse(public_url).netloc):
        raise ValueError("PUBLIC_URL должен быть HTTPS-адресом.")
    public_host = urlparse(public_url).netloc

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Request URLs can contain Telegram launch parameters.
            pass

        def respond(self, status, payload, cookie=None, content_type="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 20000:
                raise ValueError("Слишком большой или пустой запрос.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Ожидается JSON-объект.")
            return data

        def current_user(self):
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            value = cookies.get("ritm_session")
            return store.user(value.value) if value else None

        def safe_request(self):
            local_host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if public_host:
                allowed.add(public_host)
            if local_host not in allowed:
                self.respond(403, {"error": "Недопустимый адрес сервера."})
                return False
            if self.command != "GET":
                origin = self.headers.get("Origin")
                allowed_origins = {f"http://{h}" for h in allowed if h != public_host}
                if public_host:
                    allowed_origins.add(f"https://{public_host}")
                if (origin and origin not in allowed_origins) or self.headers.get("X-Ritm-Request") != "1":
                    self.respond(403, {"error": "Запрос должен прийти из приложения."})
                    return False
            return True

        def handle_request(self):
            if not self.safe_request():
                return
            route = urlparse(self.path).path
            if route == "/api/health" and self.command == "GET":
                self.respond(200, {"ok": True})
                return
            if route == "/api/session" and self.command == "POST":
                data = self.body()
                init_data = data.get("init_data", "")
                current = self.current_user()
                if init_data:
                    tg = validate_telegram(init_data, token)
                    user_id, name = f"tg:{tg['id']}", tg.get("first_name", "Друг")
                elif demo:
                    if current and current["id"].startswith("demo:"):
                        self.respond(200, {"user": current, "demo": True, "bot_connected": bool(token)})
                        return
                    user_id, name = f"demo:{secrets.token_hex(16)}", "Серафим"
                else:
                    self.respond(401, {"error": "Открой приложение через Telegram."})
                    return
                session_token = store.session(user_id, name)
                policy = "SameSite=None; Secure" if public_url else "SameSite=Lax"
                self.respond(200, {"user": store.user(session_token), "demo": user_id.startswith("demo:"), "bot_connected": bool(token)}, f"ritm_session={session_token}; Path=/; HttpOnly; {policy}; Max-Age=604800")
                return
            if route.startswith("/api/"):
                user = self.current_user()
                if not user:
                    self.respond(401, {"error": "Сессия завершилась. Перезагрузи приложение."})
                    return
                if route == "/api/onboarding" and self.command == "POST":
                    data = self.body()
                    if data.get("seen") is not True:
                        raise ValueError("Ожидается подтверждение просмотра.")
                    store.finish_onboarding(user["id"])
                    self.respond(200, {"ok": True})
                elif route == "/api/export" and self.command == "GET":
                    self.respond(200, store.export_data(user["id"]))
                elif route == "/api/entries" and self.command == "GET":
                    query = parse_qs(urlparse(self.path).query)
                    self.respond(200, {"entries": store.entries(user["id"], query.get("from", [None])[0], query.get("to", [None])[0])})
                elif route == "/api/entries" and self.command == "POST":
                    self.respond(201, store.save(user["id"], self.body()))
                elif route.startswith("/api/entries/") and self.command in ("PUT", "DELETE"):
                    entry_id = route.rsplit("/", 1)[-1]
                    if self.command == "PUT":
                        self.respond(200, store.save(user["id"], self.body(), entry_id))
                    elif store.delete(user["id"], entry_id, parse_qs(urlparse(self.path).query).get("scope", ["one"])[0]):
                        self.respond(200, {"ok": True})
                    else:
                        self.respond(404, {"error": "Запись не найдена."})
                else:
                    self.respond(404, {"error": "Неизвестный запрос."})
                return
            files = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/planner.js": ("planner.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
                "/ser-logo.png": ("ser-logo.png", "image/png"),
            }
            if self.command == "GET" and route in files:
                filename, mime = files[route]
                self.respond(200, (ROOT / "static" / filename).read_bytes(), content_type=mime)
            else:
                self.respond(404, {"error": "Страница не найдена."})

        def dispatch(self):
            try:
                self.handle_request()
            except LookupError:
                self.respond(404, {"error": "Запись не найдена."})
            except ValueError as error:
                self.respond(400, {"error": str(error) or "Проверь заполнение полей и дату записи."})
            except (TypeError, KeyError, UnicodeError):
                self.respond(400, {"error": "Проверь заполнение полей и дату записи."})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                log.exception("Ошибка сервера")
                self.respond(500, {"error": "Не удалось сохранить изменения. Попробуй ещё раз."})

        do_GET = dispatch
        do_POST = dispatch
        do_PUT = dispatch
        do_DELETE = dispatch

    return Handler


def make_server(store, host="127.0.0.1", port=8765, token="", public_url="", demo=True):
    handler = make_handler(store, host, token, public_url, demo)
    return ThreadingHTTPServer((host, port), handler)


def main():
    load_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    store = Store(ROOT / "data" / "ritm.sqlite3")
    token = os.getenv("BOT_TOKEN", "")
    public_url = os.getenv("PUBLIC_URL", "").rstrip("/")
    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8765"))
    demo = os.getenv("DEMO_MODE", "1") == "1"
    server = make_server(store, host, port, token, public_url, demo)
    stop = threading.Event()
    if token and not demo:
        if public_url:
            configure_bot(token, public_url)
        for target, args in ((polling_loop, (store, token, public_url, stop)), (reminder_loop, (store, token, stop, public_url))):
            threading.Thread(target=target, args=args, daemon=True).start()
    print(f"Ритм: http://{host}:{port}", flush=True)
    print("Локальная демоверсия. Ctrl+C — остановить." if demo else "Вход только через Telegram.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
