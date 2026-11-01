"""GRURL (Ibrahim 2026-10-30 «اضف لي كم مطور في الفلتر في المكتبة الكتب اللي ما عندنا لها رابط في goodreads»): fill
books.goodreads_url (the book's own Goodreads page) from what our research already holds, with where it was found
(rule 12). Only empty values are filled; nothing is guessed or searched here.

Sources, first found wins:
  reviews_gr   research/reviews_gr/<book_id>.json «goodreads» (the page the readers' reviews were read from, matched
               by ISBN or by title and author at the time)
  summary      books.summary_source when the summary was written from a Goodreads page
  evidence     attribute_evidence.source_urls (the most frequent Goodreads book page of the book's evidence)
  original_year books.original_year_ref when the first-publication year was read on Goodreads

  backend/venv/bin/python scripts/import_goodreads_urls.py [--research <folder with reviews_gr>] [--apply]"""
import argparse
import collections
import glob
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import models  # noqa: E402

GR = re.compile(r"https?://(?:www\.)?goodreads\.com/book/show/(\d+)")


def canon(text):
    m = GR.search(str(text or ""))
    return f"https://www.goodreads.com/book/show/{m.group(1)}" if m else None


def collect(conn, research):
    found = {}

    def put(bid, url, src):
        if url and bid not in found:
            found[bid] = (url, src)
    for path in sorted(glob.glob(os.path.join(research, "reviews_gr", "*.json"))):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(d.get("book_id") or "").isdigit():
            put(int(d["book_id"]), canon(d.get("goodreads")), "reviews_gr (" + os.path.basename(path) + ")")
    for bid, s in conn.execute("SELECT book_id, summary_source FROM books WHERE summary_source LIKE '%goodreads.com/book/show%'"):
        put(bid, canon(s), "summary_source")
    per = collections.defaultdict(collections.Counter)
    for bid, s in conn.execute("SELECT book_id, source_urls FROM attribute_evidence WHERE source_urls LIKE '%goodreads.com/book/show%'"):
        for m in GR.finditer(str(s)):
            per[bid][f"https://www.goodreads.com/book/show/{m.group(1)}"] += 1
    for bid, c in per.items():
        put(bid, c.most_common(1)[0][0], "evidence")
    for bid, s in conn.execute("SELECT book_id, original_year_ref FROM books WHERE original_year_ref LIKE '%goodreads.com/book/show%'"):
        put(bid, canon(s), "original_year_ref")
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--research", default=os.path.join(ROOT, "research"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    models.init_db()            # the goodreads_url columns (models._migrate_schema)
    conn = models.get_connection()
    found = collect(conn, a.research)
    have = {r[0] for r in conn.execute("SELECT book_id FROM books WHERE goodreads_url IS NOT NULL AND goodreads_url <> ''")}
    books = {r[0] for r in conn.execute("SELECT book_id FROM books")}
    todo = {b: v for b, v in found.items() if b in books and b not in have}
    by_src = collections.Counter(v[1].split(" ")[0] for v in todo.values())
    active = {r[0] for r in conn.execute("SELECT book_id FROM books WHERE active = 1")}
    print(f"found {len(found)} · already filled {len(have)} · to fill {len(todo)} {dict(by_src)}")
    print(f"active books without a Goodreads page after this: {len(active - have - set(todo))} of {len(active)}")
    if not a.apply:
        print("(dry run: nothing written)")
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    with conn:
        for bid, (url, src) in todo.items():
            conn.execute("UPDATE books SET goodreads_url = ?, goodreads_url_source = ? WHERE book_id = ? AND (goodreads_url IS NULL OR goodreads_url = '')",
                         (url, src, bid))
        conn.execute("INSERT INTO audit_log (at, username, action, target, details) VALUES (?, ?, ?, ?, ?)",
                     (now, "MGR (Claude)", "books.goodreads_url_filled", "catalog",
                      json.dumps({"filled": len(todo), "by_source": dict(by_src)}, ensure_ascii=False)))
    print(f"filled {len(todo)}; integrity {conn.execute('PRAGMA integrity_check').fetchone()[0]}")


if __name__ == "__main__":
    main()
