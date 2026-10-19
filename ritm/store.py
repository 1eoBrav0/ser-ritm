import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .recurrence import RecurrenceStore


class Store(RecurrenceStore):
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, chat_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entries (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    kind TEXT NOT NULL, title TEXT NOT NULL, details TEXT NOT NULL,
                    day TEXT NOT NULL, starts_at TEXT, duration INTEGER NOT NULL,
                    priority TEXT NOT NULL, category TEXT NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0, reminder INTEGER NOT NULL DEFAULT -1,
                    reminder_at REAL, notified INTEGER NOT NULL DEFAULT 0,
                    retry_at REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS entries_user_day ON entries(user_id, day);
                CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY);
            """)
            if "onboarding_seen" not in {row["name"] for row in db.execute("PRAGMA table_info(users)")}:
                db.execute("ALTER TABLE users ADD COLUMN onboarding_seen INTEGER NOT NULL DEFAULT 0")
            if "archived" not in {row["name"] for row in db.execute("PRAGMA table_info(entries)")}:
                db.execute("ALTER TABLE entries ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            if "ends_at" not in {row["name"] for row in db.execute("PRAGMA table_info(entries)")}:
                db.execute("ALTER TABLE entries ADD COLUMN ends_at TEXT")
                previous_events = db.execute("SELECT id,starts_at,duration FROM entries WHERE kind='event' AND starts_at IS NOT NULL").fetchall()
                for event in previous_events:
                    end = datetime.fromisoformat(event["starts_at"]) + timedelta(minutes=event["duration"])
                    db.execute("UPDATE entries SET ends_at=? WHERE id=?", (end.isoformat(), event["id"]))
            columns = {row["name"] for row in db.execute("PRAGMA table_info(entries)")}
            for column in ("series_id", "occurrence_day"):
                if column not in columns:
                    db.execute(f"ALTER TABLE entries ADD COLUMN {column} TEXT")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS series (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    start_day TEXT NOT NULL, until_day TEXT, timezone TEXT NOT NULL,
                    rule TEXT NOT NULL, template TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS series_user ON series(user_id);
                CREATE UNIQUE INDEX IF NOT EXISTS entries_occurrence ON entries(series_id, occurrence_day);
            """)
            if not db.execute("SELECT 1 FROM migrations WHERE name='archive_old_examples'").fetchone():
                # Only our unchanged, known test records from the first preview qualify.
                examples = [
                    ("Разобраться с функциями в Python", "Повторить аргументы и возвращаемые значения. Решить три небольшие задачи.", "task", "study", "high", 60, "2026-10-18T01:00:00+00:00", 1),
                    ("Практика по программированию", "Взять ноутбук и подготовить вопросы.", "event", "study", "normal", 90, "2026-10-18T03:30:00+00:00", 0),
                    ("Прогуляться без телефона", "Немного воздуха между делами.", "task", "personal", "normal", 30, "2026-10-18T08:00:00+00:00", 0),
                    ("Набросать идеи для портфолио", "Выбрать одну задачу, которую действительно хочется решить.", "task", "work", "normal", 60, None, 0),
                    ("Мысли на потом", "Не пытаться успеть всё за день.\n\nСделать одну вещь, которую захочется показать другим.", "note", "personal", "normal", 60, None, 0),
                    ("Проверить сохранение ежедневника", "Тестовая запись: после обновления страницы она должна остаться.", "task", "personal", "normal", 60, None, 0),
                ]
                db.executemany("""UPDATE entries SET archived=1
                    WHERE title=? AND details=? AND kind=? AND category=?
                    AND priority=? AND duration=? AND starts_at IS ? AND done=?
                    AND day='2026-10-18' AND user_id LIKE 'demo:%' AND reminder=-1""", examples)
                db.execute("INSERT INTO migrations VALUES('archive_old_examples')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def session(self, user_id, name):
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            db.execute("INSERT INTO users(id,name) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name", (user_id, name[:80]))
            db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (digest, user_id, int(time.time()) + 86400 * 7))
        return token

    def user(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            row = db.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?", (digest, time.time())).fetchone()
        return dict(row) if row else None

    def link_chat(self, telegram_id, name):
        with self.connect() as db:
            db.execute("INSERT INTO users(id,name,chat_id) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET chat_id=excluded.chat_id", (f"tg:{telegram_id}", name[:80], telegram_id))

    def unlink_chat(self, telegram_id):
        with self.connect() as db:
            db.execute("UPDATE users SET chat_id=NULL WHERE id=?", (f"tg:{telegram_id}",))

    def finish_onboarding(self, user_id):
        with self.connect() as db:
            db.execute("UPDATE users SET onboarding_seen=1 WHERE id=?", (user_id,))

    def pending(self, now):
        self.prepare_reminders(now)
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT e.*, u.chat_id FROM entries e
                JOIN users u ON u.id=e.user_id WHERE e.notified=0 AND e.done=0 AND e.archived=0
                AND e.reminder_at<=? AND e.retry_at<=? AND u.chat_id IS NOT NULL""", (now, now))]

    def mark_notification(self, entry_id, sent, now):
        with self.connect() as db:
            db.execute("UPDATE entries SET notified=?, retry_at=? WHERE id=?", (int(sent), 0 if sent else now + 120, entry_id))


def validate_entry(data):
    if not isinstance(data, dict):
        raise ValueError("Ожидается объект записи.")
    def text(key, limit, default=""):
        value = data.get(key, default)
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"Некорректное поле: {key}.")
        return value.strip()
    title = text("title", 160)
    if not title:
        raise ValueError("Добавь название.")
    kind = text("kind", 10, "task")
    category = text("category", 20, "personal")
    priority = text("priority", 10, "normal")
    if kind not in ("task", "event", "note") or category not in ("personal", "study", "work") or priority not in ("low", "normal", "high"):
        raise ValueError("Неизвестный тип, категория или приоритет.")
    day = text("day", 10)
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("Некорректная дата.")
    def timestamp(key):
        value = data.get(key)
        if not value:
            return None
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError("Некорректное время.")
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("У времени должен быть часовой пояс.")
        return dt.astimezone(timezone.utc)
    start = timestamp("starts_at")
    end = timestamp("ends_at")
    reminder = data.get("reminder", -1)
    duration = data.get("duration", 60)
    if type(reminder) is not int or reminder not in (-1, 0, 5, 15, 30, 60):
        raise ValueError("Некорректное напоминание.")
    if end is not None:
        if start is None:
            raise ValueError("Для времени окончания укажи начало.")
        seconds = (end - start).total_seconds()
        if not 60 <= seconds <= 86400 or seconds % 60:
            raise ValueError("Конец должен быть позже начала; интервал — от минуты до суток.")
        duration = int(seconds / 60)
    elif type(duration) is not int or not 1 <= duration <= 1440:
        raise ValueError("Продолжительность: от 1 до 1440 минут.")
    elif kind == "event" and start:
        # Preserve API compatibility with earlier events defined by duration.
        end = start + timedelta(minutes=duration)
    if kind == "event" and not start:
        raise ValueError("Для события укажи время.")
    if reminder >= 0 and not start:
        raise ValueError("Для напоминания укажи время.")
    done = data.get("done", False)
    if type(done) not in (int, bool) or done not in (0, 1):
        raise ValueError("Некорректный статус.")
    return dict(kind=kind, title=title, details=text("details", 5000), day=day,
                starts_at=start.isoformat() if start else None, ends_at=end.isoformat() if end else None,
                duration=duration, category=category, priority=priority,
                done=int(done), reminder=reminder,
                reminder_at=start.timestamp() - reminder * 60 if start and reminder >= 0 else None)
