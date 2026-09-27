"""Reading trends and visitor satisfaction (approved by Ibrahim 2026-09-27).

What is recorded (all anonymous: no conversation text, no visitor id, no address):
  - book_daily.shown   : how many times a book was suggested to visitors, per day
  - book_choices       : each time a visitor tapped «أبغى هذا الكتاب» (book, time, language, match kind)
  - feedback.choice_id : the rating a visitor gave after choosing, linked to that choice
Choosing a book NEVER changes stock; quantity is managed only by staff or the Rewaa file.
Days are counted in the store's local time (UTC+3 by default; LIBRARY_KEEPER_UTC_OFFSET_HOURS).
"""
import os
from datetime import datetime, timedelta, timezone

from models import get_connection

RATINGS = ("perfect", "close", "no")
MATCHES = ("direct", "filler", "shown")
OFFSET_HOURS = float(os.environ.get("LIBRARY_KEEPER_UTC_OFFSET_HOURS", "3"))
_SHIFT = f"{int(OFFSET_HOURS * 60):+d} minutes"


def _utc_now():
    return datetime.now(timezone.utc)


def local_day(dt=None):
    return ((dt or _utc_now()) + timedelta(hours=OFFSET_HOURS)).date().isoformat()


def record_shown(book_ids):
    ids = sorted({int(i) for i in book_ids if i is not None})
    if not ids:
        return
    day = local_day()
    conn = get_connection()
    conn.executemany("INSERT INTO book_daily (day, book_id, shown) VALUES (?, ?, 1) "
                     "ON CONFLICT(day, book_id) DO UPDATE SET shown = shown + 1", [(day, i) for i in ids])
    conn.commit()
    conn.close()


def record_choice(book_id, language="ar", match=None):
    """Store one choice; returns its id. Stock is not touched."""
    conn = get_connection()
    cur = conn.execute("INSERT INTO book_choices (book_id, created_at, language, match) VALUES (?, ?, ?, ?)",
                       (int(book_id), _utc_now().isoformat(timespec="seconds"),
                        "en" if language == "en" else "ar", match if match in MATCHES else None))
    conn.execute("INSERT INTO book_daily (day, book_id, chosen) VALUES (?, ?, 1) "
                 "ON CONFLICT(day, book_id) DO UPDATE SET chosen = chosen + 1", (local_day(), int(book_id)))
    conn.commit()
    choice_id = cur.lastrowid
    conn.close()
    return choice_id


def choice_status(choice_id):
    """-> (book_id, already_rated) or None when the choice does not exist or is older than 2 days."""
    conn = get_connection()
    row = conn.execute("SELECT book_id, created_at FROM book_choices WHERE choice_id = ?", (int(choice_id),)).fetchone()
    if row is None:
        conn.close()
        return None
    rated = conn.execute("SELECT 1 FROM feedback WHERE choice_id = ?", (int(choice_id),)).fetchone() is not None
    conn.close()
    made = datetime.fromisoformat(row["created_at"])
    if _utc_now() - made > timedelta(days=2):
        return None
    return row["book_id"], rated


def add_rating(choice_id, book_id, rating):
    conn = get_connection()
    conn.execute("INSERT INTO feedback (book_id, rating, choice_id) VALUES (?, ?, ?)", (book_id, rating, int(choice_id)))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- statistics
def _range(days):
    today = datetime.fromisoformat(local_day()).date()
    if days:
        start = today - timedelta(days=days - 1)
        prev_start, prev_end = start - timedelta(days=days), start - timedelta(days=1)
        return start.isoformat(), today.isoformat(), prev_start.isoformat(), prev_end.isoformat()
    return None, today.isoformat(), None, None


def _ratings(conn, start, end):
    q = f"SELECT rating, COUNT(*) n FROM feedback WHERE date(created_at, '{_SHIFT}') <= ?"
    args = [end]
    if start:
        q += f" AND date(created_at, '{_SHIFT}') >= ?"
        args.append(start)
    counts = {r: 0 for r in RATINGS}
    for row in conn.execute(q + " GROUP BY rating", args):
        counts[row["rating"]] = row["n"]
    return counts


def _satisfaction(counts):
    total = sum(counts.values())
    return {"total": total, "counts": counts,
            "satisfied_pct": round(100 * (counts["perfect"] + counts["close"]) / total) if total else None,
            "perfect_pct": round(100 * counts["perfect"] / total) if total else None}


def _day_filter(start, end, col="day"):
    if start:
        return f"{col} BETWEEN ? AND ?", [start, end]
    return f"{col} <= ?", [end]


def summary(days=30):
    start, end, prev_start, prev_end = _range(days)
    conn = get_connection()
    where, args = _day_filter(start, end)
    tot = conn.execute(f"SELECT COALESCE(SUM(shown),0) shown, COALESCE(SUM(chosen),0) chosen FROM book_daily WHERE {where}", args).fetchone()
    sat = _satisfaction(_ratings(conn, start, end))
    out = {"days": days, "from": start, "to": end,
           "totals": {"shown": tot["shown"], "chosen": tot["chosen"],
                      "choice_rate_pct": round(100 * tot["chosen"] / tot["shown"], 1) if tot["shown"] else None,
                      "rated": sat["total"],
                      "rated_pct": round(100 * sat["total"] / tot["chosen"]) if tot["chosen"] else None},
           "satisfaction": sat}
    if prev_start:
        pw, pa = _day_filter(prev_start, prev_end)
        ptot = conn.execute(f"SELECT COALESCE(SUM(chosen),0) chosen FROM book_daily WHERE {pw}", pa).fetchone()
        out["previous"] = {"chosen": ptot["chosen"], "satisfaction": _satisfaction(_ratings(conn, prev_start, prev_end))}

    # per-day series (choices and ratings)
    first = start or (conn.execute("SELECT MIN(day) d FROM book_daily").fetchone()["d"] or end)
    series = {}
    d = datetime.fromisoformat(first).date()
    last = datetime.fromisoformat(end).date()
    while d <= last and len(series) < 400:
        series[d.isoformat()] = {"day": d.isoformat(), "chosen": 0, "shown": 0, "perfect": 0, "close": 0, "no": 0}
        d += timedelta(days=1)
    for row in conn.execute(f"SELECT day, SUM(shown) s, SUM(chosen) c FROM book_daily WHERE {where} GROUP BY day", args):
        if row["day"] in series:
            series[row["day"]].update(shown=row["s"], chosen=row["c"])
    fw, fa = _day_filter(start, end, f"date(created_at, '{_SHIFT}')")
    for row in conn.execute(f"SELECT date(created_at, '{_SHIFT}') d, rating, COUNT(*) n FROM feedback WHERE {fw} GROUP BY d, rating", fa):
        if row["d"] in series:
            series[row["d"]][row["rating"]] = row["n"]
    out["daily"] = list(series.values())

    # per book
    books = {}
    for row in conn.execute(f"""SELECT b.book_id, b.title, b.author, b.genre, b.cover_url, b.quantity, b.active,
                                       SUM(d.shown) shown, SUM(d.chosen) chosen
                                FROM book_daily d JOIN books b ON b.book_id = d.book_id
                                WHERE {where.replace('day', 'd.day')} GROUP BY b.book_id""", args):
        books[row["book_id"]] = dict(row, perfect=0, close=0, no=0)
    for row in conn.execute(f"SELECT book_id, rating, COUNT(*) n FROM feedback WHERE {fw} AND book_id IS NOT NULL "
                            "GROUP BY book_id, rating", fa):
        if row["book_id"] in books:
            books[row["book_id"]][row["rating"]] = row["n"]
    for b in books.values():
        b["ratings"] = b["perfect"] + b["close"] + b["no"]
        b["choice_rate_pct"] = round(100 * b["chosen"] / b["shown"]) if b["shown"] else None
    ranked = sorted(books.values(), key=lambda b: (-b["chosen"], -b["shown"], b["title"]))
    out["top_books"] = [b for b in ranked if b["chosen"]][:15]
    out["shown_not_chosen"] = sorted([b for b in books.values() if b["shown"] >= 5 and not b["chosen"]],
                                     key=lambda b: -b["shown"])[:10]
    out["low_rated"] = sorted([b for b in books.values() if b["ratings"] >= 3 and b["no"] / b["ratings"] >= 0.4],
                              key=lambda b: (-b["no"] / b["ratings"], -b["ratings"]))[:10]
    genres = {}
    for b in books.values():
        g = genres.setdefault(b["genre"] or "غير مصنّف", {"genre": b["genre"] or "غير مصنّف", "chosen": 0, "shown": 0})
        g["chosen"] += b["chosen"]
        g["shown"] += b["shown"]
    out["genres"] = sorted([g for g in genres.values() if g["chosen"] or g["shown"]], key=lambda g: (-g["chosen"], -g["shown"]))[:12]
    rw = f"date(f.created_at, '{_SHIFT}') BETWEEN ? AND ?" if start else f"date(f.created_at, '{_SHIFT}') <= ?"
    out["recent_ratings"] = [dict(r) for r in conn.execute(
        f"""SELECT f.created_at, f.rating, b.title FROM feedback f LEFT JOIN books b ON b.book_id = f.book_id
            WHERE {rw} ORDER BY f.feedback_id DESC LIMIT 15""", ([start, end] if start else [end]))]
    conn.close()
    return out
