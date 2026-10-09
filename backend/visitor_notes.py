"""Visitor reports about a book's information, and visitor suggestions (Ibrahim, 2026-10-03).

Two kinds of notes land in one inbox shown in the admin page («البلاغات»):
  - report     : a visitor tapped «بلّغ عن خطأ» on a book page, picked what is wrong and
                 optionally wrote a short note. Linked to the book.
  - suggestion : a visitor tapped «عندك اقتراح؟» in the chat and wrote a short note.

Anonymous like the rest of the visitor data: no name, no conversation id, no network address.
The only free text stored is what the visitor typed in the note box itself.
A report never changes the book: staff read it, fix the book if needed, and mark it handled.
"""
import re
from datetime import datetime, timezone

from models import get_connection

KINDS = ("report", "suggestion")
# what the visitor says is wrong (labels are for the admin page; the visitor page has its own wording)
REASONS = {
    "title_author": "العنوان أو المؤلف",
    "cover": "الغلاف",
    "summary": "النبذة أو الوصف",
    "details": "التصنيف أو عدد الصفحات أو اللغة",
    "location": "المكان أو التوفر",
    "other": "شيء آخر",
}
STATUSES = ("open", "done")
MAX_TEXT = 500
MIN_SUGGESTION = 3
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS visitor_notes (
            note_id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            book_id INTEGER,
            book_title TEXT,
            reason TEXT,
            note TEXT,
            language TEXT,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            handled_by TEXT,
            handled_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_visitor_notes_status ON visitor_notes(status, kind)")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value):
    """Visitor text: a string, control characters removed, trimmed, capped at MAX_TEXT."""
    if not isinstance(value, str):
        return ""
    text = _CONTROL.sub("", value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_TEXT]


def add_report(book, reason, note="", language="ar"):
    """book: a row/dict with book_id and title. Raises ValueError on an invalid reason."""
    if not isinstance(reason, str) or reason not in REASONS:
        raise ValueError("invalid reason")
    note = clean_text(note)
    if reason == "other" and not note:
        raise ValueError("note required")
    return _insert("report", book["book_id"], book["title"], reason, note, language)


def add_suggestion(note, language="ar"):
    note = clean_text(note)
    if len(note) < MIN_SUGGESTION:
        raise ValueError("note required")
    return _insert("suggestion", None, None, None, note, language)


def _insert(kind, book_id, title, reason, note, language):
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO visitor_notes (kind, book_id, book_title, reason, note, language, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (kind, book_id, title, reason, note or None, "en" if language == "en" else "ar", _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def counts():
    conn = get_connection()
    try:
        out = {"report": {"open": 0, "done": 0}, "suggestion": {"open": 0, "done": 0}}
        for row in conn.execute("SELECT kind, status, COUNT(*) n FROM visitor_notes GROUP BY kind, status"):
            if row["kind"] in out and row["status"] in STATUSES:
                out[row["kind"]][row["status"]] = row["n"]
        out["open"] = out["report"]["open"] + out["suggestion"]["open"]
        return out
    finally:
        conn.close()


PAGE = 100   # notes per page in the admin inbox; older ones are reached with ?offset=


def total(kind="report", status="open"):
    """How many notes the list for this kind and status holds in all (for paging)."""
    if kind not in KINDS:
        kind = "report"
    q, args = "SELECT COUNT(*) FROM visitor_notes WHERE kind = ?", [kind]
    if status in STATUSES:
        q += " AND status = ?"
        args.append(status)
    conn = get_connection()
    try:
        return conn.execute(q, args).fetchone()[0]
    finally:
        conn.close()


def list_notes(kind="report", status="open", limit=PAGE, offset=0):
    if kind not in KINDS:
        kind = "report"
    q = """SELECT n.*, b.title AS current_title, b.author AS book_author, b.cover_url
           FROM visitor_notes n LEFT JOIN books b ON b.book_id = n.book_id WHERE n.kind = ?"""
    args = [kind]
    if status in STATUSES:
        q += " AND n.status = ?"
        args.append(status)
    # open ones oldest first is not useful on a phone: newest first, open before handled
    q += " ORDER BY (n.status = 'open') DESC, n.note_id DESC LIMIT ? OFFSET ?"
    args += [max(1, min(int(limit), 300)), max(0, int(offset))]
    conn = get_connection()
    try:
        rows = []
        for r in conn.execute(q, args):
            d = dict(r)
            d["book_exists"] = d["book_id"] is not None and d["current_title"] is not None
            d["book_title"] = d.pop("current_title") or d["book_title"]
            d["reason_label"] = REASONS.get(d["reason"]) if d["reason"] else None
            rows.append(d)
        return rows
    finally:
        conn.close()


def set_status(note_id, status, username=None):
    """-> the updated note as a dict, or None when it does not exist."""
    if status not in STATUSES:
        raise ValueError("invalid status")
    conn = get_connection()
    try:
        row = conn.execute("SELECT note_id FROM visitor_notes WHERE note_id = ?", (int(note_id),)).fetchone()
        if row is None:
            return None
        done = status == "done"
        conn.execute("UPDATE visitor_notes SET status = ?, handled_by = ?, handled_at = ? WHERE note_id = ?",
                     (status, username if done else None, _now() if done else None, int(note_id)))
        conn.commit()
        return dict(conn.execute("SELECT * FROM visitor_notes WHERE note_id = ?", (int(note_id),)).fetchone())
    finally:
        conn.close()
