"""Fetch the publisher's / back-cover description text of researched books from pages already saved by the research
(2026-10-03), for a researcher to read and write our own 2–3 sentence summary. Run on the Mac (needs internet).

Input: research/summaries/_targets.json  {book_id: {title, author, publisher, sources: [{kind, url, host}]}}
  kind: publisher | abjjad | abjjad_unchecked | goodreads | store
Output: <out>/<book_id>.json  {book_id, title, author, publisher, fetched: [{kind, url, page_title, page_publisher,
  desc, how, error}]}.  The description text is a WORKING FILE only: it is never imported or shown; summaries are
written in our own words. It never writes to the database. Resumable: a book whose file exists is skipped
(--retry-errors refetches sources that failed). Polite: --delay seconds between requests to the same host.
Goodreads is skipped unless --goodreads is given (it throttles per computer and the review fetcher may be running).

Usage: backend/venv/bin/python scripts/fetch_descriptions.py --out research/summaries/desc [--ids 1,2] [--kinds publisher,abjjad,store]
       [--delay 3] [--limit N] [--probe URL]
"""
import argparse, html, json, os, re, sys, time, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lookup_sources as L  # noqa: E402

ROOT = L.ROOT


def _clean(s):
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\s*\n\s*", "\n", s).strip()


def _walk(x, out, pub):
    if isinstance(x, dict):
        d = x.get("description")
        if isinstance(d, str) and len(d) > 40:
            out.append(("jsonld", _clean(d)))
        for k in ("publisher", "brand"):
            p = x.get(k)
            if isinstance(p, dict):
                p = p.get("name")
            if isinstance(p, str) and p.strip():
                pub.append(p.strip())
        for v in x.values():
            _walk(v, out, pub)
    elif isinstance(x, list):
        for v in x:
            _walk(v, out, pub)


BLOCKS = [  # (name, regex capturing an HTML block that holds the description)
    ("woo_tab", r'(?is)id="tab-description"[^>]*>(.*?)</div>\s*(?:<div[^>]+id="tab-|</div>\s*</div>)'),
    ("woo_short", r'(?is)class="woocommerce-product-details__short-description"[^>]*>(.*?)</div>'),
    ("abjjad", r'(?is)<[^>]+(?:itemprop="description"|class="[^"]*(?:book-?desc|description|brief|synopsis)[^"]*")[^>]*>(.*?)</(?:div|p|section|span)>'),
    ("nwf", r'(?is)(نبذة (?:الناشر|نيل وفرات|المؤلف)\s*:.*?)</(?:div|td|p)>'),
]


def extract(page, host):
    cands, pub = [], []
    for m in re.finditer(r'(?is)<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', page):
        try:
            _walk(json.loads(m.group(1).strip()), cands, pub)
        except Exception:
            pass
    if "goodreads.com" in host:
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S)
        if not m:
            raise RuntimeError("Goodreads page without data (throttled?)")
        st = json.loads(m.group(1))["props"]["pageProps"].get("apolloState") or {}
        for k, v in st.items():
            if k.startswith("Book:") and isinstance(v, dict):
                d = v.get('description({"stripped":true})') or v.get("description")
                if isinstance(d, str) and len(d) > 40:
                    cands.append(("gr_book", _clean(d)))
                p = (v.get("details") or {}).get("publisher")
                if p:
                    pub.append(p)
    for name, attr in (("og", r'property="og:description"'), ("meta", r'name="description"')):
        m = re.search(r'(?is)<meta[^>]+' + attr + r'[^>]+content="([^"]*)"', page) or \
            re.search(r'(?is)<meta[^>]+content="([^"]*)"[^>]+' + attr, page)
        if m and len(m.group(1)) > 40:
            cands.append((name, _clean(m.group(1))))
    for name, rx in BLOCKS:
        for m in re.finditer(rx, page):
            t = _clean(m.group(1))
            if len(t) > 60:
                cands.append((name, t))
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    title = _clean(m.group(1))[:200] if m else None
    m = re.search(r"(?:الناشر|دار النشر)\s*:?\s*</[^>]+>\s*(?:<[^>]+>\s*)*([^<]{2,80})<", page)
    if m:
        pub.append(_clean(m.group(1)))
    cands = [(n, re.split(r'",\s*"offers"', t)[0].strip()) for n, t in cands]
    cands = [(n, t) for n, t in cands if not re.search(r"خصم فوري|يصلك في أقل من|نافذتك لاقتناء", t)]
    best = max(cands, key=lambda c: len(c[1])) if cands else (None, None)
    return {"page_title": title, "page_publisher": pub[0] if pub else None, "desc": best[1], "how": best[0],
            "all": [(n, len(t)) for n, t in cands]}


def fetch(url):
    host = urllib.parse.urlparse(url).netloc
    # non-ASCII paths (some saved URLs are not percent-encoded)
    parts = urllib.parse.urlsplit(url)
    url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, urllib.parse.quote(parts.path, safe="/%"),
                                   urllib.parse.quote(parts.query, safe="=&%+"), ""))
    page = L.get(url)
    if "aseeralkotb" in host:  # the site answers a short page that sets a cookie in JavaScript first
        for _ in range(3):
            m = re.search(r"'(AserElKotb=[0-9a-f]+);", page)
            if not (m and len(page) < 5000):
                break
            req = L.urllib.request.Request(url, headers={"User-Agent": L.UA, "Cookie": m.group(1)})
            with L._OPENER.open(req, timeout=25) as r:
                page = r.read().decode("utf-8", "replace")
    return extract(page, host)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out"); ap.add_argument("--ids"); ap.add_argument("--limit", type=int)
    ap.add_argument("--kinds", default="publisher,abjjad,store"); ap.add_argument("--delay", type=float, default=3.0)
    ap.add_argument("--goodreads", action="store_true"); ap.add_argument("--retry-errors", action="store_true")
    ap.add_argument("--max-per-book", type=int, default=3); ap.add_argument("--probe")
    a = ap.parse_args()
    if a.probe:
        r = fetch(a.probe)
        print(json.dumps({k: (v[:700] if isinstance(v, str) else v) for k, v in r.items()}, ensure_ascii=False, indent=1))
        return
    kinds = [k.strip() for k in a.kinds.split(",") if k.strip()] + (["goodreads"] if a.goodreads else [])
    targets = json.load(open(os.path.join(ROOT, "research", "summaries", "_targets.json"), encoding="utf-8"))
    ids = [i.strip() for i in a.ids.split(",")] if a.ids else sorted(targets, key=int)
    os.makedirs(a.out, exist_ok=True)
    last, done, errs_run = {}, 0, 0
    for bid in ids:
        t = targets.get(bid)
        if not t:
            continue
        path = os.path.join(a.out, f"{bid}.json")
        old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
        if old and not a.retry_errors:
            continue
        have = {f["url"]: f for f in (old or {}).get("fetched", []) if not f.get("error")}
        srcs = sorted((s for s in t["sources"] if s["kind"] in kinds), key=lambda s: kinds.index(s["kind"]))[:a.max_per_book]
        fetched = []
        for s in srcs:
            if s["url"] in have:
                fetched.append(have[s["url"]]); continue
            wait = a.delay - (time.time() - last.get(s["host"], 0))
            if wait > 0:
                time.sleep(wait)
            rec = {"kind": s["kind"], "url": s["url"]}
            try:
                rec.update(fetch(s["url"])); rec.pop("all", None); errs_run = 0
            except Exception as e:  # noqa: BLE001
                rec["error"] = f"{type(e).__name__}: {e}"[:200]; errs_run += 1
            last[s["host"]] = time.time()
            fetched.append(rec)
        json.dump({"book_id": int(bid), "title": t["title"], "author": t["author"], "publisher": t["publisher"],
                   "fetched": fetched}, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        done += 1
        print(bid, [(f["kind"], len(f.get("desc") or ""), f.get("error", "")) for f in fetched], flush=True)
        if errs_run >= 15:
            print("15 errors in a row: stopping (network or throttling?)", flush=True); break
        if a.limit and done >= a.limit:
            break
    print("done", done, flush=True)


if __name__ == "__main__":
    main()
