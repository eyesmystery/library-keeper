"""Manual deletion of catalog rows that are not books (Ibrahim, 2026-09-30).

Some Rewaa products are games, children's learning kits and the like: they are not books and
have no author. Staff mark them with «ليس كتابًا» in the admin book card (after a confirmation):
the row and everything attached to it (card, evidence, relations, author links, choices, daily
counts, ratings) is removed from the catalog, and a full snapshot is kept in `deleted_books`
so it can be restored from المكتبة → «المحذوفة» if it was deleted by mistake.

While a deletion is not restored, the weekly Rewaa import skips the same product (barcode or
product code; by title only for file rows without any code) instead of adding it back as a new book.
Cover files are left in place (book ids are AUTOINCREMENT and never reused), so a restore
brings the cover back too.
"""
import json
from datetime import datetime, timezone

from models import get_connection

REASONS = {"not_a_book": "ليس كتابًا"}

# (table, WHERE clause) – every row that belongs to one book
_TABLES = [
    ("books", "book_id = :id"),
    ("book_profiles", "book_id = :id"),
    ("attribute_evidence", "book_id = :id"),
    ("book_relations", "source_book_id = :id OR target_book_id = :id"),
    ("book_authors", "book_id = :id"),
    ("book_choices", "book_id = :id"),
    ("book_daily", "book_id = :id"),
    ("feedback", "book_id = :id"),
    ("book_confirmations", "book_id = :id"),
]
# parents first when restoring, children first when deleting
_RESTORE_ORDER = [t for t, _ in _TABLES]


def ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deleted_books (
            deletion_id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            author TEXT,
            publisher TEXT,
            barcode TEXT,
            rewaa_sku TEXT,
            isbn TEXT,
            quantity INTEGER,
            reason TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            deleted_by TEXT,
            snapshot TEXT NOT NULL,
            restored_at TEXT,
            restored_by TEXT
        )
        """
    )


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def delete_book(book_id, reason="not_a_book", user_name=None):
    """Snapshot and delete one book. -> {"deletion_id", "title"}; LookupError if unknown."""
    if reason not in REASONS:
        raise ValueError("invalid reason")
    conn = get_connection()
    try:
        book = conn.execute("SELECT * FROM books WHERE book_id = ?", (int(book_id),)).fetchone()
        if book is None:
            raise LookupError(book_id)
        have = _tables(conn)
        snap = {}
        for table, where in _TABLES:
            if table in have:
                snap[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE {where}", {"id": int(book_id)})]
        conn.execute("BEGIN")
        cur = conn.execute(
            """INSERT INTO deleted_books (book_id, title, author, publisher, barcode, rewaa_sku, isbn, quantity,
                                          reason, deleted_at, deleted_by, snapshot)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (book["book_id"], book["title"], book["author"], book["publisher"], book["barcode"], book["rewaa_sku"],
             book["isbn"], book["quantity"], reason, _now(), user_name, json.dumps(snap, ensure_ascii=False)))
        for table, where in reversed(_TABLES):
            if table in have:
                conn.execute(f"DELETE FROM {table} WHERE {where}", {"id": int(book_id)})
        conn.commit()
        return {"deletion_id": cur.lastrowid, "title": book["title"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_deleted(include_restored=False):
    conn = get_connection()
    try:
        q = """SELECT deletion_id, book_id, title, author, publisher, barcode, rewaa_sku, isbn, quantity, reason,
                      deleted_at, deleted_by, restored_at, restored_by FROM deleted_books"""
        if not include_restored:
            q += " WHERE restored_at IS NULL"
        rows = [dict(r) for r in conn.execute(q + " ORDER BY deletion_id DESC")]
    finally:
        conn.close()
    for r in rows:
        r["reason_label"] = REASONS.get(r["reason"], r["reason"])
    return rows


def restore(deletion_id, user_name=None):
    """Put a deleted book back exactly as it was. -> {"book_id", "title"}.
    LookupError: unknown deletion; ValueError: already restored or the id is taken."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM deleted_books WHERE deletion_id = ?", (int(deletion_id),)).fetchone()
        if row is None:
            raise LookupError(deletion_id)
        if row["restored_at"]:
            raise ValueError("تم استرجاعه من قبل")
        if conn.execute("SELECT 1 FROM books WHERE book_id = ?", (row["book_id"],)).fetchone():
            raise ValueError("رقم الكتاب مستخدم")
        snap = json.loads(row["snapshot"])
        have = _tables(conn)
        existing_books = {r[0] for r in conn.execute("SELECT book_id FROM books")} | {row["book_id"]}
        existing_authors = {r[0] for r in conn.execute("SELECT author_id FROM authors")} if "authors" in have else set()
        conn.execute("BEGIN")
        for table in _RESTORE_ORDER:
            if table not in have or not snap.get(table):
                continue
            cols_now = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for rec in snap[table]:
                if table == "book_relations" and not ({rec.get("source_book_id"), rec.get("target_book_id")} <= existing_books):
                    continue  # the other book is gone
                if table == "book_authors" and rec.get("author_id") not in existing_authors:
                    continue  # author merged/removed since; authors.sync() relinks from the text fields
                rec = {k: v for k, v in rec.items() if k in cols_now}
                cols = ", ".join(rec)
                marks = ", ".join("?" for _ in rec)
                conn.execute(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({marks})", tuple(rec.values()))
        conn.execute("UPDATE deleted_books SET restored_at = ?, restored_by = ? WHERE deletion_id = ?",
                     (_now(), user_name, int(deletion_id)))
        conn.commit()
        return {"book_id": row["book_id"], "title": row["title"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def import_skip_keys(conn):
    """Keys of deleted (not restored) products, for the Rewaa import. -> (barcodes, skus, titles)"""
    if "deleted_books" not in _tables(conn):
        return set(), set(), {}
    barcodes, skus, titles = set(), set(), {}
    for r in conn.execute("SELECT title, barcode, rewaa_sku, isbn FROM deleted_books WHERE restored_at IS NULL"):
        for code in (r["barcode"], r["isbn"]):
            if code:
                barcodes.add(str(code))
        if r["rewaa_sku"]:
            skus.add(str(r["rewaa_sku"]))
        titles[r["title"]] = True  # used only for file rows that carry neither a barcode nor a product code
    return barcodes, skus, titles
