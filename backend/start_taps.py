"""Taps on the three start choices of the visitor page (STARTTAP, Ibrahim 2026-11-01 «تمام اضف العداد»).

On 2026-10-30 the two links under the big button were made smaller so more visitors start with the chat
(«الزوار صاروا يبدؤون مع ماذا تقرأ الان اكثر … ابغى اقلل اهميتها»). This counter shows whether it worked:
how many taps each of the three gets.

  chat     «خلّنا نلقى كتابك»            (#startBtn, opens the chat)
  reading  «قرأت كتابًا؟ شاركنا رأيك»     (#readingBtn)
  shelf    «اقترح كتابًا لرفوفنا»          (#shelfStartBtn)

Numbers only: one row per (day in Riyadh time, choice, language) with a count. No address, no session or
conversation id, no browser, no text. Not counted: taps while «وضع الاختبار» is on, and taps from a browser
signed in to the admin page (staff trying the page). The per-address limit lives in memory only.
Shown in «إحصائيات الزوار» (insights.summary) and in the inbox file (inbox_export, section 14).
"""
import time
from collections import deque
from datetime import date, timedelta

from models import get_connection

CHOICES = ("chat", "reading", "shelf")
LABELS_AR = {"chat": "خلّنا نلقى كتابك", "reading": "قرأت كتابًا؟ شاركنا رأيك", "shelf": "اقترح كتابًا لرفوفنا"}
SHORT_AR = {"chat": "المحادثة", "reading": "قرأت كتابًا", "shelf": "اقترح كتابًا"}
# the two links were made smaller on 2026-10-30 at 19:00 Riyadh. Rows are per day, so that day itself is
# left out of a before/after comparison: before = up to 2026-10-29, after = from 2026-10-31.
LINKS_SMALLER_DAY = "2026-10-30"
RATE = (30, 60)          # taps per address per seconds (one phone cannot inflate the numbers)
_hits = {}


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS start_taps (
        day TEXT NOT NULL, choice TEXT NOT NULL, language TEXT NOT NULL, taps INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, choice, language))""")


def clean_choice(value):
    return value if isinstance(value, str) and value in CHOICES else None


def allow(address):
    """False when this address tapped more than RATE allows. Kept in memory only, never stored."""
    limit, window = RATE
    now = time.time()
    key = str(address)
    q = _hits.get(key)
    if q is None:
        if len(_hits) > 5000:            # drop addresses that have been quiet for a whole window
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > window]:
                del _hits[k]
        q = _hits[key] = deque()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def reset():
    _hits.clear()


def record(choice, language="ar", day=None):
    """Add one tap. choice must be one of CHOICES (ValueError otherwise)."""
    if clean_choice(choice) is None:
        raise ValueError("unknown choice")
    if day is None:
        import insights          # local import: insights imports this module
        day = insights.local_day()
    lang = "en" if language == "en" else "ar"
    conn = get_connection()
    try:
        ensure_table(conn)
        conn.execute("INSERT INTO start_taps (day, choice, language, taps) VALUES (?, ?, ?, 1) "
                     "ON CONFLICT(day, choice, language) DO UPDATE SET taps = taps + 1", (day, choice, lang))
        conn.commit()
    finally:
        conn.close()


def _counts(conn, where="", args=()):
    out = {c: 0 for c in CHOICES}
    for row in conn.execute(f"SELECT choice, SUM(taps) n FROM start_taps {where} GROUP BY choice", args):
        if row["choice"] in out:
            out[row["choice"]] = row["n"] or 0
    return out


def _with_share(counts):
    total = sum(counts.values())
    return {"total": total, "counts": counts,
            "pct": {c: (round(100 * n / total) if total else None) for c, n in counts.items()}}


def summary(start=None, end=None):
    """The three counts for the period [start, end] (Riyadh days, start None = from the first tap), their share,
    the day counting started, and a before/after split around LINKS_SMALLER_DAY when there are taps before it."""
    conn = get_connection()
    try:
        ensure_table(conn)
        where, args = ("WHERE day BETWEEN ? AND ?", [start, end]) if start else ("WHERE day <= ?", [end]) if end else ("", [])
        out = _with_share(_counts(conn, where, args))
        out["started"] = conn.execute("SELECT MIN(day) FROM start_taps WHERE taps > 0").fetchone()[0]
        out["links_smaller_day"] = LINKS_SMALLER_DAY
        out["compare"] = None
        if out["started"] and out["started"] < LINKS_SMALLER_DAY:
            sep = " AND " if where else "WHERE "
            before = _counts(conn, where + sep + "day < ?", args + [LINKS_SMALLER_DAY])
            after = _counts(conn, where + sep + "day > ?", args + [LINKS_SMALLER_DAY])
            if sum(before.values()):
                out["compare"] = {"before": _with_share(before), "after": _with_share(after)}
        return out
    finally:
        conn.close()


def daily(days=30, today=None):
    """-> list of {day, chat, reading, shelf, total}, oldest first, only days with taps; days=None: every day."""
    if today is None:
        import insights
        today = insights.local_day()
    conn = get_connection()
    try:
        ensure_table(conn)
        where, args = "", []
        if days:
            first = (date.fromisoformat(today) - timedelta(days=days - 1)).isoformat()
            where, args = "WHERE day BETWEEN ? AND ?", [first, today]
        rows = {}
        for r in conn.execute(f"SELECT day, choice, SUM(taps) n FROM start_taps {where} GROUP BY day, choice ORDER BY day", args):
            d = rows.setdefault(r["day"], {"day": r["day"], **{c: 0 for c in CHOICES}})
            if r["choice"] in CHOICES:
                d[r["choice"]] = r["n"] or 0
        started = conn.execute("SELECT MIN(day) FROM start_taps WHERE taps > 0").fetchone()[0]
    finally:
        conn.close()
    out = [dict(d, total=sum(d[c] for c in CHOICES)) for d in rows.values()]
    return [d for d in out if d["total"]], started
