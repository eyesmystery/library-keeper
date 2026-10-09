"""Relations between books (card v2 §12.5, approved 2026-09-28).

Computed, never researched: they come only from bibliographic facts (author, series) and from
card values, and every relation stores the reason it was made. Visitors are only offered
targets that are in stock and suitable (checked where they are used, see app.py).

Types: same_author, series_prev, series_next, similar, lighter_alternative,
shorter_alternative, easier_alternative. Rebuilt in full by rebuild() (cheap: only books with
known authors/series or a researched card take part).
"""
import json
import re
from datetime import datetime, timezone

from models import get_connection

LENGTH = ["very_short", "short", "medium", "long", "very_long"]
WEIGHT = ["light", "medium", "heavy", "intense"]
LANG = ["very_easy", "easy", "medium", "advanced"]
LENGTH_AR = {"very_short": "قصير جدًا", "short": "قصير", "medium": "متوسط الطول", "long": "طويل", "very_long": "طويل جدًا"}
WEIGHT_AR = {"light": "خفيف", "medium": "متوسط", "heavy": "ثقيل", "intense": "شديد"}
LANG_AR = {"very_easy": "سهلة جدًا", "easy": "سهلة", "medium": "متوسطة", "advanced": "متقدمة"}
MOOD_AR = {"comforting": "مريحة", "warm": "دافئة", "funny": "مرحة", "suspenseful": "مشوقة", "mysterious": "غامضة",
           "inspiring": "ملهمة", "contemplative": "تأملية", "romantic": "رومانسية", "tense": "متوترة",
           "dark": "قاتمة", "sad": "حزينة", "uplifting": "مبهجة"}
TYPES_AR = {"same_author": "للمؤلف نفسه", "series_prev": "الجزء السابق", "series_next": "الجزء التالي",
            "similar": "مشابه", "lighter_alternative": "بديل أخف", "shorter_alternative": "بديل أقصر",
            "easier_alternative": "بديل بلغة أسهل"}
PER_TYPE = {"same_author": 5, "similar": 4, "lighter_alternative": 2, "shorter_alternative": 2, "easier_alternative": 2}
CARD_STATUSES = ("enriched", "needs_review", "approved")


def _norm(text):
    t = re.sub(r"[ً-ْـ]", "", str(text or "")).strip().lower()
    t = re.sub("[إأآٱ]", "ا", t).replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


def _jl(v):
    try:
        x = json.loads(v or "[]")
        return x if isinstance(x, list) else []
    except ValueError:
        return []


def _order(scale, v):
    return scale.index(v) if v in scale else None


def _shared_text(themes, moods):
    parts = []
    if themes:
        parts.append("يتقاطعان في: " + "، ".join(themes[:3]))
    if moods:
        parts.append("أجواء " + "، ".join(MOOD_AR.get(m, m) for m in moods[:2]))
    return "؛ ".join(parts)


def compute(books, profiles):
    """-> list of (source_id, target_id, type, reason_ar, confidence)."""
    out = []
    # same author
    by_author = {}
    for b in books:
        a = _norm(b.get("author"))
        if a and a not in ("غير معروف", "unknown"):
            by_author.setdefault(a, []).append(b)
    for group in by_author.values():
        for s in group:
            for t in group:
                if s["book_id"] != t["book_id"] and _norm(s["title"]) != _norm(t["title"]):
                    out.append((s["book_id"], t["book_id"], "same_author", f"من كتب {t['author']} أيضًا", 0.9))
    # series
    by_series = {}
    for b in books:
        if b.get("series_name") and isinstance(b.get("series_position"), int):
            by_series.setdefault(_norm(b["series_name"]), []).append(b)
    for group in by_series.values():
        for s in group:
            for t in group:
                if t["series_position"] == s["series_position"] - 1:
                    out.append((s["book_id"], t["book_id"], "series_prev", f"الجزء {t['series_position']} من {t['series_name']}", 0.9))
                if t["series_position"] == s["series_position"] + 1:
                    out.append((s["book_id"], t["book_id"], "series_next", f"الجزء {t['series_position']} من {t['series_name']}", 0.9))
    # card-based: only researched cards, same format
    carded = []
    for b in books:
        p = profiles.get(b["book_id"])
        if p and p.get("overall_status") in CARD_STATUSES and p.get("format") \
                and p.get("series_dependency") != "requires_previous":  # a later volume is never an alternative
            carded.append((b, p, set(_jl(p.get("themes"))), set(_jl(p.get("moods")))))
    by_format = {}
    for item in carded:
        by_format.setdefault(item[1]["format"], []).append(item)
    for group in by_format.values():
        for sb, sp, sth, smo in group:
            for tb, tp, tth, tmo in group:
                if sb["book_id"] == tb["book_id"] or _norm(sb["title"]) == _norm(tb["title"]):
                    continue
                if (sb.get("language") or "ar") != (tb.get("language") or "ar"):
                    continue  # a visitor reading this edition is offered books in the same language
                th, mo = sorted(sth & tth), sorted(smo & tmo)
                score = 2 * len(th) + len(mo) + int(bool(sp.get("pace")) and sp.get("pace") == tp.get("pace")) \
                    + int(bool(sp.get("emotional_weight")) and sp.get("emotional_weight") == tp.get("emotional_weight"))
                if score >= 3 and (th or len(mo) >= 2):
                    out.append((sb["book_id"], tb["book_id"], "similar", _shared_text(th, mo) or "قريب منه في الأجواء", round(min(0.6 + 0.05 * score, 0.85), 2)))
                if not (th or len(mo) >= 2):
                    continue  # an alternative must share a theme or at least two moods
                link = _shared_text(th, mo)
                sw, tw = _order(WEIGHT, sp.get("emotional_weight")), _order(WEIGHT, tp.get("emotional_weight"))
                if sw is not None and tw is not None and tw < sw:
                    out.append((sb["book_id"], tb["book_id"], "lighter_alternative", f"أخف عاطفيًا ({WEIGHT_AR[tp['emotional_weight']]} بدل {WEIGHT_AR[sp['emotional_weight']]})؛ {link}", 0.7))
                sl, tl = _order(LENGTH, sp.get("perceived_length")), _order(LENGTH, tp.get("perceived_length"))
                if sl is not None and tl is not None and tl < sl:
                    out.append((sb["book_id"], tb["book_id"], "shorter_alternative", f"أقصر ({LENGTH_AR[tp['perceived_length']]} بدل {LENGTH_AR[sp['perceived_length']]})؛ {link}", 0.7))
                sd, td = _order(LANG, sp.get("language_difficulty")), _order(LANG, tp.get("language_difficulty"))
                if sd is not None and td is not None and td < sd:
                    out.append((sb["book_id"], tb["book_id"], "easier_alternative", f"لغته {LANG_AR[tp['language_difficulty']]} بدل {LANG_AR[sp['language_difficulty']]}؛ {link}", 0.7))
    # keep the strongest few per (source, type)
    out.sort(key=lambda r: (r[0], r[2], -r[4]))
    kept, count = [], {}
    for r in out:
        k = (r[0], r[2])
        if count.get(k, 0) < PER_TYPE.get(r[2], 3):
            kept.append(r)
            count[k] = count.get(k, 0) + 1
    return kept


def rebuild():
    conn = get_connection()
    try:
        books = [dict(r) for r in conn.execute("SELECT * FROM books")]
        profiles = {r["book_id"]: dict(r) for r in conn.execute("SELECT * FROM book_profiles")}
        rows = compute(books, profiles)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute("BEGIN")
        conn.execute("DELETE FROM book_relations WHERE source = 'computed'")
        conn.executemany("""INSERT INTO book_relations (source_book_id, target_book_id, relation_type, reason_ar,
                            confidence, source, created_at) VALUES (?, ?, ?, ?, ?, 'computed', ?)""",
                         [(*r, now) for r in rows])
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def for_books(book_ids):
    """-> {source_id: [relation rows with target_title]} for several books at once."""
    ids = [int(i) for i in book_ids]
    if not ids:
        return {}
    conn = get_connection()
    try:
        try:
            rows = conn.execute(
                f"""SELECT r.*, b.title AS target_title FROM book_relations r JOIN books b ON b.book_id = r.target_book_id
                    WHERE r.source_book_id IN ({','.join('?' * len(ids))}) ORDER BY r.confidence DESC""", ids).fetchall()
        except Exception:  # table missing on a database that was never migrated
            return {}
        out = {}
        for r in rows:
            out.setdefault(r["source_book_id"], []).append(dict(r))
        return out
    finally:
        conn.close()


def for_book(book_id, conn=None):
    own = conn is None
    conn = conn or get_connection()
    try:
        return [dict(r) for r in conn.execute(
            """SELECT r.*, b.title AS target_title FROM book_relations r JOIN books b ON b.book_id = r.target_book_id
               WHERE r.source_book_id = ? ORDER BY r.relation_type, r.confidence DESC""", (book_id,))]
    finally:
        if own:
            conn.close()
