"""«اختياراتنا هذا الشهر» (Ibrahim, 2026-10-14): five books on the visitor's start page.

How a month's five are chosen (his choices of 2026-10-14):
  * the automatic ranking proposes and a staff member approves («التلقائي يقترح والموظف يعتمد»); a month
    nobody approved shows nothing new — the visitor keeps seeing the last approved month;
  * staff can replace any slot from the candidates (or any book in stock), pin a slot so the automatic
    ranking never changes it, and reorder; every change goes to the audit log (the staff changes file);
  * the outside signals (monthly best-seller / trending lists of نيل وفرات، جرير، أبجد and the stored
    reader ratings with their counts) are built on Ibrahim's side once a month by
    scripts/build_pick_signals.py into backend/picks_signals.json, which travels with the update — the
    library's computer never reads those sites;
  * a book that runs out of stock is not shown: the next candidate takes its place for the visitor. Stock is
    read from the database at that moment (the ranking cache also renews itself when the books in stock
    change — AUDFIX, audit 2026-10-14);
  * a slot staff empty («تفريغ») stays empty: the row is kept with chosen_by = 'cleared' (the book it held is
    remembered), the visitor sees one book fewer, no replacement fills it and that book is not brought back
    by a replacement or by «تحديث الاقتراح التلقائي» in that month (AUDFIX, audit 2026-10-14).

Ranking of an in-stock book (only books we already hold; an outside list is a signal, never a source of
books): outside monthly lists, reader rating weighted by how many readers rated it (4.2 from 1,000 readers
counts more than 5 from 10), prizes, and our own visitors' «أبغى هذا الكتاب» choices and ratings (a large
weight, the closest signal of this library's taste). Diversity: one book per author, at most two per genre,
nothing that was a pick in the previous three months. Books whose card lists sexual content, books without
a cover and books with neither a hook nor a summary are left to staff (never proposed automatically).
"""
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta

from models import get_connection
from keyword_prefs import normalize

SLOTS = 5
CANDIDATES = 12
NO_REPEAT_MONTHS = 3
VISITOR_DAYS = 90
SIGNALS_PATH = os.environ.get("LIBRARY_KEEPER_PICK_SIGNALS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "picks_signals.json")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class PickError(ValueError):
    pass


def ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS monthly_picks (
        month TEXT NOT NULL, slot INTEGER NOT NULL, book_id INTEGER NOT NULL,
        reason TEXT, pinned INTEGER NOT NULL DEFAULT 0, chosen_by TEXT NOT NULL DEFAULT 'auto',
        updated_by TEXT, updated_at TEXT, PRIMARY KEY (month, slot))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS monthly_pick_months (
        month TEXT PRIMARY KEY, approved_by TEXT, approved_at TEXT)""")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def this_month():
    """The library's month (Riyadh, UTC+3)."""
    return datetime.fromtimestamp(time.time() + 3 * 3600, timezone.utc).strftime("%Y-%m")


def valid_month(value):
    return isinstance(value, str) and bool(_MONTH_RE.match(value))


def _shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    m += n
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}-{m:02d}"


# ---------------------------------------------------------------- outside signals (built on Ibrahim's side)
_signals_cache = {"mtime": None, "data": None}


def load_signals():
    """-> {"month", "built_at", "lists": [{"key", "label", "url", "books": [{"book_id", "rank"}]}],
    "ratings": {book_id(str): {"avg", "count", "source"}}}; an empty shape when the file is missing or broken."""
    empty = {"month": None, "built_at": None, "lists": [], "ratings": {}}
    try:
        mtime = os.path.getmtime(SIGNALS_PATH)
    except OSError:
        return empty
    if _signals_cache["mtime"] != mtime:
        try:
            with open(SIGNALS_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError
            data.setdefault("lists", [])
            data.setdefault("ratings", {})
        except (OSError, ValueError):
            data = empty
        _signals_cache.update(mtime=mtime, data=data)
    return _signals_cache["data"]


def _rating_points(r):
    """Reader rating weighted by its count: a Bayesian average pulled towards 3.8 by 200 imaginary readers,
    so 4.2 from 1,000 readers (~4.13) beats 5.0 from 10 (~3.86). Under 20 ratings counts for nothing."""
    try:
        avg, count = float(r.get("avg")), int(r.get("count"))
    except (TypeError, ValueError):
        return 0.0, None
    if count < 20 or not (0 < avg <= 5):
        return 0.0, None
    bayes = (avg * count + 3.8 * 200) / (count + 200)
    src = {"goodreads": "Goodreads", "abjjad": "أبجد"}.get(r.get("source"), r.get("source") or "")
    reason = f"تقييم {avg:.1f} من {count:,} قارئ" + (f" ({src})" if src else "")
    return max(0.0, bayes - 3.6) * 5, reason


def _num(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- ranking
_rank_cache = {"key": None, "at": 0.0, "data": None}
AWARD_POINTS = {"winner": 2.0, "shortlist": 1.0, "longlist": 0.6, "nominated": 0.6}
AWARD_WORDS = {"winner": "فاز بـ", "shortlist": "في القائمة القصيرة لـ", "longlist": "في القائمة الطويلة لـ",
               "nominated": "مرشح لـ"}


def _has_cover(book):
    import covers  # late: covers imports nothing of ours, but keeps this module light for the scripts
    return bool(book.get("cover_url")) and covers.has_local_cover(book)


def visitor_line(book):
    """The short line under the cover: the hook, else the first sentence of the summary."""
    hook = (book.get("hook") or "").strip()
    if hook:
        return hook
    summary = (book.get("summary") or "").strip()
    if not summary:
        return ""
    first = re.split(r"(?<=[.!؟?])\s", summary, maxsplit=1)[0]
    return first if len(first) <= 140 else first[:137].rsplit(" ", 1)[0] + "…"


def _recent_picks(conn, month):
    """Books that were a pick in the NO_REPEAT_MONTHS months before `month`."""
    months = [_shift(month, -i) for i in range(1, NO_REPEAT_MONTHS + 1)]
    rows = conn.execute(f"SELECT DISTINCT book_id FROM monthly_picks WHERE month IN ({','.join('?' * len(months))}) "
                        "AND chosen_by <> 'cleared'", months).fetchall()
    return {r[0] for r in rows}


def _stocked_ids(conn):
    """The books in stock right now (active, quantity above 0), read from the database."""
    return frozenset(r[0] for r in conn.execute("SELECT book_id FROM books WHERE active = 1 AND quantity > 0"))


CLEARED = "cleared"  # chosen_by of a slot staff emptied on purpose


def _cleared_ids(conn, month):
    return {r[0] for r in conn.execute("SELECT book_id FROM monthly_picks WHERE month = ? AND chosen_by = ?",
                                       (month, CLEARED))}


def ranking(month=None, conn=None):
    """-> list of {"book": dict, "score": float, "reasons": [str], "auto_ok": bool}, best first, over every
    book in stock. Cached for five minutes per month (5,000 books are scored in well under a second); the
    cache is renewed at once when the set of books in stock changes (a stock edit in the admin, a Rewaa
    import or a script in another process), so it never offers a book that has run out."""
    month = month or this_month()
    sig = load_signals()
    own = conn is None
    conn = conn or get_connection()
    try:
        stocked = _stocked_ids(conn)
        key = (month, sig.get("built_at"), stocked)
        if _rank_cache["key"] == key and time.time() - _rank_cache["at"] < 300:
            return _rank_cache["data"]
        ensure_tables(conn)
        books = [dict(r) for r in conn.execute("SELECT * FROM books WHERE active = 1 AND quantity > 0")]
        sensitive = {r[0] for r in conn.execute(
            "SELECT book_id FROM book_profiles WHERE sensitive_content LIKE '%sexual_content%'")}
        awards = {}
        for r in conn.execute("SELECT book_id, award_name, result FROM book_awards"):
            awards.setdefault(r[0], []).append((r[2], r[1]))
        since = (datetime.now(timezone.utc) - timedelta(days=VISITOR_DAYS)).isoformat(timespec="seconds")
        chosen = dict(conn.execute("SELECT book_id, COUNT(*) FROM book_choices WHERE created_at >= ? GROUP BY book_id",
                                   (since,)).fetchall())
        liked = {}
        for bid, rating in conn.execute("SELECT book_id, rating FROM feedback WHERE created_at >= ?", (since,)):
            liked[bid] = liked.get(bid, 0) + {"perfect": 1.0, "close": 0.3, "no": -1.0}.get(rating, 0)
        recent = _recent_picks(conn, month)
    finally:
        if own:
            conn.close()
    listed = {}
    for lst in sig.get("lists") or []:
        for item in lst.get("books") or []:
            bid = _num(item.get("book_id"))
            if bid:
                listed.setdefault(bid, []).append((lst.get("label") or lst.get("key") or "", _num(item.get("rank"))))
    ratings = sig.get("ratings") or {}
    year_now = int(month[:4])
    out = []
    for b in books:
        bid = b["book_id"]
        score, reasons = 0.0, []
        for label, rank in listed.get(bid, []):
            score += 3.0 + (1.0 if rank and rank <= 10 else 0.0)
            reasons.append(f"ضمن {label}" + (f" (المركز {rank})" if rank else ""))
        pts, why = _rating_points(ratings.get(str(bid)) or {})
        if why:
            score += pts
            reasons.append(why)
        best = None
        for result, name in awards.get(bid, []):
            if best is None or AWARD_POINTS.get(result, 0) > AWARD_POINTS.get(best[0], 0):
                best = (result, name)
        if best:
            score += AWARD_POINTS.get(best[0], 0.5)
            reasons.append(AWARD_WORDS.get(best[0], "") + (best[1] or "جائزة"))
        n = chosen.get(bid, 0)
        if n:
            score += min(n, 4) * 1.5
            reasons.append("اختاره زائر من زوارنا" if n == 1 else f"اختاره {n} من زوارنا")
        if liked.get(bid):
            score += max(-2.0, min(liked[bid], 3.0))
        year = _num(b.get("original_year")) or _num(b.get("publication_year"))
        if year and year_now - year <= 2:
            score += 0.5
            reasons.append(f"صدر سنة {year}")
        auto_ok = (bid not in sensitive and bid not in recent and _has_cover(b) and bool(visitor_line(b)))
        out.append({"book": b, "score": round(score, 2), "reasons": reasons, "auto_ok": auto_ok,
                    "recent": bid in recent})
    out.sort(key=lambda x: (-x["score"], x["book"]["book_id"]))
    _rank_cache.update(key=key, at=time.time(), data=out)
    return out


def clear_cache():
    _rank_cache.update(key=None, data=None)


# ---------------------------------------------------------------- a month's five
def _author_key(book):
    return normalize(book.get("author") or "").strip() or f"#{book['book_id']}"


def _title_key(book):
    return normalize(book.get("title") or "").strip()


def _fits(book, taken):
    """Diversity against the books already in the month: one per author, the same work once, two per genre."""
    if any(_author_key(book) == _author_key(t) or _title_key(book) == _title_key(t) for t in taken):
        return False
    genre = book.get("genre")
    return not genre or sum(1 for t in taken if t.get("genre") == genre) < 2


def _rows(conn, month):
    return [dict(r) for r in conn.execute("SELECT * FROM monthly_picks WHERE month = ? ORDER BY slot", (month,))]


def _book(conn, book_id):
    r = conn.execute("SELECT * FROM books WHERE book_id = ?", (book_id,)).fetchone()
    return dict(r) if r else None


def _in_stock(book):
    return bool(book) and book.get("active") == 1 and (book.get("quantity") or 0) > 0


def fill(month, by=None, conn=None):
    """Fill the empty slots and replace the unpinned automatic ones with the best ranked books, keeping
    pinned slots, books staff placed and slots staff emptied (whose books are not proposed again this
    month). -> number of slots written."""
    own = conn is None
    conn = conn or get_connection()
    try:
        ensure_tables(conn)
        rows = {r["slot"]: r for r in _rows(conn, month)}
        keep = {s: r for s, r in rows.items() if r["pinned"] or r["chosen_by"] in ("staff", CLEARED)}
        taken = [b for b in (_book(conn, r["book_id"]) for r in keep.values() if r["chosen_by"] != CLEARED) if b]
        refused = {r["book_id"] for r in keep.values() if r["chosen_by"] == CLEARED}
        free = [s for s in range(1, SLOTS + 1) if s not in keep]
        chosen = []
        for item in ranking(month, conn):
            if len(chosen) >= len(free):
                break
            b = item["book"]
            if item["auto_ok"] and b["book_id"] not in refused and _fits(b, taken):
                chosen.append(item)
                taken.append(b)
        now = _now()
        for slot, item in zip(free, chosen):
            conn.execute("INSERT OR REPLACE INTO monthly_picks (month, slot, book_id, reason, pinned, chosen_by, "
                         "updated_by, updated_at) VALUES (?, ?, ?, ?, 0, 'auto', ?, ?)",
                         (month, slot, item["book"]["book_id"], "؛ ".join(item["reasons"][:3]) or None, by, now))
        for slot in free[len(chosen):]:
            conn.execute("DELETE FROM monthly_picks WHERE month = ? AND slot = ?", (month, slot))
        conn.commit()
        return len(chosen)
    finally:
        if own:
            conn.close()


def ensure_draft(month):
    """A month that has no rows yet gets the automatic proposal (not approved)."""
    conn = get_connection()
    try:
        ensure_tables(conn)
        if not conn.execute("SELECT 1 FROM monthly_picks WHERE month = ? LIMIT 1", (month,)).fetchone():
            fill(month, None, conn)
    finally:
        conn.close()


def set_slot(month, slot, book_id, by=None):
    """Staff put a book in a slot. A book staff chose stays when the proposal is refreshed."""
    if not (isinstance(slot, int) and 1 <= slot <= SLOTS):
        raise PickError("رقم الخانة غير صالح")
    conn = get_connection()
    try:
        ensure_tables(conn)
        book = _book(conn, book_id)
        if not book:
            raise PickError("الكتاب غير موجود")
        if not _in_stock(book):
            raise PickError("الكتاب غير متوفر الآن، فما ينفع يكون من الاختيارات")
        other = conn.execute("SELECT slot FROM monthly_picks WHERE month = ? AND book_id = ? AND slot <> ? "
                             "AND chosen_by <> 'cleared'", (month, book_id, slot)).fetchone()
        if other:
            raise PickError(f"الكتاب موجود في الخانة {other[0]}")
        reason = next(("؛ ".join(i["reasons"][:3]) for i in ranking(month, conn)
                       if i["book"]["book_id"] == book_id and i["reasons"]), None)
        conn.execute("INSERT OR REPLACE INTO monthly_picks (month, slot, book_id, reason, pinned, chosen_by, updated_by, "
                     "updated_at) VALUES (?, ?, ?, ?, 0, 'staff', ?, ?)", (month, slot, book_id, reason, by, _now()))
        conn.commit()
        return book
    finally:
        conn.close()


def _update(month, sql, params):
    conn = get_connection()
    try:
        ensure_tables(conn)
        n = conn.execute(sql, params).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def set_pinned(month, slot, pinned, by=None):
    return _update(month, "UPDATE monthly_picks SET pinned = ?, updated_by = ?, updated_at = ? WHERE month = ? AND slot = ? "
                   "AND chosen_by <> 'cleared'", (1 if pinned else 0, by, _now(), month, slot)) > 0


def clear_slot(month, slot, by=None):
    """Staff empty a slot on purpose: it stays empty for the visitor (no replacement) and its book is not
    brought back this month. The row is kept (chosen_by 'cleared') so the book is remembered; putting a
    book in the slot (set_slot) replaces it."""
    return _update(month, "UPDATE monthly_picks SET chosen_by = 'cleared', pinned = 0, reason = NULL, updated_by = ?, "
                   "updated_at = ? WHERE month = ? AND slot = ? AND chosen_by <> 'cleared'",
                   (by, _now(), month, slot)) > 0


def move(month, slot, to):
    """Swap two slots (the order the visitor sees)."""
    if not all(isinstance(x, int) and 1 <= x <= SLOTS for x in (slot, to)) or slot == to:
        raise PickError("ترتيب غير صالح")
    conn = get_connection()
    try:
        ensure_tables(conn)
        conn.execute("UPDATE monthly_picks SET slot = -1 WHERE month = ? AND slot = ?", (month, slot))
        conn.execute("UPDATE monthly_picks SET slot = ? WHERE month = ? AND slot = ?", (slot, month, to))
        conn.execute("UPDATE monthly_picks SET slot = ? WHERE month = ? AND slot = -1", (to, month))
        conn.commit()
    finally:
        conn.close()


def approval(month):
    conn = get_connection()
    try:
        ensure_tables(conn)
        r = conn.execute("SELECT approved_by, approved_at FROM monthly_pick_months WHERE month = ?", (month,)).fetchone()
        return dict(r) if r and r["approved_at"] else None
    finally:
        conn.close()


def set_approved(month, approved, by=None):
    conn = get_connection()
    try:
        ensure_tables(conn)
        if approved:
            if not conn.execute("SELECT 1 FROM monthly_picks WHERE month = ? AND chosen_by <> 'cleared' LIMIT 1",
                                (month,)).fetchone():
                raise PickError("ما فيه كتب في هذا الشهر للاعتماد")
            conn.execute("INSERT OR REPLACE INTO monthly_pick_months (month, approved_by, approved_at) VALUES (?, ?, ?)",
                         (month, by, _now()))
        else:
            conn.execute("DELETE FROM monthly_pick_months WHERE month = ?", (month,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- what the admin and the visitor see
def admin_view(month, draft=True):
    """-> {"month", "approved", "slots": [{slot, book, reason, pinned, chosen_by, out_of_stock, ...} | None],
    "candidates": [{book, score, reasons}], "signals": {...}} — `book` values are raw rows (the app adds covers).
    `draft`: propose the five when the month has none yet (only for this month and the next)."""
    if draft:
        ensure_draft(month)
    conn = get_connection()
    try:
        ensure_tables(conn)
        rows = {r["slot"]: r for r in _rows(conn, month)}
        slots = []
        for s in range(1, SLOTS + 1):
            r = rows.get(s)
            if not r:
                slots.append({"slot": s, "book": None})
                continue
            if r["chosen_by"] == CLEARED:
                slots.append({"slot": s, "book": None, "cleared": True, "updated_by": r["updated_by"],
                              "updated_at": r["updated_at"]})
                continue
            b = _book(conn, r["book_id"])
            slots.append({"slot": s, "book": b, "reason": r["reason"], "pinned": bool(r["pinned"]),
                          "chosen_by": r["chosen_by"], "updated_by": r["updated_by"], "updated_at": r["updated_at"],
                          "out_of_stock": not _in_stock(b), "line": visitor_line(b or {})})
        in_month = {r["book_id"] for r in rows.values()}  # a book staff took out is not offered again
        rank = ranking(month, conn)
        stocked = _stocked_ids(conn)
    finally:
        conn.close()
    cands, seen = [], set()
    for item in rank:
        b = item["book"]
        if b["book_id"] in in_month or b["book_id"] not in stocked or _title_key(b) in seen or not item["auto_ok"]:
            continue
        seen.add(_title_key(b))
        cands.append({"book": b, "score": item["score"], "reasons": item["reasons"]})
        if len(cands) >= CANDIDATES:
            break
    sig = load_signals()
    return {"month": month, "approved": approval(month), "slots": slots, "candidates": cands,
            "signals": {"month": sig.get("month"), "built_at": sig.get("built_at"),
                        "lists": [{"label": l.get("label"), "url": l.get("url"), "matched": len(l.get("books") or [])}
                                  for l in sig.get("lists") or []],
                        "ratings": len(sig.get("ratings") or {})}}


def live_month(today_month=None):
    """The month the visitor sees: this month when approved, else the latest approved month before it."""
    today_month = today_month or this_month()
    conn = get_connection()
    try:
        ensure_tables(conn)
        r = conn.execute("SELECT month FROM monthly_pick_months WHERE approved_at IS NOT NULL AND month <= ? "
                         "ORDER BY month DESC LIMIT 1", (today_month,)).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


def public_books():
    """The visitor's books, in order: a pick that is out of stock now (read from the database, not from the
    ranking cache) is replaced by the next ranked book in stock (one per author, the same work once); a slot
    staff emptied stays empty, and neither the books of the month's slots nor a book staff took out come
    back as a replacement. -> (month or None, [book dict with "line"])."""
    month = live_month()
    if not month:
        return None, []
    conn = get_connection()
    try:
        rows = _rows(conn, month)
        shown = [r for r in rows if r["chosen_by"] != CLEARED]
        stocked = _stocked_ids(conn)
        books, missing = [], 0
        for r in shown:
            b = _book(conn, r["book_id"])
            if _in_stock(b) and b["book_id"] in stocked:
                books.append(b)
            else:
                missing += 1  # only a book that ran out is replaced
        exclude = {r["book_id"] for r in rows}
        if missing > 0:
            for item in ranking(this_month(), conn):
                if missing <= 0:
                    break
                bid = item["book"]["book_id"]
                if not item["auto_ok"] or bid in exclude or bid not in stocked:
                    continue
                b = _book(conn, bid)  # the current row, not the cached copy
                if _in_stock(b) and _fits(b, books):
                    books.append(b)
                    exclude.add(bid)
                    missing -= 1
    finally:
        conn.close()
    for b in books:
        b["line"] = visitor_line(b)
    return month, books
