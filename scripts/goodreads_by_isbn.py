"""GRURL by ISBN (Ibrahim 2026-10-30 «ابغاك انت ترجع تبحث الحين عن الكتب كلها اللي لها ISBN وتضيف رابط goodreads والباقي
انا اكمله»): for every book with an ISBN and no books.goodreads_url, ask Goodreads' ISBN address
https://www.goodreads.com/book/isbn/<isbn> (allowed by its robots.txt). A known ISBN answers with a redirect to the
book's page /book/show/<id>; that page becomes the book's link, its source «goodreads isbn <isbn> (<date>)» (rule 12).
404 = Goodreads does not know the ISBN (the ISBN-10 form is tried too). Nothing else is written.

Polite: one request every DELAY seconds, the page itself is never downloaded (the redirect is enough); a bot check
(202/429/503) rests 15 minutes and asks again. Resumable: every answer is kept in research/gr_isbn_2026-10-30/answers.jsonl
and an answered ISBN is not asked again.

  nohup backend/venv/bin/python scripts/goodreads_by_isbn.py > research/gr_isbn_2026-10-30/_run.log 2>&1 &"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import covers  # noqa: E402  (isbn_candidates)
import models  # noqa: E402
import net  # noqa: E402

OUT = os.path.join(ROOT, "research", "gr_isbn_2026-10-30")
ANSWERS = os.path.join(OUT, "answers.jsonl")
DELAY = 3.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) LibraryKeeper/1.0 (bookshop catalog; one request every 3 s)"
SHOW = re.compile(r"^https://www\.goodreads\.com/book/show/(\d+)")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def ask(isbn):
    """-> ("found", url) | ("unknown", None) | ("busy", code)."""
    req = urllib.request.Request(f"https://www.goodreads.com/book/isbn/{isbn}", method="GET", headers={"User-Agent": UA})
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=net.ssl_context()))
    try:
        with opener.open(req, timeout=30) as r:
            m = SHOW.match(r.geturl())
            return ("found", f"https://www.goodreads.com/book/show/{m.group(1)}") if m else ("busy", r.status)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            loc = urllib.parse.urljoin("https://www.goodreads.com/", e.headers.get("Location") or "")
            m = SHOW.match(loc)
            return ("found", f"https://www.goodreads.com/book/show/{m.group(1)}") if m else ("unknown", None)
        if e.code == 404:
            return ("unknown", None)
        return ("busy", e.code)


def main():
    os.makedirs(OUT, exist_ok=True)
    done = {}
    if os.path.exists(ANSWERS):
        for line in open(ANSWERS, encoding="utf-8"):
            try:
                a = json.loads(line)
                done[a["isbn"]] = a
            except ValueError:
                pass
    conn = models.get_connection()
    rows = conn.execute("SELECT book_id, title, isbn FROM books WHERE active = 1 AND isbn IS NOT NULL AND trim(isbn) <> '' "
                        "AND (goodreads_url IS NULL OR goodreads_url = '') ORDER BY (quantity > 0) DESC, book_id").fetchall()
    conn.close()
    print(time.strftime("%H:%M"), f"{len(rows)} books with an ISBN and no link; {len(done)} ISBNs answered before", flush=True)
    found = 0
    stamp = time.strftime("%Y-%m-%d")
    for i, (bid, title, isbn) in enumerate(rows, 1):
        url = None
        for cand in covers.isbn_candidates(isbn)[:2]:
            a = done.get(cand)
            while a is None:
                kind, val = ask(cand)
                if kind == "busy":
                    print(time.strftime("%H:%M"), f"Goodreads busy ({val}), resting 15 min", flush=True)
                    time.sleep(900)
                    continue
                a = {"isbn": cand, "book_id": bid, "result": kind, "url": val, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                done[cand] = a
                with open(ANSWERS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(a, ensure_ascii=False) + "\n")
                time.sleep(DELAY)
            if a["result"] == "found":
                url = (a["url"], cand)
                break
        if url:
            c = sqlite3.connect(models.DB_PATH, timeout=30)
            with c:
                n = c.execute("UPDATE books SET goodreads_url = ?, goodreads_url_source = ? WHERE book_id = ? "
                              "AND (goodreads_url IS NULL OR goodreads_url = '')",
                              (url[0], f"goodreads isbn {url[1]} ({stamp})", bid)).rowcount
            c.close()
            found += n
        if i % 50 == 0:
            print(time.strftime("%H:%M"), f"{i}/{len(rows)} asked, {found} links added", flush=True)
    c = sqlite3.connect(models.DB_PATH, timeout=30)
    with c:
        c.execute("INSERT INTO audit_log (at, username, action, target, details) VALUES (?, ?, ?, ?, ?)",
                  (time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "MGR (Claude)", "books.goodreads_url_by_isbn",
                   "catalog", json.dumps({"books": len(rows), "added": found}, ensure_ascii=False)))
    c.close()
    print(time.strftime("%H:%M"), f"DONE: {found} links added of {len(rows)} books", flush=True)


if __name__ == "__main__":
    main()
