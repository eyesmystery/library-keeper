"""«ملاحظات من المطور» and the admin «التنبيهات» box (QADFIX4, Ibrahim 2026-10-16 21:4x):

«اضف خانة في الادارة للتنبيهات اذا في شيء جديد يحتاج مراجعة او بلاغ او اقتراح. اضف خانة تظهر ايضا "ملاحظات من المطور"
يتابعها الموظفين واللي يخلصها يعلم عليها انه تم اصلاحها وتتسجل طبعا كل شيء في الlog. اضف من ضمن الملاحظات من المطور
"أرجو مراجعة كل كتب دار أسفار" واذا ضغط عالملاحظة يفتح له قائمة بكتب دار أسفار»

The developer's notes are written in backend/dev_notes.json, which travels with the code in every update. On the first
request after an update, a note whose `ref` is not yet in the library's table is added (open). A note already there is
never re-opened or rewritten, so what staff marked done stays done. Marking done / reopening is written to audit_log by
the caller (app.py) and the note keeps who did it, when, and their comment; the inbox export carries them back.
"""
import json
import os
import re
from datetime import datetime, timezone

from keyword_prefs import normalize
from models import get_connection

SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev_notes.json")
STATUSES = ("open", "done")
MAX_COMMENT = 600
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS dev_notes (
        note_id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT UNIQUE NOT NULL, title TEXT NOT NULL, body TEXT,
        action TEXT, created_at TEXT NOT NULL, added_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open', handled_by TEXT, handled_at TEXT, handled_note TEXT)""")


def _seed_notes():
    try:
        with open(SEED, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [n for n in (data.get("notes") or []) if isinstance(n, dict) and n.get("ref") and n.get("title")]


def _insert_new(conn, notes):
    ensure_table(conn)
    have = {r[0] for r in conn.execute("SELECT ref FROM dev_notes")}
    added = 0
    for n in notes:
        if not isinstance(n, dict) or not n.get("ref") or not n.get("title") or str(n["ref"])[:80] in have:
            continue
        action = n.get("action") if isinstance(n.get("action"), dict) and n["action"].get("type") == "books" else None
        conn.execute("INSERT INTO dev_notes (ref, title, body, action, created_at, added_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (str(n["ref"])[:80], str(n["title"])[:200], str(n.get("body") or "")[:3000],
                      json.dumps(action, ensure_ascii=False) if action else None,
                      str(n.get("created") or _now())[:40], _now()))
        have.add(str(n["ref"])[:80])
        added += 1
    if added:
        conn.commit()
    return added


def sync(conn):
    """Add the developer notes of this version that the library does not have yet. -> number added."""
    return _insert_new(conn, _seed_notes())


def add_notes(notes):
    """Notes that came through the signed channel (backend/channel.py, AUTOUPD): new refs only. -> number added."""
    conn = get_connection()
    try:
        return _insert_new(conn, notes or [])
    finally:
        conn.close()


def _row(r):
    d = dict(r)
    try:
        d["action"] = json.loads(d["action"]) if d.get("action") else None
    except ValueError:
        d["action"] = None
    if d["action"]:
        d["action"] = {k: v for k, v in d["action"].items() if k in ("type", "label")}   # the filter stays on the server
    return d


def list_rows(status="all"):
    conn = get_connection()
    try:
        sync(conn)
        where, args = ("WHERE status = ?", [status]) if status in STATUSES else ("", [])
        return [_row(r) for r in conn.execute(
            f"SELECT * FROM dev_notes {where} ORDER BY (status = 'open') DESC, created_at DESC, note_id DESC", args)]
    finally:
        conn.close()


def counts(conn=None):
    own = conn is None
    conn = conn or get_connection()
    try:
        sync(conn)
        return {"open": conn.execute("SELECT COUNT(*) FROM dev_notes WHERE status = 'open'").fetchone()[0],
                "all": conn.execute("SELECT COUNT(*) FROM dev_notes").fetchone()[0]}
    finally:
        if own:
            conn.close()


def clean_comment(value):
    if not isinstance(value, str):
        return None
    t = _CONTROL.sub("", value).strip()
    return t[:MAX_COMMENT] or None


def set_status(note_id, status, by=None, comment=None):
    """-> the updated row, or None when there is no such note. Raises ValueError for an unknown status."""
    if status not in STATUSES:
        raise ValueError("حالة غير معروفة")
    conn = get_connection()
    try:
        sync(conn)
        if conn.execute("SELECT 1 FROM dev_notes WHERE note_id = ?", (note_id,)).fetchone() is None:
            return None
        if status == "done":
            conn.execute("UPDATE dev_notes SET status = 'done', handled_by = ?, handled_at = ?, handled_note = ? WHERE note_id = ?",
                         (by, _now(), clean_comment(comment), note_id))
        else:
            conn.execute("UPDATE dev_notes SET status = 'open', handled_by = NULL, handled_at = NULL, handled_note = NULL WHERE note_id = ?",
                         (note_id,))
        conn.commit()
        return _row(conn.execute("SELECT * FROM dev_notes WHERE note_id = ?", (note_id,)).fetchone())
    finally:
        conn.close()


def _publisher_key(text):
    return " ".join(normalize(text or "").replace("دار ", " ").split())


def books_for(note_id):
    """-> (note row, [books]) for a note whose action opens a list of books, else (row, None) / (None, None)."""
    conn = get_connection()
    try:
        sync(conn)
        r = conn.execute("SELECT * FROM dev_notes WHERE note_id = ?", (note_id,)).fetchone()
        if r is None:
            return None, None
        try:
            action = json.loads(r["action"]) if r["action"] else None
        except ValueError:
            action = None
        if not action or action.get("type") != "books":
            return _row(r), None
        wanted = {_publisher_key(p) for p in action.get("publisher_any") or [] if p}
        wanted.discard("")
        # a note about named books («أرجو تأكيد بيانات هذا الكتاب»): their ids (MGR 2026-10-17, library inbox §1.1)
        ids = {int(i) for i in action.get("book_ids") or [] if str(i).isdigit()}
        out = []
        for b in conn.execute("SELECT book_id, title, author, publisher, translator, cover_url, quantity, active, genre, "
                              "research_status FROM books WHERE active = 1 ORDER BY book_id"):
            if (wanted and _publisher_key(b["publisher"]) in wanted) or b["book_id"] in ids:
                out.append(dict(b))
        return _row(r), out
    finally:
        conn.close()
