"""Fetch reader reviews from Goodreads for researched books (2026-10-01), slowly, for a researcher to read.

Only books whose research record already links a Goodreads *book page* are fetched (the page was matched
during research, so no search and no guessing). One request per book: the book page carries the work's
rating stats and its first 30 community reviews (all editions and languages; `lang` marks Arabic ones).
Goodreads throttles per computer: requests are spaced out, and a throttled page makes the run wait
(--rest seconds) and try again, up to --max-rests times, then stop. The run is resumable (existing files are
skipped). It never writes to the database.

Usage: backend/venv/bin/python scripts/goodreads_reviews.py --out research/reviews_gr [--ids 1,2] [--delay 60]
"""
import argparse, glob, html, json, os, re, sqlite3, sys, time, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lookup_sources as L  # noqa: E402
import goodreads_title_search as S  # noqa: E402  (same-title / same-person rules)

ROOT = L.ROOT
_AR = re.compile(r"[؀-ۿ]")


def _clean(s):
    s = re.sub(r"<br\s*/?>", "\n", s or "")
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


class NoBook(Exception):
    """The page loaded but is not one book page (not a throttle): the book is skipped, the run goes on."""


def _latin(s):
    return re.sub(r"[^a-z ]", "", (s or "").lower())


def find_gid(con, bid, use_isbn=True, search=True):
    """(page path, how) for a book: the Goodreads page linked by its research record; else its ISBN; else a Goodreads
    search that returns the same title by the same author (Arabic or Latin spelling). None when nothing safe is found.

    Our own title and author come first (Ibrahim, 2026-10-04): a page resolved by the title search is used before
    the ISBN, and an ISBN that failed before (404, or a page for another book) is not asked again."""
    g = gr_book_id(bid)
    if g:
        return f"show/{g}", "record"
    title, author, isbn = con.execute("SELECT title, author, isbn FROM books WHERE book_id=?", (bid,)).fetchone()
    isbn = re.sub(r"\D", "", isbn or "")
    hit = _search_found().get(str(bid))  # resolved by scripts/goodreads_title_search.py (same title, same author)
    if hit and (hit.get("ratings") or 0) >= MIN_RATINGS:
        return f"show/{hit['gid']}", "search"
    if use_isbn and len(isbn) in (10, 13) and str(bid) not in _isbn_failed():
        return f"isbn/{isbn}", "isbn"
    if hit:
        return None  # too few ratings on Goodreads to be worth a page request
    if not search or not author or author == "غير معروف":
        return None
    key = L.norm_ar(L.clean_title(title))
    lat = bool(re.search(r"[A-Za-z]", title))
    first = author.split("،")[0].split(" و")[0].strip()
    for c in L.goodreads(f"{L.clean_title(title)} {first}", n=5):
        t = c.get("title") or ""
        same_t = (_latin(t).startswith(_latin(title)) and len(_latin(title)) > 3) if lat else \
                 (L.norm_ar(re.sub(r"\(.*?\)", "", t)).strip() == key or L.norm_ar(t).startswith(key + " "))
        same_a = False
        for x in c.get("authors") or []:
            a = x.get("name") or ""
            if re.search(r"[A-Za-z]", first):
                same_a = same_a or bool(_latin(first).split()) and _latin(first).split()[-1:] == _latin(a).split()[-1:]
            else:
                na, nf = L.norm_ar(a), L.norm_ar(first)
                same_a = same_a or bool(na) and (nf in na or na in nf or nf.split()[-1:] == na.split()[-1:])
        if same_t and same_a:
            return f"show/{c['id']}", "search"
    return None


_FOUND = {}
MIN_RATINGS = 10


def _search_found():
    """Pages resolved by the title-search phase: research/reviews_gr/_search_found.json (re-read when it changes)."""
    p = os.path.join(ROOT, "research", "reviews_gr", "_search_found.json")
    try:
        m = os.path.getmtime(p)
    except OSError:
        return {}
    if _FOUND.get("m") != m:
        try:
            _FOUND.update(m=m, d=json.load(open(p, encoding="utf-8")))
        except ValueError:
            return _FOUND.get("d") or {}
    return _FOUND["d"]


_FAILED = {}


def _failed_path():
    return os.path.join(ROOT, "research", "reviews_gr", "_isbn_failed.json")


def _isbn_failed():
    """Books whose ISBN gave no usable Goodreads page: {book_id: {reason, ...}}. The title search reads this list too."""
    try:
        m = os.path.getmtime(_failed_path())
        if _FAILED.get("m") != m:
            _FAILED.update(m=m, d=json.load(open(_failed_path(), encoding="utf-8")))
        return _FAILED["d"]
    except (OSError, ValueError):
        return _FAILED.get("d") or {}


def _mark_isbn_failed(bid, **info):
    d = dict(_isbn_failed())
    d[str(bid)] = info
    tmp = _failed_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _failed_path())


def _tkey(t):
    t = L.norm_ar(html.unescape(t or "").replace("_", " "))
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def same_book(title, persons, gr_title, gr_author):
    """Is the page reached by an ISBN our book? Our title and author decide, not the number (Ibrahim, 2026-10-04).
    True  = the titles agree (spelling variants, a missing subtitle or series label, a part label all pass: the
            number already points at this edition, so the author's spelling is not asked for as well);
    False = both titles are Arabic and clearly different;
    None  = cannot tell: a transliterated or foreign title whose author is not recognisably the same person
            (the page is kept, marked unverified, and the reader decides)."""
    import difflib
    gr_title, gr_author = html.unescape(gr_title or ""), html.unescape(gr_author or "")
    if _AR.search(title or "") and _AR.search(gr_title):
        full_a, full_b = _tkey(title), _tkey(gr_title)
        cut = r"[:؛،,(\-–]|\.\.|\sج\s*\d|\s(?:ال)?(?:جزء|مجلد)\s"
        main_a = _tkey(re.split(cut, title)[0]) or full_a
        main_b = _tkey(re.split(cut, gr_title)[0]) or full_b
        for x, y in ((full_a, full_b), (main_a, main_b), (main_a, full_b), (full_a, main_b)):
            if not x or not y:
                continue
            if x == y or difflib.SequenceMatcher(None, x, y).ratio() >= 0.8:
                return True
            short, long_ = sorted((x, y), key=len)
            if len(short) >= 6 and f" {short} " in f" {long_} ":
                return True
        return False
    if _AR.search(gr_title) or (re.search(r"[A-Za-z]", title or "") and not _AR.search(title or "")):
        return True if S.same_title(title, gr_title) else False
    return True if gr_author and any(S.same_person(p, gr_author) for p in persons) else None


def gr_book_id(bid):
    """The Goodreads book page id linked by the latest research record of this book (None if only a search)."""
    recs = sorted((p for p in glob.glob(os.path.join(ROOT, "research", "*", f"{bid}.json")) if "review" not in p),
                  key=os.path.getmtime)
    for p in reversed(recs):
        m = re.findall(r"goodreads\.com/(?:ar/)?book/show/(\d+)", open(p, encoding="utf-8").read())
        if m:
            return m[-1]
    return None


def parse_page(page, gid):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S)
    if not m:
        raise RuntimeError("Goodreads page without data (throttled?)")
    st = json.loads(m.group(1))["props"]["pageProps"].get("apolloState") or {}
    book = next((v for k, v in st.items() if k.startswith("Book:") and str(v.get("legacyId")) == str(gid)), None)
    if not book:  # Goodreads redirects merged/old ids (and /book/isbn/ links) to the canonical book: take the page's own book
        full = [v for k, v in st.items() if k.startswith("Book:") and v.get("title") and v.get("primaryContributorEdge")]
        book = full[0] if len(full) == 1 else None
    if not book:
        raise NoBook("Goodreads page without a single book")
    ref = ((book.get("primaryContributorEdge") or {}).get("node") or {}).get("__ref")
    work = next((v for k, v in st.items() if k.startswith("Work:")), {})
    stats = work.get("stats") or {}
    reviews = []
    for k, v in st.items():
        if k.startswith("Review:"):
            text = _clean(v.get("text"))
            reviews.append({"rating": v.get("rating") or None, "text": text, "lang": "ar" if _AR.search(text) else "other",
                            "spoiler": bool(v.get("spoilerStatus")), "likes": v.get("likeCount"),
                            "date": time.strftime("%Y-%m-%d", time.gmtime((v.get("createdAt") or 0) / 1000))})
    # kept for the other tracks, so the page is asked for only once (Ibrahim, 2026-10-04): the description
    # (summaries), the original title and the edition's language (original-language track), plus edition details
    det, wdet = book.get("details") or {}, work.get("details") or {}
    desc = book.get('description({"stripped":true})') or _clean(book.get("description"))
    extra = {"description": desc or None, "original_title": wdet.get("originalTitle") or None,
             "edition_language": (det.get("language") or {}).get("name"), "pages": det.get("numPages"),
             "publisher": det.get("publisher"), "format": det.get("format"),
             "isbn13": det.get("isbn13"), "isbn10": det.get("isbn"),
             "published_ms": det.get("publicationTime"), "first_published_ms": wdet.get("publicationTime"),
             "genres": [((g.get("genre") or {}).get("name")) for g in book.get("bookGenres") or [] if (g.get("genre") or {}).get("name")],
             "contributors": [{"name": (st.get((e.get("node") or {}).get("__ref")) or {}).get("name"), "role": e.get("role")}
                              for e in [book.get("primaryContributorEdge") or {}] + list(book.get("secondaryContributorEdges") or [])
                              if (e.get("node") or {}).get("__ref")]}
    return {"gr_title": book.get("title"), "gr_author": (st.get(ref) or {}).get("name"), "page": extra,
            "avg": stats.get("averageRating"), "ratings_count": stats.get("ratingsCount"),
            "ratings_dist": stats.get("ratingsCountDist"), "text_reviews": stats.get("textReviewsCount"),
            "review_langs": {x["isoLanguageCode"]: x["count"] for x in stats.get("textReviewsLanguageCounts") or []},
            "reviews": [r for r in reviews if r["text"]]}


def P(*a, **k):
    """print with the local time first, so the logs show how many answers come per hour."""
    k.pop("flush", None)
    print(time.strftime("%H:%M:%S"), *a, flush=True, **k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True); ap.add_argument("--ids")
    ap.add_argument("--ids-file", help="book ids in fetch order (commas or new lines); used when --ids is not given")
    ap.add_argument("--limit", type=int, default=0, help="try at most this many books, then exit (for taking turns with the title search)")
    ap.add_argument("--skip-dir", default=os.path.join(ROOT, "research", "reviews"),
                    help="Abjjad review files: books with at least --skip-min written reviews there are skipped")
    ap.add_argument("--skip-min", type=int, default=5)
    ap.add_argument("--delay", type=float, default=60); ap.add_argument("--rest", type=float, default=1800)
    ap.add_argument("--max-rests", type=int, default=8, help="stop after this many rests in total (0 = no total limit)")
    ap.add_argument("--max-idle-rests", type=int, default=0,
                    help="stop after this many rests in a row with no page read in between (0 = off)")
    ap.add_argument("--min-ratings", type=int, default=10,
                    help="a page resolved by the title search is not fetched when Goodreads shows fewer ratings (no chance of 2 agreeing readers)")
    ap.add_argument("--no-search", action="store_true", help="only books with a linked page or an ISBN (the search throttles fast)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, "data", "library.db"), uri=True)
    if args.ids or args.ids_file:
        ids = [int(x) for x in (args.ids or open(args.ids_file).read()).replace("\n", ",").split(",") if x.strip()]
    else:
        ids = [r[0] for r in con.execute("SELECT book_id FROM books WHERE research_status='matched' AND active=1 "
                                         "ORDER BY quantity DESC, book_id")]
    covered = set()
    for f in glob.glob(os.path.join(args.skip_dir, "[0-9]*.json")):
        r = json.load(open(f, encoding="utf-8"))
        if sum(1 for x in r.get("reviews", []) if (x.get("text") or "").strip()) >= args.skip_min:
            covered.add(r["book_id"])
    os.makedirs(os.path.join(args.out, "_none"), exist_ok=True)
    os.makedirs(os.path.join(args.out, "_isbn_mismatch"), exist_ok=True)
    rests = idle = 0
    global MIN_RATINGS
    MIN_RATINGS = args.min_ratings
    tried = set()  # (book, page) asked in this run; a later pass asks again only when the title search brought a page

    def pending():
        out = []
        for b in ids:
            if b in covered or os.path.exists(os.path.join(args.out, f"{b}.json")) \
                    or os.path.exists(os.path.join(args.out, "_none", f"{b}")):
                continue
            hit = _search_found().get(str(b))
            key = (b, hit["gid"] if hit else None)
            if key in tried or (str(b) in _isbn_failed() and not hit and args.no_search):
                continue
            out.append(b)
        return out

    waits = 0
    while True:
        todo = pending()
        P(f"{len(todo)} books to try" + (f" (this turn: {args.limit})" if args.limit else ""), flush=True)
        if args.limit:  # one turn, then the caller decides what runs next
            if not todo:
                return 4
            return 3 if _run(args, con, todo[:args.limit], tried, [rests, idle]) else 0
        if not todo:  # nothing left for now: the title search may still bring pages, so look again a few times
            waits += 1
            if waits > max(1, args.max_idle_rests):
                return 4
            time.sleep(args.rest)
            continue
        waits = 0
        if _run(args, con, todo, tried, [rests, idle]):
            return 3


def _run(args, con, todo, tried, st):
    """One pass over todo. True = stop the whole run (too many rests)."""
    rests, idle = st
    for bid in todo:
        hit = _search_found().get(str(bid))
        tried.add((bid, hit["gid"] if hit else None))
        data, use_isbn, found = None, True, None
        while True:
            try:
                found = find_gid(con, bid, use_isbn, not args.no_search)
                if not found:
                    break
                if found[1] == "search" and not _search_found().get(str(bid)):
                    time.sleep(args.delay)
                gid = found[0].split("/")[1]
                data = parse_page(L.get(f"https://www.goodreads.com/book/{found[0]}"), gid)
                idle = 0
                if found[1] == "isbn":
                    t, a = con.execute("SELECT title, author FROM books WHERE book_id=?", (bid,)).fetchone()
                    same = same_book(t, S.persons_of(con, bid, a), data.get("gr_title"), data.get("gr_author"))
                    if same is None:
                        data["isbn_check"] = "unverified"  # the reader decides before using the page
                    if same is False:
                        # the number leads to another book: our record is not touched, the page is kept aside
                        rec = {"book_id": bid, "title": t, "author": a, "goodreads": f"https://www.goodreads.com/book/{found[0]}",
                               "found_by": "isbn", **data}
                        with open(os.path.join(args.out, "_isbn_mismatch", f"{bid}.json"), "w", encoding="utf-8") as f:
                            json.dump(rec, f, ensure_ascii=False, indent=1)
                        _mark_isbn_failed(bid, reason="other_book", gr_title=data.get("gr_title"), gr_author=data.get("gr_author"))
                        P(f"{bid} {t}: ISBN page is another book ({data.get('gr_title')} / {data.get('gr_author')}); left to the title search", flush=True)
                        data, use_isbn = None, False
                        time.sleep(args.delay)
                        continue
                break
            except NoBook as e:
                P(f"{bid}: {e}; skipped", flush=True)
                break
            except Exception as e:
                if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                    if found and found[1] == "isbn":  # Goodreads has no edition with this ISBN: try the title search
                        _mark_isbn_failed(bid, reason="404")
                        use_isbn = False
                        time.sleep(args.delay)
                        continue
                    P(f"{bid}: 404; skipped", flush=True)
                    break
                rests += 1
                idle += 1
                if (args.max_rests and rests > args.max_rests) or (args.max_idle_rests and idle > args.max_idle_rests):
                    P(f"stopping after {rests} rests ({idle - 1} in a row without a page):", e, flush=True)
                    return True
                P(f"{bid}: {e}; resting {args.rest:.0f} s ({rests}/{args.max_rests or '-'}, {idle} in a row)", flush=True)
                time.sleep(args.rest)
        title, author = con.execute("SELECT title, author FROM books WHERE book_id=?", (bid,)).fetchone()
        if not data and args.no_search:
            continue
        if not data:
            open(os.path.join(args.out, "_none", f"{bid}"), "w").close()  # no safe Goodreads page; not retried
            P(bid, title, "-> no Goodreads page", flush=True)
            time.sleep(args.delay)
            continue
        rec = {"book_id": bid, "title": title, "author": author, "goodreads": f"https://www.goodreads.com/book/{found[0]}",
               "found_by": found[1], **data}
        with open(os.path.join(args.out, f"{bid}.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        ar = sum(1 for r in data["reviews"] if r["lang"] == "ar")
        st[0], st[1] = rests, idle
        P(bid, title, "->", f"{len(data['reviews'])} written reviews ({ar} Arabic), avg {data['avg']} [{found[1]}] {data.get('gr_title')} / {data.get('gr_author')}", flush=True)
        time.sleep(args.delay)
    st[0], st[1] = rests, idle
    return False


if __name__ == "__main__":
    sys.exit(main() or 0)  # 3 = stopped because Goodreads kept refusing, 4 = nothing left to try
