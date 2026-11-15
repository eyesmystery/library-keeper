"""Questions the chat could not answer from our data (Ibrahim, 2026-10-08: layer 4 of «طبق كل الطبقات").

Each time a visitor asks for something our data cannot answer, a SHORT phrase (never the visitor's
whole message, no conversation id, no address) is counted here, so staff see what readers actually
ask about and research can follow real demand:
  topic        a subject searched for that no in-stock book covers («أدب السجون»)
  award        a prize asked for with no in-stock book recorded for it («كتارا»)
  adaptation   a film / series asked for with no in-stock book recorded for it
  missing_book a title asked for that is not in stock («الخيميائي»)
  wish         a wish the search cannot apply at all («لغة جميلة»)
  question     a question about a book the assistant could not answer («من رسم الغلاف؟»)
Same kind + same normalized phrase = one row with a count. Staff mark a row handled.
"""
import re
from datetime import datetime, timezone

from keyword_prefs import normalize
from models import get_connection

KINDS = {"topic": "موضوع ما لقينا عنه كتابًا", "award": "جائزة ما عندنا كتاب مسجّل لها",
         "adaptation": "فيلم أو مسلسل ما عندنا بياناته", "missing_book": "كتاب طُلب وليس عندنا",
         "wish": "رغبة لا نقدر نبحث بها", "question": "سؤال عن كتاب ما عرفنا نجاوبه",
         "missing_author": "مؤلف طُلب وما عندنا من كتبه"}
# ADMINWISH (Ibrahim 2026-11-13): «قيد المتابعة» and «مرفوض» join «open» (جديد) and «done» (تم توفيره / تمت المعالجة) for
# the books and authors visitors asked for (backend/visitor_wishes.py). «following» still counts as open, «declined» as handled.
STATUSES = ("open", "following", "done", "declined")
GROUPS = {"open": ("open", "following"), "done": ("done", "declined")}
PAGE = 100
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _status_where(status, col="status"):
    """-> (sql, args) for a status filter: «open» = open + following, «done» = done + declined, one status, or none."""
    if status in GROUPS:
        vals = GROUPS[status]
        return f"{col} IN ({', '.join('?' for _ in vals)})", list(vals)
    if status in STATUSES:
        return f"{col} = ?", [status]
    return "", []


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS open_questions (
        q_id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, phrase TEXT NOT NULL,
        phrase_key TEXT NOT NULL, book_title TEXT, times INTEGER NOT NULL DEFAULT 1,
        first_at TEXT NOT NULL, last_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
        handled_by TEXT, handled_at TEXT, staff_note TEXT, UNIQUE (kind, phrase_key))""")
    # ADMINWISH 2026-11-13: what staff wrote about a request («ملاحظة الموظف»); library data, an update keeps it
    if "staff_note" not in {c[1] for c in conn.execute("PRAGMA table_info(open_questions)")}:
        import models
        models._add_column(conn, "open_questions", "staff_note", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_open_questions_status ON open_questions(status, kind)")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(kind, phrase, book_title=None):
    """Count one unanswered request. Never raises: a logging failure must not break the chat."""
    try:
        if kind not in KINDS:
            return
        phrase = _CONTROL.sub(" ", str(phrase or "")).strip()[:80]
        key = " ".join(normalize(phrase).split())
        if len(key) < 2:
            return
        title = _CONTROL.sub(" ", str(book_title or "")).strip()[:120] or None
        conn = get_connection()
        try:
            ensure_table(conn)
            now = _now()
            conn.execute("""INSERT INTO open_questions (kind, phrase, phrase_key, book_title, first_at, last_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(kind, phrase_key) DO UPDATE SET times = times + 1, last_at = excluded.last_at,
                            status = CASE WHEN status IN ('following', 'declined') THEN status ELSE 'open' END,
                            book_title = COALESCE(excluded.book_title, book_title)""",
                         (kind, phrase, key, title, now, now))
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - visitor experience first
        pass


def list_rows(status="open", offset=0):
    conn = get_connection()
    try:
        ensure_table(conn)
        sql, args = _status_where(status)
        where = "WHERE " + sql if sql else ""
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM open_questions {where} ORDER BY times DESC, last_at DESC LIMIT ? OFFSET ?",
            args + [PAGE, max(0, int(offset or 0))])]
        total = conn.execute(f"SELECT COUNT(*) FROM open_questions {where}", args).fetchone()[0]
    finally:
        conn.close()
    for r in rows:
        r["kind_label"] = KINDS.get(r["kind"], r["kind"])
    return rows, total


def requested(status="open", limit=500):
    """«الكتب المطلوبة» (Ibrahim 2026-10-24: «سو قسم جديد في الادارة وهي فيها الكتب اللي تطلب ومو متوفرة وتسجل كم مرة
    طلب»): the titles and authors visitors asked for that we did not have, the most asked first, with how many times."""
    conn = get_connection()
    try:
        ensure_table(conn)
        where, args = "WHERE kind IN ('missing_book', 'missing_author')", []
        sql, sargs = _status_where(status)
        if sql:
            where += " AND " + sql
            args += sargs
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM open_questions {where} ORDER BY times DESC, last_at DESC LIMIT ?", args + [int(limit)])]
        n = {k: conn.execute(f"SELECT COUNT(*) FROM open_questions WHERE kind IN ('missing_book', 'missing_author') AND status=?",
                             (k,)).fetchone()[0] for k in STATUSES}
    finally:
        conn.close()
    for r in rows:
        r["kind_label"] = KINDS.get(r["kind"], r["kind"])
    return rows, n


def counts():
    conn = get_connection()
    try:
        ensure_table(conn)
        return {"open": conn.execute("SELECT COUNT(*) FROM open_questions WHERE status IN ('open', 'following')").fetchone()[0],
                "all": conn.execute("SELECT COUNT(*) FROM open_questions").fetchone()[0]}
    finally:
        conn.close()


def set_status(q_id, status, by=None):
    if status not in STATUSES:
        raise ValueError("حالة غير معروفة")
    conn = get_connection()
    try:
        ensure_table(conn)
        if conn.execute("SELECT 1 FROM open_questions WHERE q_id=?", (q_id,)).fetchone() is None:
            return None
        handled = status != "open"
        conn.execute("UPDATE open_questions SET status=?, handled_by=?, handled_at=? WHERE q_id=?",
                     (status, by if handled else None, _now() if handled else None, q_id))
        conn.commit()
        return dict(conn.execute("SELECT * FROM open_questions WHERE q_id=?", (q_id,)).fetchone())
    finally:
        conn.close()
