"""«ماذا تقترح أن يكون على رفوف مكتبة ثلاث؟» — books visitors want the library to have (Ibrahim, 2026-10-27).

From «ماذا تقرأ الآن؟» a visitor writes the title of a book (and its author, if they like):
  * a book we hold and have in stock: the visitor is told it is already here and sees its book page
    (Ibrahim: «اذا اقترح كتاب متوفر في المكتبة يتم اعلام الزائر ان الكتاب فعلا متوفر ويوريه بطاقته»);
  * a book in our catalogue but out of stock: the visitor is told so, and «أبغاه يتوفر» keeps it here as a
    candidate (source 'catalog');
  * a book we do not have: it is looked up outside at once (outside_books.lookup: Wikidata, Open Library,
    Wikipedia, Goodreads) and the visitor is shown what was found and asked whether that is the book
    (Ibrahim: «يصير بحث وقتها عن الكتاب ويوريه القاريء ويسأله للتأكد … وتنحفظ بطاقته ككتاب مرشح»). On «نعم» its
    record is kept here as a candidate book (source 'outside'), with what the lookup found and its sources;
    on «لا» — or when nothing was found — the visitor's words are kept as a suggestion («البلاغات ← الاقتراحات»).
A book suggested again is not a new row: times_suggested grows.

Staff see the candidates in «التنبيهات ← من الزوار ← كتب مرشّحة» and mark each «تم توفيره» or «لن نوفره».
Library data, never in a release package (scripts/make_release.py EMPTY_TABLES); an update keeps it.
No visitor detail is stored (only the book and an optional short note).
"""
import json
from datetime import datetime, timezone

from models import get_connection

STATUSES = ("open", "added", "declined")
PAGE = 100
MAX_NOTE = 300


def ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_books (
            candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            outside_key TEXT,
            book_id INTEGER,
            title TEXT NOT NULL,
            author TEXT,
            year INTEGER,
            publisher TEXT,
            language TEXT,
            pages INTEGER,
            isbn TEXT,
            summary TEXT,
            genres TEXT NOT NULL DEFAULT '[]',
            sources TEXT NOT NULL DEFAULT '[]',
            asked_as TEXT,
            notes TEXT NOT NULL DEFAULT '[]',
            times_suggested INTEGER NOT NULL DEFAULT 1,
            first_at TEXT NOT NULL,
            last_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            handled_by TEXT,
            handled_at TEXT
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_outside ON candidate_books(outside_key) WHERE outside_key IS NOT NULL")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_book ON candidate_books(book_id) WHERE book_id IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidate_status ON candidate_books(status)")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def outside_record(key):
    """The stored outside lookup with this key (found ones only), or None."""
    if not isinstance(key, str) or not key.strip():
        return None
    import outside_books
    conn = get_connection()
    try:
        outside_books.ensure_table(conn)
        row = outside_books._row(conn, key.strip()[:200])
    finally:
        conn.close()
    return row if row and row.get("status") == "found" else None


def _note(note):
    import visitor_notes
    return visitor_notes.clean_text(note)[:MAX_NOTE] if note else ""


def _upsert(where, value, fields, note):
    conn = get_connection()
    try:
        ensure_table(conn)
        old = conn.execute(f"SELECT candidate_id, notes, status FROM candidate_books WHERE {where} = ?", (value,)).fetchone()
        now = _now()
        if old:
            notes = json.loads(old["notes"] or "[]")
            if note:
                notes = (notes + [note])[-20:]
            # suggested again after staff closed it: open again, so it is seen
            conn.execute("""UPDATE candidate_books SET times_suggested = times_suggested + 1, last_at = ?, notes = ?,
                            status = CASE WHEN status = 'declined' THEN status ELSE 'open' END,
                            handled_by = CASE WHEN status = 'declined' THEN handled_by ELSE NULL END,
                            handled_at = CASE WHEN status = 'declined' THEN handled_at ELSE NULL END
                            WHERE candidate_id = ?""", (now, json.dumps(notes, ensure_ascii=False), old["candidate_id"]))
            conn.commit()
            return old["candidate_id"], False
        cols = list(fields) + ["notes", "first_at", "last_at"]
        vals = list(fields.values()) + [json.dumps([note] if note else [], ensure_ascii=False), now, now]
        cur = conn.execute(f"INSERT INTO candidate_books ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", vals)
        conn.commit()
        return cur.lastrowid, True
    finally:
        conn.close()


def add_outside(record, asked_as="", note=""):
    """The visitor confirmed an outside lookup: keep its card as a candidate. -> (candidate_id, new)"""
    summary = (record.get("blurb") or record.get("description") or "").strip()[:1200] or None
    fields = {"source": "outside", "outside_key": record["key"], "title": record.get("title") or record.get("asked_title"),
              "author": record.get("author"), "year": record.get("year"), "publisher": record.get("publisher"),
              "language": record.get("language"), "pages": record.get("pages"), "isbn": record.get("isbn"),
              "summary": summary, "genres": json.dumps((record.get("genres") or [])[:6], ensure_ascii=False),
              "sources": json.dumps(record.get("sources") or [], ensure_ascii=False),
              "asked_as": (asked_as or "")[:120] or None}
    return _upsert("outside_key", record["key"], fields, _note(note))


def add_catalog(book, asked_as="", note=""):
    """A book of our catalogue that is out of stock: the visitor wants it back. -> (candidate_id, new)"""
    fields = {"source": "catalog", "book_id": book["book_id"], "title": book["title"], "author": book.get("author"),
              "publisher": book.get("publisher"), "language": book.get("language"), "pages": book.get("page_count"),
              "isbn": book.get("isbn"), "summary": (book.get("summary") or "")[:1200] or None,
              "asked_as": (asked_as or "")[:120] or None}
    return _upsert("book_id", book["book_id"], fields, _note(note))


# ---------------------------------------------------------------- staff
def _where(status):
    if status == "open":
        return " WHERE status = 'open'", []
    if status == "done":
        return " WHERE status != 'open'", []
    if status in STATUSES:
        return " WHERE status = ?", [status]
    return "", []


def counts():
    conn = get_connection()
    try:
        ensure_table(conn)
        out = {s: 0 for s in STATUSES}
        for r in conn.execute("SELECT status, COUNT(*) n FROM candidate_books GROUP BY status"):
            if r["status"] in out:
                out[r["status"]] = r["n"]
        return out
    finally:
        conn.close()


def total(status="open"):
    w, args = _where(status)
    conn = get_connection()
    try:
        ensure_table(conn)
        return conn.execute("SELECT COUNT(*) FROM candidate_books" + w, args).fetchone()[0]
    finally:
        conn.close()


def list_candidates(status="open", limit=PAGE, offset=0):
    w, args = _where(status)
    conn = get_connection()
    try:
        ensure_table(conn)
        rows = []
        for r in conn.execute("SELECT * FROM candidate_books" + w +
                              " ORDER BY (status = 'open') DESC, times_suggested DESC, last_at DESC LIMIT ? OFFSET ?",
                              args + [max(1, min(int(limit), 300)), max(0, int(offset))]):
            d = dict(r)
            for f in ("genres", "sources", "notes"):
                try:
                    d[f] = json.loads(d[f] or "[]")
                except (TypeError, ValueError):
                    d[f] = []
            rows.append(d)
        return rows
    finally:
        conn.close()


def set_status(candidate_id, status, username=None):
    if status not in STATUSES:
        raise ValueError("invalid status")
    conn = get_connection()
    try:
        ensure_table(conn)
        if conn.execute("SELECT 1 FROM candidate_books WHERE candidate_id = ?", (int(candidate_id),)).fetchone() is None:
            return None
        closed = status != "open"
        conn.execute("UPDATE candidate_books SET status = ?, handled_by = ?, handled_at = ? WHERE candidate_id = ?",
                     (status, username if closed else None, _now() if closed else None, int(candidate_id)))
        conn.commit()
        return dict(conn.execute("SELECT * FROM candidate_books WHERE candidate_id = ?", (int(candidate_id),)).fetchone())
    finally:
        conn.close()
