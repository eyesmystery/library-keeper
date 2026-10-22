"""Build backend/picks_signals.json for «اختياراتنا هذا الشهر» (PICKS, Ibrahim 2026-10-14).

Runs on Ibrahim's side once a month, before the update is built; the file travels with the code, so the
library's computer never reads the outside sites. Two kinds of signal, both only for books we hold:

  lists    the month's best-seller / trending lists (نيل وفرات، جرير، أبجد …), saved by hand or by Claude as
           research/picks_<YYYY-MM>/lists.json:
             [{"key": "neelwafurat", "label": "الأكثر مبيعًا في نيل وفرات", "url": "...", "fetched_at": "...",
               "items": [{"rank": 1, "title": "...", "author": "..."}]}]
           Each item is matched to an in-stock book by its title (normalized), and by the author too when the
           title alone is ambiguous or the author is given; unmatched items are listed in the report only.
  ratings  stored reader ratings with their counts: Goodreads pages fetched by the reviews track
           (research/reviews_gr/<book_id>.json: avg, ratings_count), else the Abjjad ratings we read
           (research/reviews/<book_id>.json: the list of individual ratings).

Usage (from the project folder):
    backend/venv/bin/python scripts/build_pick_signals.py --month 2026-10 [--dry-run]
Reads the live database read-only. Writes backend/picks_signals.json and research/picks_<month>/report.md.
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from keyword_prefs import normalize  # noqa: E402

OUT = os.path.join(ROOT, "backend", "picks_signals.json")


def key(text):
    return " ".join(normalize(text or "").split())


def main_key(text):
    """The title before its subtitle («قدر في هذا المشرق - من الحرب…», «أبي الذي أكره ؛ تأملات…», «005 : الجزء…»)."""
    import re
    head = re.split(r"\s[-–—]\s|[:؛;(]", text or "", maxsplit=1)[0]
    return key(head)


def load_books(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT book_id, title, author FROM books WHERE active = 1 AND quantity > 0")]
    con.close()
    return rows


def match_lists(lists, books):
    by_title, by_main = {}, {}
    for b in books:
        by_title.setdefault(key(b["title"]), []).append(b)
        by_main.setdefault(main_key(b["title"]), []).append(b)
    out, unmatched = [], []
    for lst in lists:
        found, seen = [], set()
        for item in lst.get("items") or []:
            hits = by_title.get(key(item.get("title")), [])
            if not hits and len(main_key(item.get("title"))) >= 3:
                hits = by_main.get(main_key(item.get("title")), [])   # the same title without its subtitle
            if item.get("author") and hits:
                a = key(item["author"])
                same = [b for b in hits if a and (a in key(b["author"]) or key(b["author"]) in a)]
                hits = same or (hits if len(hits) == 1 and not key(hits[0]["author"]) else [])
            if len({key(b["author"]) for b in hits}) > 1:      # two different works with this title: leave it
                hits = []
            if not hits:
                unmatched.append((lst.get("label"), item))
                continue
            for b in hits:                                      # every in-stock edition of the same work
                if b["book_id"] not in seen:
                    seen.add(b["book_id"])
                    found.append({"book_id": b["book_id"], "rank": item.get("rank")})
        out.append({"key": lst.get("key"), "label": lst.get("label"), "url": lst.get("url"),
                    "fetched_at": lst.get("fetched_at"), "books": found})
    return out, unmatched


def load_ratings(stock_ids):
    ratings = {}
    for path in glob.glob(os.path.join(ROOT, "research", "reviews_gr", "*.json")):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        bid = d.get("book_id") if isinstance(d, dict) else None
        if bid in stock_ids and d.get("ratings_count") and d.get("avg"):
            ratings[str(bid)] = {"avg": round(float(d["avg"]), 2), "count": int(d["ratings_count"]), "source": "goodreads"}
    for path in glob.glob(os.path.join(ROOT, "research", "reviews", "*.json")):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        bid = d.get("book_id") if isinstance(d, dict) else None
        vals = [v for v in (d.get("ratings") or []) if isinstance(v, (int, float)) and 0 < v <= 5] if isinstance(d, dict) else []
        if bid in stock_ids and vals and str(bid) not in ratings:
            ratings[str(bid)] = {"avg": round(sum(vals) / len(vals), 2), "count": len(vals), "source": "abjjad"}
    return ratings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "library.db"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    books = load_books(a.db)
    stock_ids = {b["book_id"] for b in books}
    folder = os.path.join(ROOT, "research", f"picks_{a.month}")
    lists_path = os.path.join(folder, "lists.json")
    lists = json.load(open(lists_path, encoding="utf-8")) if os.path.exists(lists_path) else []
    matched, unmatched = match_lists(lists, books)
    ratings = load_ratings(stock_ids)
    built = datetime.now(timezone(timedelta(hours=3))).isoformat(timespec="seconds")
    data = {"month": a.month, "built_at": built, "lists": matched, "ratings": ratings}
    lines = [f"# Pick signals {a.month} — built {built} (Riyadh)", "",
             f"In-stock books: {len(books)}; stored ratings for {len(ratings)} of them "
             f"({sum(1 for r in ratings.values() if r['source'] == 'goodreads')} Goodreads, "
             f"{sum(1 for r in ratings.values() if r['source'] == 'abjjad')} Abjjad).", ""]
    for lst, src in zip(matched, lists):
        lines.append(f"- {lst['label']}: {len(src.get('items') or [])} items, {len(lst['books'])} of our in-stock books matched ({lst['url']})")
    if unmatched:
        lines += ["", "## Not in our stock (or not matched)", ""]
        lines += [f"- {lab}: #{it.get('rank')} {it.get('title')} — {it.get('author') or ''}" for lab, it in unmatched]
    report = "\n".join(lines) + "\n"
    print(report)
    if a.dry_run:
        return
    with open(OUT + ".part", "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=0)
    os.replace(OUT + ".part", OUT)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    print("written", OUT)


if __name__ == "__main__":
    main()
