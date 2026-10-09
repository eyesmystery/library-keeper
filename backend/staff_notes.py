"""Edits staff want on a book, written in the book's own card (Ibrahim, 2026-10-08).

In the admin book card staff write, in their own words, whatever should be changed about the
book («العنوان الصحيح كذا»، «المؤلف غلط»، «الغلاف لطبعة ثانية»). The note is saved with the
book, the writer's name and the time; it never changes the book. All notes are gathered in
«البلاغات» → «تعديلات الموظفين», where whoever fixes the data reads them and marks each handled.
"""
import re
from datetime import datetime, timezone

from models import get_connection

STATUSES = ("open", "done")
MAX_TEXT = 1000
MIN_TEXT = 2
PAGE = 100
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS staff_notes (
        note_id INTEGER PRIMARY KEY AUTOINCREMENT, book_id INTEGER NOT NULL, book_title TEXT,
        note TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open', handled_by TEXT, handled_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staff_notes_book ON staff_notes(book_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staff_notes_status ON staff_notes(status)")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value):
    if not isinstance(value, str):
        return ""
    text = _CONTROL.sub("", value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_TEXT]


def add(book, note, by=None):
    """book: a row/dict with book_id and title. Raises ValueError when the note is empty."""
    note = clean_text(note)
    if len(note) < MIN_TEXT:
        raise ValueError("اكتب التعديل المطلوب أولًا")
    conn = get_connection()
    try:
        ensure_table(conn)
        cur = conn.execute(
            "INSERT INTO staff_notes (book_id, book_title, note, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (book["book_id"], book["title"], note, by, _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def for_book(book_id):
    """The notes of one book for its card: open ones first, newest first."""
    conn = get_connection()
    try:
        ensure_table(conn)
        return [dict(r) for r in conn.execute(
            "SELECT * FROM staff_notes WHERE book_id = ? ORDER BY (status = 'open') DESC, note_id DESC", (book_id,))]
    finally:
        conn.close()


def list_rows(status="open", offset=0):
    """All notes for the «تعديلات الموظفين» list -> (rows, total). The book's current title and
    author are shown; a note whose book was deleted keeps the title it was written under."""
    where, args = ("WHERE n.status = ?", [status]) if status in STATUSES else ("", [])
    conn = get_connection()
    try:
        ensure_table(conn)
        rows = []
        for r in conn.execute(
                f"""SELECT n.*, b.title AS current_title, b.author AS book_author, b.cover_url
                    FROM staff_notes n LEFT JOIN books b ON b.book_id = n.book_id {where}
                    ORDER BY (n.status = 'open') DESC, n.note_id DESC LIMIT ? OFFSET ?""",
                args + [PAGE, max(0, int(offset or 0))]):
            d = dict(r)
            d["book_exists"] = d["current_title"] is not None
            d["book_title"] = d.pop("current_title") or d["book_title"]
            rows.append(d)
        total = conn.execute(f"SELECT COUNT(*) FROM staff_notes n {where}", args).fetchone()[0]
        return rows, total
    finally:
        conn.close()


def counts():
    conn = get_connection()
    try:
        ensure_table(conn)
        return {"open": conn.execute("SELECT COUNT(*) FROM staff_notes WHERE status = 'open'").fetchone()[0],
                "all": conn.execute("SELECT COUNT(*) FROM staff_notes").fetchone()[0]}
    finally:
        conn.close()


def set_status(note_id, status, by=None):
    """-> the updated note as a dict, or None when it does not exist."""
    if status not in STATUSES:
        raise ValueError("حالة غير معروفة")
    conn = get_connection()
    try:
        ensure_table(conn)
        if conn.execute("SELECT 1 FROM staff_notes WHERE note_id = ?", (int(note_id),)).fetchone() is None:
            return None
        done = status == "done"
        conn.execute("UPDATE staff_notes SET status = ?, handled_by = ?, handled_at = ? WHERE note_id = ?",
                     (status, by if done else None, _now() if done else None, int(note_id)))
        conn.commit()
        return dict(conn.execute("SELECT * FROM staff_notes WHERE note_id = ?", (int(note_id),)).fetchone())
    finally:
        conn.close()
