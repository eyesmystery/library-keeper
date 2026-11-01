"""GRRATE (Ibrahim 2026-10-30 «ابدا الاصلاح كله»): store the Goodreads readers' average rating we already read for a book
(research/reviews_gr/<book_id>.json: «avg», «ratings_count», the page «goodreads», read when the file was written) in
books.goodreads_rating / goodreads_ratings_count / goodreads_rating_source / goodreads_rating_at. Nothing is fetched or
estimated here (rule 12: the page it was read on is the source). A book whose stored Goodreads page is a different book
page than the research file's is skipped (the rating would belong to another edition).

  backend/venv/bin/python scripts/import_goodreads_ratings.py [--research <folder with reviews_gr>] [--apply]"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import models  # noqa: E402

SHOW = re.compile(r"goodreads\.com/book/show/(\d+)")


def read(research):
    out = {}
    for path in sorted(glob.glob(os.path.join(research, "reviews_gr", "*.json"))):
        try:
            d = json.load(open(path, encoding="utf-8"))
            bid = int(d.get("book_id"))
            avg = float(d.get("avg"))
            cnt = int(str(d.get("ratings_count") or 0).replace(",", ""))
        except (OSError, ValueError, TypeError):
            continue
        if not (0 < avg <= 5) or cnt <= 0 or not d.get("goodreads"):
            continue
        at = datetime.datetime.fromtimestamp(os.path.getmtime(path), datetime.timezone.utc).strftime("%Y-%m-%d")
        out[bid] = (round(avg, 2), cnt, str(d["goodreads"]), at)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--research", default=os.path.join(ROOT, "research"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    models.init_db()
    conn = models.get_connection()
    found = read(a.research)
    todo, other_page = {}, 0
    for bid, (avg, cnt, page, at) in found.items():
        row = conn.execute("SELECT goodreads_url FROM books WHERE book_id = ?", (bid,)).fetchone()
        if row is None:
            continue
        m1, m2 = SHOW.search(row[0] or ""), SHOW.search(page)
        if m1 and m2 and m1.group(1) != m2.group(1):
            other_page += 1
            continue
        todo[bid] = (avg, cnt, page, at)
    print(f"ratings in research {len(found)} · to write {len(todo)} · skipped (another Goodreads page stored) {other_page}")
    print(f"  with 50+ ratings: {sum(1 for v in todo.values() if v[1] >= 50)}")
    if not a.apply:
        print("(dry run: nothing written)")
        return
    with conn:
        for bid, (avg, cnt, page, at) in todo.items():
            conn.execute("UPDATE books SET goodreads_rating = ?, goodreads_ratings_count = ?, goodreads_rating_source = ?, "
                         "goodreads_rating_at = ? WHERE book_id = ?", (avg, cnt, page, at, bid))
        conn.execute("INSERT INTO audit_log (at, username, action, target, details) VALUES (?, ?, ?, ?, ?)",
                     (time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "MGR (Claude)", "books.goodreads_rating_filled",
                      "catalog", json.dumps({"written": len(todo), "skipped_other_page": other_page}, ensure_ascii=False)))
    print(f"written {len(todo)}; integrity {conn.execute('PRAGMA integrity_check').fetchone()[0]}")


if __name__ == "__main__":
    main()
