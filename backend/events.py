"""Library events (Ibrahim, 2026-10-08): «قسم خاص حق الفعاليات».

Staff add an event in the admin («الفعاليات»): name, description, date, time, an optional picture
(of the event or of the book), and optionally a book from the catalog when the event is about a
book. Upcoming events are shown on the visitor's start page under «فعالياتنا»; tapping one opens
its page. An event is shown until the end of its day, then it is kept in the admin list as past.
"""
import os
import re
import time
from datetime import date, datetime, timezone

import covers
from models import get_connection

IMAGES_DIR = os.environ.get("LIBRARY_KEEPER_EVENT_IMAGES_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "event_images")
MAX_TITLE, MAX_TEXT = 120, 2000
_FILE_RE = re.compile(r"^\d+\.(jpg|png|webp)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class EventError(ValueError):
    pass


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT,
        event_date TEXT NOT NULL, event_time TEXT, end_time TEXT, image_url TEXT, book_id INTEGER,
        active INTEGER NOT NULL DEFAULT 1, created_by TEXT, created_at TEXT NOT NULL,
        updated_by TEXT, updated_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date)")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today():
    """The library's date (Riyadh, UTC+3): an event stays on the start page until its day ends there."""
    return datetime.fromtimestamp(time.time() + 3 * 3600, timezone.utc).date().isoformat()


def _text(value, limit):
    if not isinstance(value, str):
        return ""
    text = _CONTROL.sub("", value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:limit]


MAX_ID = 2 ** 63 - 1  # SQLite's largest integer; a bigger number makes the driver raise OverflowError


def safe_id(value):
    """A record number from staff input -> int in SQLite's range, or None. Accepts an int or a string
    of ASCII digits only (no booleans, floats, lists, signs, or other scripts' digits)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not re.fullmatch(r"[0-9]{1,19}", value):
            return None
        value = int(value)
    if not isinstance(value, int) or not (0 < value <= MAX_ID):
        return None
    return value


def validate(data, book_exists):
    """-> clean fields, or EventError with an Arabic reason shown to staff."""
    title = _text(data.get("title"), MAX_TITLE)
    if len(title) < 2:
        raise EventError("اكتب اسم الفعالية")
    day = str(data.get("event_date") or "").strip()
    if not _DATE_RE.match(day):
        raise EventError("اختر تاريخ الفعالية")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise EventError("التاريخ غير صحيح")
    clean = {"title": title, "description": _text(data.get("description"), MAX_TEXT), "event_date": day}
    for key in ("event_time", "end_time"):
        v = str(data.get(key) or "").strip()
        if v and not _TIME_RE.match(v):
            raise EventError("الوقت غير صحيح")
        clean[key] = v or None
    if clean["end_time"] and not clean["event_time"]:
        raise EventError("اكتب وقت البداية قبل وقت النهاية")
    # An event is one day (it is shown until its day ends); an end at or before the start would mean
    # the next day, which we do not support (audit 2026-10-08). "HH:MM" strings compare as times.
    if clean["end_time"] and clean["end_time"] <= clean["event_time"]:
        raise EventError("وقت النهاية لازم يكون بعد وقت البداية في نفس اليوم")
    book_id = data.get("book_id")
    if book_id in ("", None):
        clean["book_id"] = None
    else:
        book_id = safe_id(book_id)
        if book_id is None:
            raise EventError("رقم الكتاب غير صالح")
        if not book_exists(book_id):
            raise EventError("الكتاب غير موجود")
        clean["book_id"] = book_id
    return clean


def _row(r):
    d = dict(r)
    d["past"] = d["event_date"] < today()
    return d


def get(event_id):
    if safe_id(event_id) is None:  # /api/admin/events/<huge number> is "not found", not a server error
        return None
    conn = get_connection()
    try:
        ensure_table(conn)
        r = conn.execute("SELECT * FROM events WHERE event_id = ?", (int(event_id),)).fetchone()
        return _row(r) if r else None
    finally:
        conn.close()


def list_all():
    """Admin list: upcoming first (soonest first), then past (latest first)."""
    conn = get_connection()
    try:
        ensure_table(conn)
        rows = [_row(r) for r in conn.execute("SELECT * FROM events")]
    finally:
        conn.close()
    upcoming = sorted([r for r in rows if not r["past"]], key=lambda r: (r["event_date"], r["event_time"] or ""))
    past = sorted([r for r in rows if r["past"]], key=lambda r: (r["event_date"], r["event_time"] or ""), reverse=True)
    return upcoming + past


def upcoming(limit=12):
    """Visitor start page: active events from today on, soonest first."""
    conn = get_connection()
    try:
        ensure_table(conn)
        rows = conn.execute("SELECT * FROM events WHERE active = 1 AND event_date >= ? "
                            "ORDER BY event_date, COALESCE(event_time, '') LIMIT ?", (today(), int(limit))).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def add(clean, by=None):
    conn = get_connection()
    try:
        ensure_table(conn)
        cur = conn.execute("INSERT INTO events (title, description, event_date, event_time, end_time, book_id, active, "
                           "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                           (clean["title"], clean["description"], clean["event_date"], clean["event_time"],
                            clean["end_time"], clean["book_id"], by, _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update(event_id, clean, by=None, active=None):
    conn = get_connection()
    try:
        ensure_table(conn)
        fields = dict(clean, updated_by=by, updated_at=_now())
        if active is not None:
            fields["active"] = 1 if active else 0
        sets = ", ".join(f"{k} = ?" for k in fields)
        n = conn.execute(f"UPDATE events SET {sets} WHERE event_id = ?", list(fields.values()) + [int(event_id)]).rowcount
        conn.commit()
        return n > 0
    finally:
        conn.close()


def delete(event_id):
    ev = get(event_id)
    if not ev:
        return False
    _remove_files(event_id)
    conn = get_connection()
    try:
        conn.execute("DELETE FROM events WHERE event_id = ?", (int(event_id),))
        conn.commit()
    finally:
        conn.close()
    return True


# ---------------------------------------------------------------- picture
def image_path(filename):
    if not _FILE_RE.match(filename or ""):
        return None
    path = os.path.join(IMAGES_DIR, filename)
    return path if os.path.isfile(path) else None


def _remove_files(event_id):
    for ext in ("jpg", "png", "webp"):
        p = os.path.join(IMAGES_DIR, f"{int(event_id)}.{ext}")
        if os.path.exists(p):
            os.remove(p)


def save_image(event_id, data):
    """Same checks as a book cover (JPEG / PNG / WebP, up to 5 MB), but any shape: an event poster
    may be wide. -> the image URL."""
    if not data or len(data) < covers.MIN_BYTES:
        raise EventError("الصورة صغيرة جدًا أو فارغة")
    if len(data) > covers.MAX_BYTES:
        raise EventError("الصورة أكبر من ٥ ميجابايت")
    ext = covers.image_type(data)
    if not ext:
        raise EventError("الملف لازم يكون صورة JPG أو PNG أو WebP")
    os.makedirs(IMAGES_DIR, exist_ok=True)
    _remove_files(event_id)
    name = f"{int(event_id)}.{ext}"
    tmp = os.path.join(IMAGES_DIR, name + ".part")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, os.path.join(IMAGES_DIR, name))
    url = f"/event-images/{name}?v={int(time.time())}"
    conn = get_connection()
    try:
        conn.execute("UPDATE events SET image_url = ?, updated_at = ? WHERE event_id = ?", (url, _now(), int(event_id)))
        conn.commit()
    finally:
        conn.close()
    return url


def remove_image(event_id):
    _remove_files(event_id)
    conn = get_connection()
    try:
        conn.execute("UPDATE events SET image_url = NULL, updated_at = ? WHERE event_id = ?", (_now(), int(event_id)))
        conn.commit()
    finally:
        conn.close()


def has_image(ev):
    url = ev.get("image_url") or ""
    return url.startswith("/event-images/") and image_path(url.split("/event-images/", 1)[1].split("?", 1)[0]) is not None


# «أضف إلى التقويم» on the event page (Ibrahim, 2026-10-09): an .ics file the phone's calendar opens.
# Times are Riyadh time (UTC+3, no daylight saving), written in UTC; no time -> an all-day event;
# no end time -> one hour.
CALENDAR_LOCATION = "مكتبة ثلاث"


def _ics_text(value):
    return (str(value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\n").replace("\n", "\\n"))


def _fold(line):
    """RFC 5545: lines of at most 75 octets, continued with a space (never inside a UTF-8 character)."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def calendar_times(ev):
    """-> ("DATE", start, end) or ("UTC", start, end) as iCalendar values."""
    from datetime import date, timedelta
    d = date.fromisoformat(ev["event_date"])
    if not ev.get("event_time"):
        return "DATE", d.strftime("%Y%m%d"), (d + timedelta(days=1)).strftime("%Y%m%d")
    h, m = (int(x) for x in ev["event_time"].split(":")[:2])
    start = datetime(d.year, d.month, d.day, h, m, tzinfo=timezone(timedelta(hours=3)))
    if ev.get("end_time"):
        eh, em = (int(x) for x in ev["end_time"].split(":")[:2])
        end = start.replace(hour=eh, minute=em)
        if end <= start:
            end += timedelta(days=1)
    else:
        end = start + timedelta(hours=1)
    fmt = lambda x: x.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "UTC", fmt(start), fmt(end)


def ics(ev, book_title=None):
    kind, start, end = calendar_times(ev)
    desc = ev.get("description") or ""
    if book_title:
        desc = (desc + "\n\n" if desc else "") + "عن كتاب «" + book_title + "»"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Thalath//Manar//AR", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "BEGIN:VEVENT", f"UID:event-{ev['event_id']}@thalath-smart", f"DTSTAMP:{stamp}",
             (f"DTSTART;VALUE=DATE:{start}" if kind == "DATE" else f"DTSTART:{start}"),
             (f"DTEND;VALUE=DATE:{end}" if kind == "DATE" else f"DTEND:{end}"),
             "SUMMARY:" + _ics_text(ev.get("title")), "LOCATION:" + _ics_text(CALENDAR_LOCATION)]
    if desc:
        lines.append("DESCRIPTION:" + _ics_text(desc))
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"
