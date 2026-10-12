"""«اقتراحات الموظفين» (Ibrahim, 2026-10-09): staff write any general suggestion about the app
(not about one book) in «البلاغات». Each one is kept with the writer's name and time, logged in
audit_log, listed in the inbox, and goes into the downloaded inbox file for Ibrahim.
"""
from datetime import datetime, timezone

import staff_notes
from models import get_connection

STATUSES = ("open", "done")
MIN_TEXT = 3
PAGE = 100


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS staff_ideas (
        idea_id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open', handled_by TEXT, handled_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_staff_ideas_status ON staff_ideas(status)")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add(text, by=None):
    """-> (idea_id, clean text). Raises ValueError when the text is empty."""
    text = staff_notes.clean_text(text)
    if len(text) < MIN_TEXT:
        raise ValueError("اكتب الاقتراح أولًا")
    conn = get_connection()
    try:
        ensure_table(conn)
        cur = conn.execute("INSERT INTO staff_ideas (text, created_by, created_at) VALUES (?, ?, ?)", (text, by, _now()))
        conn.commit()
        return cur.lastrowid, text
    finally:
        conn.close()


def list_rows(status="open", offset=0):
    where, args = ("WHERE status = ?", [status]) if status in STATUSES else ("", [])
    conn = get_connection()
    try:
        ensure_table(conn)
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM staff_ideas {where} ORDER BY (status = 'open') DESC, idea_id DESC LIMIT ? OFFSET ?",
            args + [PAGE, max(0, int(offset or 0))])]
        total = conn.execute(f"SELECT COUNT(*) FROM staff_ideas {where}", args).fetchone()[0]
        return rows, total
    finally:
        conn.close()


def counts():
    conn = get_connection()
    try:
        ensure_table(conn)
        return {"open": conn.execute("SELECT COUNT(*) FROM staff_ideas WHERE status = 'open'").fetchone()[0],
                "all": conn.execute("SELECT COUNT(*) FROM staff_ideas").fetchone()[0]}
    finally:
        conn.close()


def set_status(idea_id, status, by=None):
    if status not in STATUSES:
        raise ValueError("حالة غير معروفة")
    conn = get_connection()
    try:
        ensure_table(conn)
        if conn.execute("SELECT 1 FROM staff_ideas WHERE idea_id = ?", (int(idea_id),)).fetchone() is None:
            return None
        done = status == "done"
        conn.execute("UPDATE staff_ideas SET status = ?, handled_by = ?, handled_at = ? WHERE idea_id = ?",
                     (status, by if done else None, _now() if done else None, int(idea_id)))
        conn.commit()
        return dict(conn.execute("SELECT * FROM staff_ideas WHERE idea_id = ?", (int(idea_id),)).fetchone())
    finally:
        conn.close()
