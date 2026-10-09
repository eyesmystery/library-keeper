"""Fetch missing book covers: Open Library (by ISBN) -> Goodreads -> Abjjad.

Run on the Mac (needs the internet):   ./fetch_covers.command            (all books without a cover)
Options:
  --limit N           stop after N books (default: all)
  --book-id ID        only this book (can repeat)
  --sources LIST      e.g. openlibrary,goodreads   (default: openlibrary,goodreads,abjjad)
  --include-inactive  also books that are inactive / out of stock (default: in-stock books first, then the rest)
  --delay SECONDS     pause between books (default 1.0, be polite to the sites)
  --records DIR       search covers (Open Library -> Goodreads -> Abjjad) for the identified books of a
                      research folder that still have no cover
  --urls FILE         instead: download the image URLs found during research ({"book_id": "https://…"},
                      written by scripts/import_enrichment.py as covers_todo.json); skips books that have a cover
Books still without a cover are listed in reports/covers_missing.csv for a web search during research
(scripts/import_enrichment.py stores a found image URL) or a staff upload in the admin page.
"""
import argparse
import glob
import csv
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import covers  # noqa: E402
import models  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--book-id", type=int, action="append")
    ap.add_argument("--sources", default=",".join(covers.AUTO_SOURCES))
    ap.add_argument("--include-inactive", action="store_true")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--report", default=os.path.join(ROOT, "reports", "covers_missing.csv"))
    ap.add_argument("--urls")
    ap.add_argument("--records", help="research folder: search covers for its identified books that still have none")
    args = ap.parse_args(argv)
    if args.urls:
        return from_urls(args.urls, args.delay)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    bad = [s for s in sources if s not in covers.FETCHERS]
    if bad:
        ap.error("unknown source: " + ", ".join(bad))

    models.init_db()
    conn = models.get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM books ORDER BY (active = 1 AND quantity > 0) DESC, book_id")]
    conn.close()
    if args.records:
        ids = set()
        for f in glob.glob(os.path.join(args.records, "*.json")):
            if os.path.basename(f).startswith(("_", "import_report", "covers_")):
                continue
            d = json.load(open(f, encoding="utf-8"))
            if (d.get("match") or {}).get("status") == "matched" and isinstance(d.get("book_id"), int):
                ids.add(d["book_id"])
        rows = [r for r in rows if r["book_id"] in ids and not covers.has_local_cover(r)]
    elif args.book_id:
        rows = [r for r in rows if r["book_id"] in args.book_id]
    elif not args.include_inactive:
        rows = [r for r in rows if r["active"] and (r["quantity"] or 0) > 0]
    todo = [r for r in rows if args.book_id or not covers.has_local_cover(r)]
    if args.limit:
        todo = todo[:args.limit]

    print(f"Books to try: {len(todo)}  (sources: {' -> '.join(sources)})", flush=True)
    found, missing = {}, []
    for n, book in enumerate(todo, 1):
        try:
            got = covers.fetch_cover(book, sources)
        except Exception as e:  # one bad book must not stop the run
            got, err = None, str(e)[:120]
        else:
            err = ""
        if got:
            found[got["source"]] = found.get(got["source"], 0) + 1
            print(f"[{n}/{len(todo)}] OK   {got['source']:<11} {book['title']}", flush=True)
        else:
            missing.append(book)
            print(f"[{n}/{len(todo)}] --   not found   {book['title']} {err}", flush=True)
        if n < len(todo):
            time.sleep(args.delay)

    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["book_id", "title", "author", "isbn", "barcode"])
        for b in missing:
            w.writerow([b["book_id"], b["title"], b["author"], b.get("isbn") or "", b.get("barcode") or ""])
    print("\nFound:", ", ".join(f"{k} {v}" for k, v in found.items()) or "none")
    print(f"Still without a cover: {len(missing)}  -> {args.report}")
    return 0


def from_urls(path, delay):
    models.init_db()
    todo = json.load(open(path, encoding="utf-8"))
    ok = skipped = failed = 0
    for n, (bid, url) in enumerate(todo.items(), 1):
        book = models.get_book(int(bid))
        if book is None:
            failed += 1
            print(f"[{n}/{len(todo)}] --   book {bid} not in the catalog")
            continue
        if covers.has_local_cover(dict(book)):
            skipped += 1
            continue
        try:
            got = covers.save_from_url(int(bid), url)
            ok += 1
            print(f"[{n}/{len(todo)}] OK   {got['cover_source']:<11} {book['title']}", flush=True)
        except Exception as e:
            # the researched page gave nothing: try the automatic search (Open Library -> Goodreads -> Abjjad)
            try:
                auto = covers.fetch_cover(dict(book))
            except Exception:
                auto = None
            if auto:
                ok += 1
                print(f"[{n}/{len(todo)}] OK   {auto['source']:<11} {book['title']}  (search)", flush=True)
            else:
                failed += 1
                print(f"[{n}/{len(todo)}] --   {book['title']}: {str(e)[:140]}", flush=True)
        time.sleep(delay)
    print(f"\nSaved {ok}, already had a cover {skipped}, failed {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
