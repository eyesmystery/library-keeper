"""«بانتظار إعادة البحث» (BKREV, Ibrahim 2026-10-29: «انا لما اعدل بيانات كتاب ابغاها تنحط في قائمة مستقلة يعيد البحث عنها
بناءا على البيانات المضافة. مثلا لو اضفت الغلاف او الدار»).

When staff (or the developer) change what identifies a book — title, author, publisher, ISBN, translator, year, pages,
language, genre — or its cover, the book is put on this list with the fields that changed. One open row per book: a
second edit before the research adds its fields to the same row. The list is a filter in «المكتبة», a line in the book
card, and a section of the downloaded inbox file; the research itself is done by Claude (summary, card, sources) and the
row is closed in the developer's copy («تمت إعادة البحث») or at the library by the next update (inbox_resolutions.json,
kind «research»). Nothing is deleted.
"""
import json
from datetime import datetime, timezone

from models import get_connection

STATUSES = ("open", "done")
PAGE = 300
# field -> Arabic name shown to staff
FIELDS = {"title": "العنوان", "author": "المؤلف", "publisher": "الدار", "isbn": "ISBN", "translator": "المترجم",
          "publication_year": "سنة الطبعة", "original_year": "سنة الصدور الأصلية", "page_count": "عدد الصفحات",
          "language": "لغة الطبعة", "original_language": "لغة الأصل", "genre": "التصنيف", "cover": "الغلاف"}


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS research_queue (
        queue_id INTEGER PRIMARY KEY AUTOINCREMENT, book_id INTEGER NOT NULL, changed TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL, created_by TEXT, updated_at TEXT, status TEXT NOT NULL DEFAULT 'open',
        handled_by TEXT, handled_at TEXT, handled_note TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_queue_book ON research_queue(book_id, status)")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _list(text):
    try:
        v = json.loads(text or "[]")
        return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
    except ValueError:
        return []


def labels(fields):
    return [FIELDS.get(f, f) for f in fields]


def _out(r):
    d = dict(r)
    d["changed"] = _list(d.get("changed"))
    d["changed_ar"] = labels(d["changed"])
    return d


def add(book_id, fields, by=None):
    """Queue the book after a change of `fields` (only names in FIELDS count). -> queue_id or None."""
    fields = [f for f in dict.fromkeys(fields or []) if f in FIELDS]
    if not fields:
        return None
    conn = get_connection()
    try:
        ensure_table(conn)
        row = conn.execute("SELECT queue_id, changed FROM research_queue WHERE book_id = ? AND status = 'open' "
                           "ORDER BY queue_id DESC LIMIT 1", (int(book_id),)).fetchone()
        if row:
            merged = list(dict.fromkeys(_list(row["changed"]) + fields))
            conn.execute("UPDATE research_queue SET changed = ?, updated_at = ? WHERE queue_id = ?",
                         (json.dumps(merged), _now(), row["queue_id"]))
            qid = row["queue_id"]
        else:
            qid = conn.execute("INSERT INTO research_queue (book_id, changed, created_at, created_by, updated_at) "
                               "VALUES (?, ?, ?, ?, ?)", (int(book_id), json.dumps(fields), _now(), by, _now())).lastrowid
        conn.commit()
        return qid
    finally:
        conn.close()


def for_book(book_id):
    """The open row of a book (or None)."""
    conn = get_connection()
    try:
        ensure_table(conn)
        r = conn.execute("SELECT * FROM research_queue WHERE book_id = ? AND status = 'open' ORDER BY queue_id DESC LIMIT 1",
                         (int(book_id),)).fetchone()
        return _out(r) if r else None
    finally:
        conn.close()


def open_ids():
    conn = get_connection()
    try:
        ensure_table(conn)
        return {r[0] for r in conn.execute("SELECT DISTINCT book_id FROM research_queue WHERE status = 'open'")}
    finally:
        conn.close()


def list_rows(status="open", offset=0):
    where, args = ("WHERE q.status = ?", [status]) if status in STATUSES else ("", [])
    conn = get_connection()
    try:
        ensure_table(conn)
        rows = [_out(r) for r in conn.execute(
            f"""SELECT q.*, b.title, b.author, b.publisher, b.isbn FROM research_queue q LEFT JOIN books b USING (book_id)
                {where} ORDER BY (q.status = 'open') DESC, q.queue_id DESC LIMIT ? OFFSET ?""",
            args + [PAGE, max(0, int(offset or 0))])]
        total = conn.execute(f"SELECT COUNT(*) FROM research_queue q {where}", args).fetchone()[0]
        return rows, total
    finally:
        conn.close()


def set_done(book_id, by=None, note=None):
    """Close the book's open row(s) after the research. -> number closed."""
    conn = get_connection()
    try:
        ensure_table(conn)
        n = conn.execute("UPDATE research_queue SET status = 'done', handled_by = ?, handled_at = ?, handled_note = ? "
                         "WHERE book_id = ? AND status = 'open'",
                         (by, _now(), (str(note or "").strip()[:500] or None), int(book_id))).rowcount
        conn.commit()
        return n
    finally:
        conn.close()
