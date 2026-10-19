"""Calendar rules with independent, lazily stored occurrences."""
import json
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def checked_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Некорректная дата.")
    return date.fromisoformat(value)


def validate_repeat(data, values):
    if not isinstance(data, dict) or values["kind"] == "note":
        raise ValueError("Повторение доступно для задач и событий.")
    frequency = data.get("frequency")
    days = data.get("weekdays", [])
    if frequency not in ("daily", "weekly") or not isinstance(days, list):
        raise ValueError("Выбери правило повторения.")
    if frequency == "weekly" and (not days or len(days) > 7 or any(type(d) is not int or not 0 <= d <= 6 for d in days)):
        raise ValueError("Выбери дни недели.")
    zone_name = data.get("timezone", "")
    if not isinstance(zone_name, str) or len(zone_name) > 80:
        raise ValueError("Некорректный часовой пояс.")
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Часовой пояс недоступен. Установи tzdata из requirements.txt.") from None
    until = data.get("until") or None
    if until and checked_day(until) < checked_day(values["day"]):
        raise ValueError("Повторение не может закончиться раньше начала.")
    for key in ("starts_at", "ends_at"):
        if values[key] and datetime.fromisoformat(values[key]).astimezone(zone).date().isoformat() != values["day"]:
            raise ValueError("Время должно попадать в выбранный день часового пояса серии.")
    return dict(frequency=frequency, weekdays=sorted(set(days)) if frequency == "weekly" else [],
                timezone=zone_name, until=until)


def applies(series, day):
    key = day.isoformat()
    rule = json.loads(series["rule"])
    return (series["start_day"] <= key and (not series["until_day"] or key <= series["until_day"])
            and (rule["frequency"] == "daily" or day.weekday() in rule["weekdays"]))


def occurrence(series, day):
    from .store import validate_entry
    values = json.loads(series["template"])
    zone = ZoneInfo(series["timezone"])
    for key in ("starts_at", "ends_at"):
        if values[key]:
            local = datetime.fromisoformat(values[key]).astimezone(zone)
            shifted = datetime.combine(day, local.time(), zone)
            utc = shifted.astimezone(timezone.utc)
            # A nonexistent wall-clock time on a DST jump is skipped, never silently moved.
            if utc.astimezone(zone).replace(tzinfo=None) != shifted.replace(tzinfo=None):
                return None
            values[key] = utc.isoformat()
    values.update(day=day.isoformat(), done=0)
    if values["starts_at"] and values["ends_at"] and values["ends_at"] <= values["starts_at"]:
        return None
    values = validate_entry(values)
    values.update(id=f"{series['id']}:{day.isoformat()}", user_id=series["user_id"],
                  series_id=series["id"], occurrence_day=day.isoformat(), archived=0,
                  notified=0, retry_at=0, created_at=series["created_at"])
    return values


def with_rule(entry, series):
    if entry and series:
        entry["recurrence"] = {**json.loads(series["rule"]), "timezone": series["timezone"],
                               "until": series["until_day"], "start": series["start_day"]}
    return entry


def persist(db, values):
    fields = [key for key in values if key != "recurrence"]
    db.execute("INSERT INTO entries (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields)
               + ") ON CONFLICT(id) DO UPDATE SET "
               + ",".join(f"{key}=excluded.{key}" for key in fields if key not in ("id", "user_id")),
               [values[key] for key in fields])


class RecurrenceStore:
    def _get(self, db, user_id, entry_id):
        row = db.execute("SELECT * FROM entries WHERE id=? AND user_id=?", (entry_id, user_id)).fetchone()
        if row:
            if row["archived"]:
                return None
            series = db.execute("SELECT * FROM series WHERE id=? AND user_id=?", (row["series_id"], user_id)).fetchone()
            return with_rule(dict(row), series)
        if not isinstance(entry_id, str) or ":" not in entry_id:
            return None
        sid, key = entry_id.rsplit(":", 1)
        series = db.execute("SELECT * FROM series WHERE id=? AND user_id=?", (sid, user_id)).fetchone()
        if not series:
            return None
        day = checked_day(key)
        return with_rule(occurrence(series, day), series) if applies(series, day) else None

    def get(self, user_id, entry_id):
        with self.connect() as db:
            return self._get(db, user_id, entry_id)

    def export_data(self, user_id):
        with self.connect() as db:
            private = {"user_id", "notified", "retry_at", "reminder_at"}
            records = [{key: row[key] for key in row.keys() if key not in private}
                       for row in db.execute("SELECT * FROM entries WHERE user_id=?", (user_id,))]
            series = []
            for row in db.execute("SELECT * FROM series WHERE user_id=?", (user_id,)):
                series.append(dict(id=row["id"], start=row["start_day"], until=row["until_day"],
                    timezone=row["timezone"], rule=json.loads(row["rule"]), template=json.loads(row["template"])))
            return dict(app="Ser Ритм", format_version=2, exported_at=datetime.now(timezone.utc).isoformat(),
                        entries=records, series=series)

    def entries(self, user_id, start=None, end=None):
        today = date.today()
        start = checked_day(start) if start else today - timedelta(days=7)
        end = checked_day(end) if end else today + timedelta(days=60)
        if end < start or (end - start).days > 400:
            raise ValueError("Выбери период не больше 400 дней.")
        with self.connect() as db:
            rules = {row["id"]: row for row in db.execute("SELECT * FROM series WHERE user_id=?", (user_id,))}
            rows = db.execute("SELECT * FROM entries WHERE user_id=?", (user_id,)).fetchall()
            overrides = {row["id"]: row for row in rows}
            result = {row["id"]: with_rule(dict(row), rules.get(row["series_id"])) for row in rows if not row["archived"]}
            for series in rules.values():
                day = max(start, checked_day(series["start_day"]))
                last = min(end, checked_day(series["until_day"])) if series["until_day"] else end
                while day <= last:
                    if applies(series, day):
                        entry_id = f"{series['id']}:{day.isoformat()}"
                        if entry_id not in overrides:
                            item = occurrence(series, day)
                            if item:
                                result[entry_id] = with_rule(item, series)
                    day += timedelta(days=1)
            return sorted(result.values(), key=lambda e: (e["day"], e["starts_at"] or "", e["created_at"]))

    def _new_series(self, db, user_id, values, repeat):
        rule = validate_repeat(repeat, values)
        sid = secrets.token_hex(12)
        template = {**values, "done": 0}
        db.execute("INSERT INTO series VALUES(?,?,?,?,?,?,?,?)", (sid, user_id, values["day"], rule["until"],
                   rule["timezone"], json.dumps(rule), json.dumps(template), time.time()))
        series = db.execute("SELECT * FROM series WHERE id=?", (sid,)).fetchone()
        day = checked_day(values["day"])
        for offset in range(8):
            candidate = day + timedelta(days=offset)
            if applies(series, candidate):
                item = occurrence(series, candidate)
                if item:
                    return with_rule(item, series)
        raise ValueError("В выбранном периоде нет подходящих дней повторения.")

    def save(self, user_id, data, entry_id=None):
        from .store import validate_entry
        values = validate_entry(data)
        scope = data.get("scope", "one")
        if scope not in ("one", "future"):
            raise ValueError("Неизвестная область изменения.")
        with self.connect() as db:
            old = self._get(db, user_id, entry_id) if entry_id else None
            if entry_id and not old:
                raise LookupError("Запись не найдена.")
            if old and old.get("series_id") and scope == "future":
                if values["day"] != old["occurrence_day"]:
                    raise ValueError("При изменении серии оставь дату выбранного повторения.")
                self._truncate(db, user_id, old)
                if data.get("recurrence"):
                    return self._new_series(db, user_id, values, data["recurrence"])
                old = None
                entry_id = None
            elif old and not old.get("series_id") and data.get("recurrence"):
                db.execute("DELETE FROM entries WHERE id=? AND user_id=?", (old["id"], user_id))
                return self._new_series(db, user_id, values, data["recurrence"])
            elif not old and data.get("recurrence"):
                return self._new_series(db, user_id, values, data["recurrence"])
            changed = not old or any(old[k] != values[k] for k in ("starts_at", "reminder", "done"))
            values.update(id=entry_id or secrets.token_hex(12), user_id=user_id,
                          series_id=old.get("series_id") if old else None,
                          occurrence_day=old.get("occurrence_day") if old else None,
                          archived=0, created_at=old["created_at"] if old else time.time(),
                          notified=0 if changed else old["notified"], retry_at=0 if changed else old["retry_at"])
            if old and old.get("series_id") and values["kind"] == "note":
                raise ValueError("Повторяющаяся запись должна остаться задачей или событием.")
            persist(db, values)
            return self._get(db, user_id, values["id"])

    def _truncate(self, db, user_id, old):
        before = (checked_day(old["occurrence_day"]) - timedelta(days=1)).isoformat()
        db.execute("UPDATE series SET until_day=? WHERE id=? AND user_id=?", (before, old["series_id"], user_id))
        # Keep past occurrences and completion history. Remove only future plans/exceptions.
        db.execute("DELETE FROM entries WHERE series_id=? AND user_id=? AND occurrence_day>=? AND done=0",
                   (old["series_id"], user_id, old["occurrence_day"]))

    def delete(self, user_id, entry_id, scope="one"):
        if scope not in ("one", "future"):
            raise ValueError("Неизвестная область удаления.")
        with self.connect() as db:
            old = self._get(db, user_id, entry_id)
            if not old:
                return False
            if old.get("series_id"):
                if scope == "future":
                    self._truncate(db, user_id, old)
                else:
                    old["archived"] = 1
                    persist(db, old)  # Tombstone prevents the rule from bringing it back.
            else:
                db.execute("DELETE FROM entries WHERE id=? AND user_id=?", (entry_id, user_id))
            return True

    def prepare_reminders(self, now):
        with self.connect() as db:
            # Materialize only upcoming reminders; normal viewing doesn't fill the database.
            for series in db.execute("SELECT s.* FROM series s JOIN users u ON u.id=s.user_id WHERE u.chat_id IS NOT NULL").fetchall():
                template = json.loads(series["template"])
                if template["reminder"] < 0:
                    continue
                local_day = datetime.fromtimestamp(now, ZoneInfo(series["timezone"])).date()
                for offset in (-1, 0, 1):
                    day = local_day + timedelta(days=offset)
                    if not applies(series, day):
                        continue
                    item = occurrence(series, day)
                    if item and item["reminder_at"] <= now and not db.execute("SELECT 1 FROM entries WHERE id=?", (item["id"],)).fetchone():
                        persist(db, item)
