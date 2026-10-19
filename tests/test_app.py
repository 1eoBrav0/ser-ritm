import hashlib
import hmac
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

from ritm.auth import validate_telegram
from ritm.bot import deliver_reminders
from ritm.store import Store, validate_entry
from server import make_server


def entry(**changes):
    values = dict(title="Повторить Python", details="Аргументы функций", kind="task", day="2026-10-18",
                starts_at=None, category="study", priority="normal", duration=60,
                reminder=-1, done=False)
    values.update(changes)
    return values


def signed_data(token, user_id=42, age=0):
    fields = {"auth_date": str(int(time.time()) - age), "user": json.dumps({"id": user_id, "first_name": "Серафим"})}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class AuthTests(unittest.TestCase):
    def test_signed_telegram_user(self):
        self.assertEqual(validate_telegram(signed_data("test-token"), "test-token")["id"], 42)

    def test_reject_tampering_expiry_and_future(self):
        for data in (signed_data("wrong-token"), signed_data("test-token", age=3605), signed_data("test-token", age=-300)):
            with self.assertRaises(ValueError):
                validate_telegram(data, "test-token")

    def test_reject_duplicate_fields(self):
        with self.assertRaises(ValueError):
            validate_telegram(signed_data("test-token") + "&auth_date=1", "test-token")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.store.session("alice", "Алиса")
        self.store.session("bob", "Боб")

    def tearDown(self):
        self.temp.cleanup()

    def test_crud_is_scoped_to_user(self):
        saved = self.store.save("alice", entry())
        self.assertEqual(len(self.store.entries("alice")), 1)
        self.assertEqual(self.store.entries("bob"), [])
        self.assertIsNone(self.store.get("bob", saved["id"]))
        with self.assertRaises(LookupError):
            self.store.save("bob", entry(), saved["id"])
        self.assertFalse(self.store.delete("bob", saved["id"]))
        payload = entry()
        payload["done"] = True
        self.assertTrue(self.store.save("alice", payload, saved["id"])["done"])
        self.assertTrue(self.store.delete("alice", saved["id"]))

    def test_persistence(self):
        saved = self.store.save("alice", entry())
        reopened = Store(self.store.path)
        self.assertEqual(reopened.get("alice", saved["id"])["title"], "Повторить Python")

    def test_old_examples_archived_without_deleting_personal_entries(self):
        self.store.session("demo:legacy", "Локальный профиль")
        self.store.session("tg:42", "Серафим")
        sample = entry(title="Набросать идеи для портфолио",
                       details="Выбрать одну задачу, которую действительно хочется решить.", category="work")
        old = self.store.save("demo:legacy", sample)
        edited = self.store.save("demo:legacy", {**sample, "details": "Моя собственная запись"})
        rescheduled = self.store.save("demo:legacy", {**sample, "starts_at": "2026-10-18T11:00:00+00:00"})
        telegram_entry = self.store.save("tg:42", sample)
        # Reproduce the previous schema, which had neither of the new columns.
        with self.store.connect() as db:
            db.execute("ALTER TABLE entries DROP COLUMN archived")
            db.execute("ALTER TABLE users DROP COLUMN onboarding_seen")
            db.execute("DELETE FROM migrations WHERE name='archive_old_examples'")
        upgraded = Store(self.store.path)
        self.assertIsNone(upgraded.get("demo:legacy", old["id"]))
        self.assertIsNotNone(upgraded.get("demo:legacy", edited["id"]))
        self.assertIsNotNone(upgraded.get("demo:legacy", rescheduled["id"]))
        self.assertIsNotNone(upgraded.get("tg:42", telegram_entry["id"]))
        with upgraded.connect() as db:
            self.assertEqual(db.execute("SELECT archived FROM entries WHERE id=?", (old["id"],)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT onboarding_seen FROM users WHERE id='demo:legacy'").fetchone()[0], 0)
        # Cleanup is one-time: a later real entry with the same text stays visible.
        later = upgraded.save("demo:legacy", sample)
        self.assertIsNotNone(Store(self.store.path).get("demo:legacy", later["id"]))

    def test_validation_and_utc_conversion(self):
        for field, value in (("title", " "), ("day", "2026-02-30"), ("kind", "script"), ("duration", -20), ("done", "yes"), ("reminder", 9), ("starts_at", "2026-10-18T12:00:00")):
            payload = entry()
            payload[field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError)):
                validate_entry(payload)
        payload = entry()
        payload.update(starts_at="2026-10-18T12:00:00+09:00", reminder=15)
        validated = validate_entry(payload)
        self.assertEqual(validated["starts_at"], "2026-10-18T03:00:00+00:00")
        self.assertEqual(validated["reminder_at"], 1792291500)

    def test_notifications_retry_and_completed_tasks(self):
        self.store.link_chat(42, "Серафим")
        payload = entry()
        payload.update(starts_at="2026-10-18T12:00:00+09:00", reminder=0)
        saved = self.store.save("tg:42", payload)
        now = saved["reminder_at"] + 1
        calls = []
        def fail(*args):
            raise RuntimeError("offline")
        self.assertEqual(deliver_reminders(self.store, "fake", fail, now), 0)
        self.assertEqual(self.store.pending(now + 60), [])
        self.assertEqual(deliver_reminders(self.store, "fake", lambda *args: calls.append(args), now + 121), 1)
        self.assertEqual(deliver_reminders(self.store, "fake", lambda *args: calls.append(args), now + 122), 0)
        self.assertEqual(len(calls), 1)
        payload["done"] = True
        self.store.save("tg:42", payload, saved["id"])
        self.assertEqual(self.store.pending(now + 1000), [])

    def test_time_range_for_tasks_and_events(self):
        for kind in ("task", "event"):
            payload = entry(title="Работа", kind=kind, starts_at="2026-10-18T15:00:00+09:00",
                            ends_at="2026-10-18T17:00:00+09:00", duration=60, reminder=15)
            saved = self.store.save("alice", payload)
            self.assertEqual(saved["duration"], 120)
            self.assertEqual(saved["ends_at"], "2026-10-18T08:00:00+00:00")
            self.assertEqual(Store(self.store.path).get("alice", saved["id"])["duration"], 120)
            changed = self.store.save("alice", {**saved, "ends_at": "2026-10-18T08:45:00Z"}, saved["id"])
            self.assertEqual(changed["duration"], 165)
        self.assertIsNone(validate_entry(entry())["ends_at"])

    def test_invalid_ranges_are_rejected(self):
        start = "2026-10-18T15:00:00+09:00"
        for end in (start, "2026-10-18T14:00:00+09:00", "2026-10-20T17:00:00+09:00", "2026-10-18T17:00:00"):
            with self.subTest(end=end), self.assertRaises(ValueError):
                validate_entry(entry(starts_at=start, ends_at=end))
        with self.assertRaises(ValueError):
            validate_entry(entry(ends_at="2026-10-18T17:00:00+09:00"))
        self.assertEqual(validate_entry(entry(starts_at=start, ends_at="2026-10-18T15:05:00+09:00"))["duration"], 5)

    def test_old_event_duration_migrates_without_changing_task_time(self):
        old_event = self.store.save("alice", entry(kind="event", starts_at="2026-10-18T15:00:00+09:00", duration=120))
        old_task = self.store.save("alice", entry(starts_at="2026-10-18T15:00:00+09:00"))
        with self.store.connect() as db:
            db.execute("ALTER TABLE entries DROP COLUMN ends_at")
        upgraded = Store(self.store.path)
        self.assertEqual(upgraded.get("alice", old_event["id"])["ends_at"], "2026-10-18T08:00:00+00:00")
        self.assertIsNone(upgraded.get("alice", old_task["id"])["ends_at"])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "http.sqlite3")
        self.server = make_server(self.store, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, method="GET", data=None, cookie="", extra=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        headers = {"Content-Type": "application/json", "X-Ritm-Request": "1", "Cookie": cookie}
        headers.update(extra or {})
        conn.request(method, path, json.dumps(data) if data is not None else None, headers)
        response = conn.getresponse()
        body = response.read()
        result = (response.status, dict(response.getheaders()), json.loads(body) if response.getheader("Content-Type").startswith("application/json") else body)
        conn.close()
        return result

    def login(self):
        status, headers, _ = self.request("/api/session", "POST", {})
        self.assertEqual(status, 200)
        return headers["Set-Cookie"].split(";", 1)[0]

    def test_http_isolation_and_reload(self):
        alice, bob = self.login(), self.login()
        status, _, saved = self.request("/api/entries", "POST", entry(), alice)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("/api/entries", cookie=bob)[2]["entries"], [])
        self.assertEqual(self.request(f"/api/entries/{saved['id']}", "DELETE", {}, bob)[0], 404)
        self.request("/api/session", "POST", {}, alice)
        self.assertEqual(len(self.request("/api/entries", cookie=alice)[2]["entries"]), 1)
        self.assertEqual(self.request("/api/entries")[0], 401)

    def test_empty_first_login_and_onboarding_persists_per_user(self):
        alice, bob = self.login(), self.login()
        session = self.request("/api/session", "POST", {}, alice)[2]
        self.assertFalse(session["user"]["onboarding_seen"])
        self.assertEqual(self.request("/api/entries", cookie=alice)[2]["entries"], [])
        self.assertEqual(self.request("/api/onboarding", "POST", {"seen": True}, alice)[0], 200)
        self.assertTrue(self.request("/api/session", "POST", {}, alice)[2]["user"]["onboarding_seen"])
        self.assertFalse(self.request("/api/session", "POST", {}, bob)[2]["user"]["onboarding_seen"])
        self.assertEqual(self.request("/api/entries", cookie=alice)[2]["entries"], [])
        self.assertEqual(self.request("/api/onboarding", "POST", {"seen": True})[0], 401)
        self.assertEqual(self.request("/api/onboarding", "POST", {"seen": "yes"}, bob)[0], 400)

    def test_brand_asset_served_and_example_generator_removed(self):
        self.assertEqual(self.request("/ser-logo.png")[0], 200)
        self.assertEqual(self.request("/icon.svg")[0], 404)
        page = self.request("/")[2]
        self.assertIn(b'onboarding-dialog', page)
        self.assertNotIn(b'seed-demo', page)

    def test_csrf_and_host(self):
        self.assertEqual(self.request("/api/session", "POST", {}, extra={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("/api/session", "POST", {}, extra={"X-Ritm-Request": ""})[0], 403)
        self.assertEqual(self.request("/", extra={"Host": "evil.example"})[0], 403)

    def test_static_and_invalid_json(self):
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/../.env")[0], 404)
        self.assertEqual(self.request("/api/entries", "POST", {"title": ""}, self.login())[0], 400)

    def test_recurring_http_range_update_delete_and_isolation(self):
        alice, bob = self.login(), self.login()
        payload = entry(day="2026-10-19", recurrence=dict(frequency="weekly", weekdays=[0], timezone="Asia/Yakutsk"))
        status, _, saved = self.request("/api/entries", "POST", payload, alice)
        self.assertEqual(status, 201)
        route = "/api/entries?from=2026-10-01&to=2026-10-31"
        self.assertEqual(len(self.request(route, cookie=alice)[2]["entries"]), 2)
        self.assertEqual(self.request(route, cookie=bob)[2]["entries"], [])
        path = f"/api/entries/{saved['id']}"
        self.assertEqual(self.request(path, "PUT", {**saved,"done":True}, bob)[0], 404)
        self.assertEqual(self.request(path, "PUT", {**saved,"done":True}, alice)[0], 200)
        self.assertEqual(self.request(path+"?scope=future", "DELETE", {}, bob)[0], 404)
        self.assertEqual(self.request(path+"?scope=future", "DELETE", {}, alice)[0], 200)
        self.assertEqual(len(self.request(route, cookie=alice)[2]["entries"]), 1)  # history kept
        self.assertEqual(self.request("/api/entries?from=2026-01-01&to=2028-01-01",cookie=alice)[0], 400)
        self.assertEqual(self.request("/planner.js")[0], 200)

    def test_export_contains_complete_rules_and_exceptions_for_owner_only(self):
        alice, bob = self.login(), self.login()
        _, _, saved = self.request("/api/entries", "POST", entry(recurrence=dict(frequency="daily",weekdays=[],timezone="UTC")), alice)
        self.request(f"/api/entries/{saved['id']}", "DELETE", {}, alice)
        status, _, result = self.request("/api/export",cookie=alice)
        self.assertEqual(status, 200)
        self.assertEqual(len(result["series"]),1)
        self.assertEqual(result["entries"][0]["archived"],1)
        self.assertNotIn("user_id",json.dumps(result))
        self.assertEqual(self.request("/api/export",cookie=bob)[2]["series"],[])
        self.assertEqual(self.request("/api/export")[0],401)

    def test_no_public_demo(self):
        with self.assertRaises(ValueError):
            make_server(self.store, port=0, public_url="https://example.org", demo=True)

    def test_public_app_requires_signed_telegram_and_keeps_profiles_separate(self):
        server = make_server(self.store, port=0, token="public-test-token", public_url="https://app.example", demo=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, method="GET", data=None, cookie=""):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request(method, path, json.dumps(data) if data is not None else None,
                {"Host":"app.example", "Origin":"https://app.example", "X-Ritm-Request":"1", "Content-Type":"application/json", "Cookie":cookie})
            response=connection.getresponse()
            result=(response.status, response.getheader("Set-Cookie", ""), json.loads(response.read()))
            connection.close()
            return result
        try:
            self.assertEqual(request('/api/entries')[0],401)
            self.assertEqual(request('/api/session','POST',{})[0],401)
            self.assertEqual(request('/api/session','POST',{'init_data':signed_data('wrong-token')})[0],400)
            status,cookie,user=request('/api/session','POST',{'init_data':signed_data('public-test-token',user_id=42), 'user_id':'tg:99'})
            self.assertEqual(status,200)
            self.assertEqual(user['user']['id'],'tg:42')
            self.assertFalse(user['demo'])
            self.assertIn('Secure',cookie)
            self.assertEqual(request('/api/entries',cookie=cookie.split(';')[0])[2]['entries'],[])
            self.store.save('tg:42',entry())
            _,bob_cookie,_=request('/api/session','POST',{'init_data':signed_data('public-test-token',user_id=99)})
            self.assertEqual(request('/api/entries',cookie=bob_cookie.split(';')[0])[2]['entries'],[])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
