"""Research helper: look books up on Goodreads (and Abjjad) from this computer and save the candidates.

It never decides a match. It writes, per book, the search results (title, authors with roles,
rating counts, Goodreads work slug = original title, cover URL) so a researcher can confirm or
reject each match by title + publisher + author. Polite: one request every --delay seconds.

Usage (from the project folder):
  backend/venv/bin/python scripts/lookup_sources.py --status needs_confirmation --out reports/lookup_nc.json
  backend/venv/bin/python scripts/lookup_sources.py --list research/batch_07/_list.json --out research/batch_07/_lookup.json
Options: --ids 1,2,3   --limit N   --delay 2.5   --no-goodreads --no-abjjad --no-bookhome --no-elain   --retry-empty
Sources: Goodreads, Abjjad, بيت الكتب (thebookhome.com) and, for Dar al-Ain books, the publisher's own site.
Re-running with the same --out skips books already looked up (resumable).
"""
import argparse, html, http.cookiejar, json, os, re, sqlite3, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import net  # noqa: E402

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
NOISE = re.compile(r"\b(DC|[A-Z]\d?|ط\s*\d+|م\s*\.\s*ت|جـ?\s*\d+(\s*ط\s*\d+)?)\b|[()\"]|\s+-\s+|\d+/\d+")


_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                      urllib.request.HTTPSHandler(context=net.ssl_context()))


def get(url, timeout=25):
    """Goodreads redirects through a cookie-setting URL; without a cookie jar it loops."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ar,en;q=0.8"})
    with _OPENER.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def clean_title(t):
    t = NOISE.sub(" ", t or "")
    return re.sub(r"\s+", " ", t).strip(" -:،")


def _text(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def _count(num, unit):
    v = float(num.replace(",", ""))
    return int(v * {"thousand": 1e3, "million": 1e6}.get(unit or "", 1))


def _goodreads_new(page, n):
    """Current Goodreads search layout (served when cookies are kept)."""
    out = []
    for blk in page.split('data-testid="book-item-kca')[1:n + 1]:
        m = re.search(r'data-testid="book-item-title"><a href="/book/show/(\d+)[^"]*">(.*?)</a>', blk, re.S)
        if not m:
            continue
        authors = []
        for a in re.finditer(r'ContributorLink__name" data-testid="name">([^<]+)</span>(<span data-testid="role"[^>]*>(.*?)</span>)?', blk):
            role = re.sub(r"<!--.*?-->|[()\s]", "", a.group(3) or "") or "Author"
            authors.append({"name": html.unescape(a.group(1)).strip(), "role": role})
        r = re.search(r"Average rating of ([\d.]+) stars, ([\d.,]+) ?(thousand|million)? ratings", blk)
        img = re.search(r'src="(https://[^"]+/books/[^"]+\.jpg)"', blk)
        out.append({"id": m.group(1), "url": "https://www.goodreads.com/book/show/" + m.group(1), "title": _text(m.group(2)),
                    "authors": authors, "avg": float(r.group(1)) if r else None,
                    "ratings": _count(r.group(2), r.group(3)) if r else 0, "work_slug": None,
                    "cover": img.group(1) if img else None})
    return out


def goodreads(q, n=5):
    page = get("https://www.goodreads.com/search?q=" + urllib.parse.quote(q))
    if 'data-testid="book-item-kca' in page:
        return _goodreads_new(page, n)
    if 'itemtype="http://schema.org/Book"' not in page and "No results" not in page:
        # neither a result list nor Goodreads' «No results» message: a throttled/stripped page (seen 2026-09-29)
        raise RuntimeError("Goodreads returned a page without a result list (throttled?)")
    out = []
    for row in page.split('itemtype="http://schema.org/Book"')[1:n + 1]:
        m = re.search(r'href="/book/show/(\d+)[^"]*"[^>]*>\s*<span[^>]*>(.*?)</span>', row, re.S)
        if not m:
            continue
        authors = []
        for a in re.finditer(r'itemprop="name">([^<]+)</span></a>\s*(<span class="authorName greyText smallText role">\(([^)]+)\)</span>)?', row):
            authors.append({"name": html.unescape(a.group(1)).strip(), "role": a.group(3) or "Author"})
        r = re.search(r"([\d.]+) avg rating &mdash; ([\d,]+) rating", row)
        w = re.search(r'href="/work/editions/\d+-([^"]+)"', row)
        img = re.search(r'class="bookCover"[^>]*src="([^"]+)"', row)
        out.append({"id": m.group(1), "url": "https://www.goodreads.com/book/show/" + m.group(1), "title": _text(m.group(2)),
                    "authors": authors, "avg": float(r.group(1)) if r else None,
                    "ratings": int(r.group(2).replace(",", "")) if r else 0,
                    "work_slug": w.group(1) if w else None,
                    "cover": re.sub(r"\._S[XY]\d+_", "", img.group(1)) if img else None})
    return out


def abjjad(q, n=3):
    page = get("https://www.abjjad.com/search?kw=" + urllib.parse.quote(q))
    out = []
    for li in page.split('<div class="thebookbadge">')[1:n + 1]:
        m = re.search(r'<h3 class="summary-header"><a href="(/book/[^"]+)"[^>]*>([^<]+)<', li)
        if not m:
            continue
        a = re.search(r'href="/author/[^"]+"[^>]*>([^<]+)<', li)
        out.append({"url": "https://www.abjjad.com" + m.group(1), "title": html.unescape(m.group(2)).strip(),
                    "author": html.unescape(a.group(1)).strip() if a else None})
    return out


def norm_ar(s):
    """Loose Arabic comparison key: no diacritics/tatweel, unified alef/ya/ta marbuta, single spaces."""
    s = re.sub(r"[ً-ْـ]", "", s or "")
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي")):
        s = s.replace(a, b)
    return re.sub(r"[^\w]+", " ", s).strip().lower()


def bookhome(q, n=3):
    """بيت الكتب (thebookhome.com): Egyptian bookshop whose product pages name title, publisher and author.
    Search results give title + image; the product page <title> is «العنوان-الناشر-المؤلف|بيت الكتب»."""
    page = get("https://www.thebookhome.com/Home/SearchResult?searchKey=%s&sortKey=Date%%20desc&pageNumber=1" % urllib.parse.quote(q))
    seen, out, key = set(), [], norm_ar(q)
    for m in re.finditer(r'<a href="(https://www\.thebookhome\.com/product/index/(\d+)/[^"]*)">([^<]+)</a>', page):
        url, pid, title = m.group(1), m.group(2), html.unescape(m.group(3)).strip()
        if pid in seen:
            continue
        seen.add(pid)
        t = norm_ar(title)
        if not (t == key or t.startswith(key + " ") or key.startswith(t + " ")):
            continue  # only titles that match the query; the shop's search is fuzzy
        prod = get(url)
        tm = re.search(r"<title>([^<]*)\|", prod)
        parts = [x.strip() for x in html.unescape(tm.group(1)).split("-")] if tm else []
        img = re.search(r'og:image" content="([^"]+)"', prod)
        cover = img.group(1) if img and "siteDefult" not in img.group(1) else None
        out.append({"url": url, "title": "-".join(parts[:-2]) if len(parts) >= 3 else title,
                    "publisher": parts[-2] if len(parts) >= 3 else None, "author": parts[-1] if len(parts) >= 3 else None,
                    "cover": cover})
        if len(out) >= n:
            break
        time.sleep(1)
    return out


def elain(q, n=3):
    """دار العين (elainpublishinghouse.com), WooCommerce: the author is the product «brand»."""
    page = get("https://elainpublishinghouse.com/?s=" + urllib.parse.quote(q))
    out, key = [], norm_ar(q)
    for url in dict.fromkeys(re.findall(r'href="(https://elainpublishinghouse\.com/books/[^"#]+/)"', page)):
        prod = get(url)
        tm = re.search(r"<title>([^<&]*)", prod)
        title = html.unescape(tm.group(1)).strip() if tm else ""
        t = norm_ar(title)
        if not (t == key or t.startswith(key + " ") or key.startswith(t + " ")):
            continue
        am = re.search(r"wd-single-brands.*?<a [^>]*>\s*([^<]+?)\s*</a>", prod, re.S)
        img = re.search(r'<img[^>]+class="[^"]*wp-post-image[^"]*"[^>]+src="([^"]+)"', prod) or \
            re.search(r'<img[^>]+src="([^"]+)"[^>]+class="[^"]*wp-post-image', prod)
        out.append({"url": url, "title": title, "publisher": "دار العين", "author": html.unescape(am.group(1)).strip() if am else None,
                    "cover": img.group(1) if img else None})
        if len(out) >= n:
            break
    return out


def books(args):
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, "data", "library.db"), uri=True)
    con.row_factory = sqlite3.Row
    if args.list:
        ids = [b["book_id"] for b in json.load(open(args.list, encoding="utf-8"))]
    elif args.ids:
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
    else:
        ids = [r[0] for r in con.execute("SELECT book_id FROM books WHERE research_status=? ORDER BY book_id", (args.status,))]
    rows = {r["book_id"]: dict(r) for r in con.execute(
        "SELECT book_id, title, publisher, isbn FROM books WHERE book_id IN (%s)" % ",".join("?" * len(ids)), ids)} if ids else {}
    return [rows[i] for i in ids if i in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status"); ap.add_argument("--ids"); ap.add_argument("--list")
    ap.add_argument("--out", required=True); ap.add_argument("--limit", type=int)
    ap.add_argument("--delay", type=float, default=2.5); ap.add_argument("--no-abjjad", action="store_true")
    ap.add_argument("--no-goodreads", action="store_true", help="skip Goodreads (while it throttles)")
    ap.add_argument("--no-bookhome", action="store_true"); ap.add_argument("--no-elain", action="store_true")
    ap.add_argument("--pause-every", type=int, default=0, help="rest after every N books (gentler on Goodreads)")
    ap.add_argument("--pause", type=float, default=600, help="seconds to rest for --pause-every")
    ap.add_argument("--backoff", type=float, default=120, help="seconds to wait once when Goodreads throttles")
    ap.add_argument("--retry-empty", action="store_true", help="look up again books whose Goodreads result was empty or whose lookup had an error")
    args = ap.parse_args()
    if not (args.status or args.ids or args.list):
        ap.error("give --status, --ids or --list")
    done = json.load(open(args.out, encoding="utf-8")) if os.path.exists(args.out) else {}
    if args.retry_empty:
        done = {k: v for k, v in done.items() if v.get("goodreads") and not v.get("errors")}
    todo = [b for b in books(args) if str(b["book_id"]) not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"Books to look up: {len(todo)} (already done: {len(done)})", flush=True)
    for i, b in enumerate(todo, 1):
        q = clean_title(b["title"])
        rec = {"title": b["title"], "publisher": b["publisher"], "query": q, "goodreads": [], "abjjad": [], "bookhome": [],
               "elain": [], "errors": []}
        for name, fn in (("goodreads", goodreads), ("abjjad", abjjad), ("bookhome", bookhome), ("elain", elain)):
            if getattr(args, "no_" + name) or (name == "elain" and "العين" not in (b["publisher"] or "")):
                continue
            for attempt in range(2):
                try:
                    rec[name] = fn(q)
                    break
                except RuntimeError as e:  # throttled: back off once, then stop the run (it is resumable)
                    if attempt == 0:
                        print(f"  {name} throttled, waiting {args.backoff:.0f} s", flush=True)
                        time.sleep(args.backoff)
                        continue
                    rec["errors"].append(f"{name}: {e}")
                    if name == "goodreads":
                        print("Goodreads keeps refusing; stopping. Re-run later with --retry-empty.", flush=True)
                        return
                except Exception as e:  # network/HTML errors are recorded, never fatal
                    rec["errors"].append(f"{name}: {e}")
                    break
            time.sleep(args.delay)
        done[str(b["book_id"])] = rec
        top = rec["goodreads"][0] if rec["goodreads"] else None
        ab = rec["abjjad"][0] if rec["abjjad"] else None
        bh = (rec["elain"] or rec["bookhome"] or [None])[0]
        print(f"[{i}/{len(todo)}] {b['book_id']} {q} -> " +
              (f"{top['title'][:50]} | {', '.join(a['name'] for a in top['authors'][:2])} ({top['ratings']})" if top else
               f"shop: {bh['title'][:50]} | {bh['publisher']} | {bh['author']}" if bh else
               f"abjjad: {ab['title'][:50]} | {ab['author']}" if ab else "no result"), flush=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(done, f, ensure_ascii=False, indent=1)
        if args.pause_every and i % args.pause_every == 0 and i < len(todo):
            print(f"  resting {args.pause:.0f} s", flush=True)
            time.sleep(args.pause)
    print("Done:", args.out)


if __name__ == "__main__":
    main()
