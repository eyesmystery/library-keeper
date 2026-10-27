"""Books we do not have, looked up while the visitor waits and kept for next time (Ibrahim 2026-10-11:
«ابغاه يدور وقتها واللي يحصله يخزن معلوماته للمستقبل … ياخذ البيانات الاساسية واذا قدر ياخذ المراجعات»).

When a visitor names a book that is not in our catalogue («أبغى كتاب يشبه اخلع حذائك»), the model's
own memory is not enough: it does not know many Arabic books and sometimes invents an author. Here:

1. `outside_books` is read first: a book looked up before answers at once (and `times_asked` grows).
2. Otherwise, within a few seconds: Wikidata (search + the work's author, genres, subjects, year,
   ISBN, Goodreads id, Arabic Wikipedia page) and Open Library (title search, author, year, ISBNs,
   subjects), each with the Arabic spellings of the title (حذائك / حذاءك …). A result counts only when
   its title is the visitor's title. The short summary comes from the Arabic (else English) Wikipedia.
3. After the answer, in the background: the Goodreads page of that edition (by its Goodreads id or
   ISBN — never Goodreads' search, which its robots.txt forbids): description, genres, rating, number
   of ratings (reader reviews are not kept). Stored for the next time.
A title not found anywhere is remembered too (`status = not_found`) and tried again after a week.

Nothing here is ever shown as our stock. The chat uses it only to describe the visitor's book honestly
(«حسب ويكيبيديا / Goodreads») and to find similar books we have (same author, close in meaning).
Off when LIBRARY_KEEPER_OUTSIDE=0 (the test suites) or the setting `outside_lookup` is false.
"""
import html
import http.cookiejar
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone

import net
from models import get_connection

UA_API = "Manar-library-assistant/1.0 (bookshop helper)"
UA_WEB = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
BUDGET = 6.0                  # seconds the visitor may wait for a first lookup
ENRICH_WAIT = 4.0             # at least this long for Goodreads when the record has nothing about the book yet
RETRY_NOT_FOUND = 7 * 86400   # a title found nowhere is tried again after a week
_down = {}                    # source -> time until which it is skipped after a failure
_busy = set()                 # keys being enriched in the background
_lock = threading.Lock()


def enabled():
    if os.environ.get("LIBRARY_KEEPER_OUTSIDE") == "0":
        return False
    try:
        import llm
        return llm.read_settings().get("outside_lookup", True) is not False
    except Exception:  # noqa: BLE001
        return True


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS outside_books (
        key TEXT PRIMARY KEY, asked_title TEXT NOT NULL, status TEXT NOT NULL,
        title TEXT, author TEXT, year INTEGER, publisher TEXT, language TEXT, pages INTEGER,
        genres TEXT NOT NULL DEFAULT '[]', subjects TEXT NOT NULL DEFAULT '[]', description TEXT, blurb TEXT,
        rating REAL, ratings_count INTEGER, reviews TEXT NOT NULL DEFAULT '[]', isbn TEXT,
        wikidata TEXT, goodreads_id TEXT, sources TEXT NOT NULL DEFAULT '[]',
        times_asked INTEGER NOT NULL DEFAULT 0, looked_at TEXT, enriched_at TEXT)""")
    cols = {c[1] for c in conn.execute("PRAGMA table_info(outside_books)")}
    if "blurb" not in cols:   # the publisher's / Goodreads' blurb: what the book is about (Wikipedia tells its history)
        conn.execute("ALTER TABLE outside_books ADD COLUMN blurb TEXT")


# ---------------------------------------------------------------------------- names
def norm(text):
    t = str(text or "")
    t = re.sub(r"[ً-ْٰـ]", "", t)
    t = t.translate(str.maketrans("أإآٱىةؤئ", "اااايهءء"))
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    t = re.sub(r"[^\w\s]", " ", t.lower())
    t = re.sub(r"^(ال)?(كتاب|روايه|رواية|مجموعه قصصيه)\s+", "", t.strip())
    return " ".join(t.split())


def key_of(title):
    return norm(title)


def spellings(title):
    """The ways the visitor's title may be written in a catalogue: «اخلع حذائك» / «اخلع حذاءك» / «إخلع …»."""
    t = " ".join(str(title or "").split())
    out = [t]
    for a, b in (("ئ", "ء"), ("ء", "ئ"), ("ة", "ه"), ("ه ", "ة "), ("ى", "ي"), ("ي ", "ى ")):
        if a in t + " ":
            v = (t + " ").replace(a, b).strip()
            if v not in out:
                out.append(v)
    if t[:1] == "ا":
        out.append("إ" + t[1:])
    return out[:5]


def same_title(asked, found):
    a, f = norm(asked), norm(found)
    if not a or not f:
        return False
    return a == f or (f.startswith(a + " ") and len(a) >= 6) or (a.startswith(f + " ") and len(f) >= 6)


# ---------------------------------------------------------------------------- fetching
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                      urllib.request.HTTPSHandler(context=net.ssl_context()))


def _get(url, timeout, web=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA_WEB if web else UA_API, "Accept-Language": "ar,en;q=0.8"})
    with _opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _json(url, timeout):
    return json.loads(_get(url, timeout))


def _up(source):
    return time.time() >= _down.get(source, 0)


def _fail(source, seconds=600):
    _down[source] = time.time() + seconds


def _wd_labels(ids, timeout):
    if not ids:
        return {}
    d = _json("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props=labels&languages=ar|en&ids="
              + "|".join(ids[:40]), timeout)
    out = {}
    for q, e in (d.get("entities") or {}).items():
        lab = (e.get("labels") or {})
        out[q] = (lab.get("ar") or lab.get("en") or {}).get("value")
    return out


_WORK = re.compile(r"رواية|روايه|كتاب|قصص|قصصية|ديوان|مسرحية|سيرة|novel|book|short stor|poetry|memoir|essay|written work|literary work", re.I)
_NOT_WORK = re.compile(r"مسلسل|فيلم|صفحة توضيح|film|television|series|disambiguation|painting|album|song|episode", re.I)


def from_wikidata(title, timeout=4.0):
    """-> dict or None. The work whose Arabic/English label is the visitor's title."""
    for lang in (("ar", "en") if re.search(r"[؀-ۿ]", title) else ("en", "ar")):
        for t in spellings(title):
            d = _json("https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&type=item&limit=7"
                      f"&language={lang}&uselang={lang}&search=" + urllib.parse.quote(t), timeout)
            for e in d.get("search") or []:
                desc = e.get("description") or ""
                if not same_title(title, e.get("label") or "") or _NOT_WORK.search(desc) or not _WORK.search(desc):
                    continue
                return _wd_work(e["id"], e.get("label"), desc, timeout)
    return None


def _wd_work(qid, label, desc, timeout):
    d = _json("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props=claims|sitelinks&ids=" + qid, timeout)
    e = (d.get("entities") or {}).get(qid) or {}
    claims = e.get("claims") or {}

    def vals(p):
        out = []
        for c in claims.get(p) or []:
            v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            out.append(v.get("id") if isinstance(v, dict) and "id" in v else v)
        return [v for v in out if v]
    authors, genres, subjects = vals("P50"), vals("P136"), vals("P921")
    labels = _wd_labels(authors + genres + subjects, timeout)
    year = None
    for v in vals("P577"):
        m = re.match(r"[+-]?(\d{4})", (v or {}).get("time", "") if isinstance(v, dict) else "")
        if m:
            year = int(m.group(1))
            break
    isbn = (vals("P212") or vals("P957") or [None])[0]
    sl = e.get("sitelinks") or {}
    page = (sl.get("arwiki") or {}).get("title")
    page_en = (sl.get("enwiki") or {}).get("title")
    return {"title": label, "author": "، ".join(x for x in (labels.get(a) for a in authors) if x) or None,
            "year": year, "genres": [labels[g] for g in genres if labels.get(g)],
            "subjects": [labels[s] for s in subjects if labels.get(s)], "isbn": isbn,
            "goodreads_id": (vals("P2969") or [None])[0], "wikidata": qid, "kind": desc,
            "wiki": ("ar", page) if page else (("en", page_en) if page_en else None),
            "sources": ["https://www.wikidata.org/wiki/" + qid]}


def wiki_summary(lang, page, timeout=4.0):
    d = _json(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(page.replace(" ", "_")), timeout)
    text = (d.get("extract") or "").strip()
    return (text[:900], f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(page.replace(" ", "_"))) if text else (None, None)


def from_openlibrary(title, timeout=4.0):
    for t in spellings(title):
        d = _json("https://openlibrary.org/search.json?limit=5&fields=key,title,author_name,first_publish_year,isbn,subject,language,publisher"
                  "&title=" + urllib.parse.quote(t), timeout)
        for doc in d.get("docs") or []:
            if not same_title(title, doc.get("title") or ""):
                continue
            isbns = doc.get("isbn") or []
            return {"title": doc.get("title"), "author": "، ".join((doc.get("author_name") or [])[:2]) or None,
                    "year": doc.get("first_publish_year"), "isbn": next((i for i in isbns if len(i) == 13), isbns[0] if isbns else None),
                    "subjects": [s for s in (doc.get("subject") or []) if len(s) < 40][:8],
                    "publisher": ((doc.get("publisher") or [None])[0]),
                    "sources": ["https://openlibrary.org" + doc.get("key", "")]}
    return None


def goodreads_page(goodreads_id=None, isbn=None, title=None, timeout=12.0):
    """The Goodreads page of one edition: by its id, its ISBN, or Goodreads' own «book by title» address
    (/book/title, which robots.txt allows; never /search, which it forbids) -> dict or None.
    The caller checks that the page is the same book."""
    if goodreads_id:
        url = "https://www.goodreads.com/book/show/" + str(goodreads_id)
    elif isbn:
        url = "https://www.goodreads.com/book/isbn/" + str(isbn)
    elif title:
        url = "https://www.goodreads.com/book/title?id=" + urllib.parse.quote(title)
    else:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": UA_WEB, "Accept-Language": "ar,en;q=0.8"})
    with _opener.open(req, timeout=timeout) as r:
        page, final, status = r.read().decode("utf-8", "replace"), r.geturl(), r.status
    if status == 202 or not page.strip():
        # Goodreads' bot check answers 202 with an empty page when this computer asked too much:
        # not «no such book» — the caller rests and tries again on a later visit
        raise RuntimeError("goodreads challenge")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S)
    if not m:
        return None
    st = json.loads(m.group(1))["props"]["pageProps"].get("apolloState") or {}
    out = {"sources": []}
    for k, v in st.items():
        if k.startswith("Book:") and v.get("title") and "title" not in out:
            out["title"] = v.get("title")
            out["description"] = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", v.get("description") or ""))).strip()[:1200] or None
            out["genres"] = [n for n in (g.get("genre", {}).get("name") for g in v.get("bookGenres") or [] if g.get("genre"))
                             if n and n not in ("Ebooks", "Audiobook", "Kindle", "Books About Books")][:8]
            det = v.get("details") or {}
            out["pages"] = det.get("numPages")
            out["publisher"] = det.get("publisher")
            out["sources"].append(v.get("webUrl") or url)
            gid = re.search(r"/show/(\d+)", v.get("webUrl") or "")
            out["goodreads_id"] = gid.group(1) if gid else goodreads_id
        elif k.startswith("Work:"):
            s = v.get("stats") or {}
            out["rating"] = s.get("averageRating")
            out["ratings_count"] = s.get("ratingsCount")
        elif k.startswith("Contributor:") and "author" not in out:
            out["author"] = v.get("name")
        # reader reviews are not kept: copied review text is never stored (AGENTS.md; daily audit 2026-10-12 #6).
        # The work's rating and number of ratings above say how readers took it.
    return out if out.get("title") else None


# ---------------------------------------------------------------------------- storage
def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(conn, key):
    r = conn.execute("SELECT * FROM outside_books WHERE key=?", (key,)).fetchone()
    if not r:
        return None
    cols = [c[0] for c in conn.execute("SELECT * FROM outside_books LIMIT 0").description]
    d = dict(zip(cols, r))
    for f in ("genres", "subjects", "reviews", "sources"):
        try:
            d[f] = json.loads(d[f] or "[]")
        except ValueError:
            d[f] = []
    return d


def get(title):
    conn = get_connection()
    try:
        ensure_table(conn)
        return _row(conn, key_of(title))
    finally:
        conn.close()


def _save(key, asked, info, status):
    conn = get_connection()
    try:
        ensure_table(conn)
        old = _row(conn, key) or {}
        merged = dict(old)
        for f, v in (info or {}).items():
            if f in ("sources",):
                merged[f] = list(dict.fromkeys((old.get(f) or []) + list(v or [])))
            elif f in ("genres", "subjects"):
                if v:
                    merged[f] = v
            elif v not in (None, "", []) and f in ("title", "author", "year", "publisher", "language", "pages", "description",
                                                   "blurb", "rating", "ratings_count", "isbn", "wikidata", "goodreads_id"):
                if f == "description" and old.get("description") and info.get("_keep_description"):
                    continue
                merged[f] = v
        conn.execute("""INSERT INTO outside_books (key, asked_title, status, title, author, year, publisher, language, pages,
                        genres, subjects, description, rating, ratings_count, reviews, isbn, wikidata, goodreads_id, sources,
                        times_asked, looked_at, enriched_at, blurb)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(key) DO UPDATE SET status=excluded.status, title=excluded.title, author=excluded.author,
                        year=excluded.year, publisher=excluded.publisher, language=excluded.language, pages=excluded.pages,
                        genres=excluded.genres, subjects=excluded.subjects, description=excluded.description, rating=excluded.rating,
                        ratings_count=excluded.ratings_count, reviews=excluded.reviews, isbn=excluded.isbn, wikidata=excluded.wikidata,
                        goodreads_id=excluded.goodreads_id, sources=excluded.sources, looked_at=excluded.looked_at,
                        enriched_at=excluded.enriched_at, blurb=excluded.blurb""",
                     (key, old.get("asked_title") or asked, status if status != "keep" else old.get("status", "found"),
                      merged.get("title"), merged.get("author"), merged.get("year"), merged.get("publisher"), merged.get("language"),
                      merged.get("pages"), json.dumps(merged.get("genres") or [], ensure_ascii=False),
                      json.dumps(merged.get("subjects") or [], ensure_ascii=False), merged.get("description"), merged.get("rating"),
                      # reader-review text is never stored, whatever a source hands over; a row that held some
                      # from before 2026-10-12 is emptied the next time it is written (AGENTS.md; audit 2026-10-12 #6)
                      merged.get("ratings_count"), "[]", merged.get("isbn"),
                      merged.get("wikidata"), merged.get("goodreads_id"), json.dumps(merged.get("sources") or [], ensure_ascii=False),
                      old.get("times_asked") or 0, merged.get("looked_at") if status == "keep" else _now(),
                      _now() if info and info.get("_enriched") else old.get("enriched_at"), merged.get("blurb")))
        conn.commit()
    finally:
        conn.close()


def _count(key):
    conn = get_connection()
    try:
        conn.execute("UPDATE outside_books SET times_asked = times_asked + 1 WHERE key=?", (key,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------- lookup
def lookup(title, budget=BUDGET, background=True, goodreads_too=False):
    """-> the stored / found record (dict with status 'found') or None. Never raises.
    goodreads_too (a visitor suggesting a book for the shelves, VREVFIX 2026-10-27): Goodreads' «book by title» page is
    asked at the same time as Wikidata and Open Library, not only after both — Arabic books are mostly found only there
    («الأرض بعد طوفان نوح»: Wikidata and Open Library know nothing, Open Library took 6 s, so Goodreads never had its
    turn) — and a title remembered as not found is tried again (the visitor is asking now)."""
    try:
        return _lookup(title, budget, background, goodreads_too)
    except Exception:  # noqa: BLE001 - the chat goes on without it
        return None


def _gr_by_title(title, timeout):
    """Goodreads' page for this title, when it is the same title; None when it is another book or none."""
    gr = goodreads_page(title=title, timeout=timeout)
    return gr if gr and same_title(title, gr.get("title") or "") else None


def _lookup(title, budget, background, goodreads_too=False):
    key = key_of(title)
    if len(key) < 3 or not enabled():
        return None
    row = get(title)
    if row:
        _count(key)
        if row["status"] == "found":
            if background and not row.get("enriched_at"):
                row = _enrich_waiting(key, title, row, 0)
            return dict(row, times_asked=(row.get("times_asked") or 0) + 1)
        looked = datetime.strptime(row["looked_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        if time.time() - looked < RETRY_NOT_FOUND and not goodreads_too:
            return None
    t0 = time.time()
    found = {}
    pool = ThreadPoolExecutor(max_workers=3)
    jobs = {}
    if goodreads_too and _up("goodreads"):
        jobs[pool.submit(_gr_by_title, title, budget)] = "goodreads"
    if _up("wikidata"):
        jobs[pool.submit(from_wikidata, title, min(4.0, budget))] = "wikidata"
    if _up("openlibrary"):
        jobs[pool.submit(from_openlibrary, title, min(4.0, budget))] = "openlibrary"
    done, _ = wait(jobs, timeout=budget)
    # the visitor's answer does not wait for a source still running past the budget (audit 2026-10-12 #5)
    pool.shutdown(wait=False, cancel_futures=True)
    for f in jobs:
        src = jobs[f]
        if f not in done:
            continue
        try:
            found[src] = f.result()
        except Exception:  # noqa: BLE001
            _fail(src, 900 if src == "goodreads" else 600)
    wd, ol = found.get("wikidata"), found.get("openlibrary")
    gr_answered = "goodreads" in found     # Goodreads was asked and answered (a page, or no such title)
    if not wd and not ol and found.get("goodreads"):
        gr = dict(found["goodreads"])
        gr.update(_enriched=True, blurb=gr.get("description"))
        _save(key, title, gr, "found")
        _count(key)
        return get(title)
    left = budget - (time.time() - t0)
    if not wd and not ol and not goodreads_too and _up("goodreads") and left > 1.5:
        # neither knows it (or Wikidata is busy: it answers 429 when asked too much): Goodreads' own
        # «book by title» page, kept only when it is the same title
        try:
            gr = goodreads_page(title=title, timeout=left)
            gr_answered = True
            if gr and same_title(title, gr.get("title") or ""):
                gr.update(_enriched=True, blurb=gr.get("description"))
                _save(key, title, gr, "found")
                _count(key)
                return get(title)
        except Exception:  # noqa: BLE001
            _fail("goodreads", 900)
            return None
    if not wd and not ol:
        # every source answered, Goodreads too: really not found. A Goodreads not asked (no time left, or resting after a
        # failure) leaves nothing remembered, so the next visitor's lookup tries again (VREVFIX 2026-10-27)
        if jobs and all(f in done for f in jobs) and gr_answered:
            _save(key, title, {"title": None}, "not_found")
            _count(key)
        return None
    info = dict(ol or {})
    for k, v in (wd or {}).items():
        if v not in (None, [], ""):
            info[k] = v if k != "sources" else list(dict.fromkeys((info.get("sources") or []) + v))
    if not info.get("isbn") and ol:
        info["isbn"] = ol.get("isbn")
    # AGY-20261027-02: Goodreads asked together with the others (goodreads_too) is not dropped when Wikidata or Open Library
    # also knows the title: its blurb (what the book is about) and whatever they left empty are kept with its source
    gr = found.get("goodreads")
    if gr:
        for k in ("author", "publisher", "pages", "year", "genres", "rating", "ratings_count", "goodreads_id", "isbn"):
            if not info.get(k) and gr.get(k):
                info[k] = gr[k]
        if gr.get("description"):
            info["blurb"] = gr["description"]
            info["_enriched"] = True
        info["sources"] = list(dict.fromkeys((info.get("sources") or []) + list(gr.get("sources") or [])))
    wiki = info.pop("wiki", None)
    info.pop("kind", None)
    left = budget - (time.time() - t0)
    if wiki and left > 1.0 and _up("wikipedia"):
        try:
            text, url = wiki_summary(wiki[0], wiki[1], timeout=min(3.0, left))
            if text:
                info["description"] = text
                info["sources"] = (info.get("sources") or []) + [url]
        except Exception:  # noqa: BLE001
            _fail("wikipedia", 300)
    _save(key, title, info, "found")
    _count(key)
    row = get(title)
    if background:
        row = _enrich_waiting(key, title, row, budget - (time.time() - t0))
    return row


def _enrich_waiting(key, title, row, left):
    """Goodreads in the background; while the record has no blurb yet (Open Library gives only a title, author
    and year; Wikipedia mostly the book's history), the visitor's answer waits for it a little."""
    th = enrich_later(key)
    if th and not row.get("blurb"):
        th.join(max(ENRICH_WAIT, left))
        return get(title) or row
    return row


def enrich_later(key):
    """Goodreads description / genres / rating (no review text), after the answer (one at a time). -> the thread or None."""
    with _lock:
        if key in _busy or not _up("goodreads"):
            return None
        _busy.add(key)

    def work():
        try:
            enrich(key)
        finally:
            with _lock:
                _busy.discard(key)
    th = threading.Thread(target=work, daemon=True)
    th.start()
    return th


def enrich(key):
    conn = get_connection()
    try:
        ensure_table(conn)
        row = _row(conn, key)
    finally:
        conn.close()
    if not row or row["status"] != "found":
        return False
    by_title = not (row.get("goodreads_id") or row.get("isbn"))
    try:
        gr = goodreads_page(row.get("goodreads_id"), row.get("isbn"), row.get("title") or row["asked_title"])
    except Exception:  # noqa: BLE001
        _fail("goodreads", 900)
        return False
    same = gr and (same_title(row.get("title") or row["asked_title"], gr.get("title") or "")
                   or same_title(row["asked_title"], gr.get("title") or ""))
    if same and by_title and row.get("author") and gr.get("author"):
        # found by title alone: the author must be the same person too. Between an Arabic and a Latin spelling
        # the page author must be one of the Latin forms we hold for that person (a Latin name alone proved
        # nothing: «الأرض» of الشرقاوي took Steinbeck's blurb, audit 2026-10-12 #1). Between two spellings in
        # the same script the whole name must agree, or both must be names of one record in our authors table:
        # one shared word is not enough («أحمد خالد توفيق» took a page by «أحمد مراد», audit 2026-10-12 evening #2).
        # A doubtful page is left aside (status «keep», nothing of it stored).
        same = same_author(row["author"], gr["author"])
    if not same:
        _save(key, row["asked_title"], {"_enriched": True}, "keep")
        return False
    gr["_enriched"] = True
    gr["blurb"] = gr.get("description")
    if row.get("description"):
        gr["_keep_description"] = True   # Wikipedia's summary stays; Goodreads' blurb only fills a gap
    if row.get("author"):
        gr.pop("author", None)
    _save(key, row["asked_title"], gr, "keep")
    return True


_LATIN = re.compile(r"[A-Za-z]")


def _family(name):
    """Last Latin word of a name, without accents and «al-»: «Yāsir Ḥārib» -> «harib»."""
    import unicodedata
    t = unicodedata.normalize("NFKD", name or "")
    t = re.sub(r"[^a-z\s-]", "", "".join(c for c in t if not unicodedata.combining(c)).lower())
    words = [w for w in re.split(r"[\s-]+", t) if w and w not in ("al", "el")]
    return words[-1] if words else ""


def same_person(name_a, name_b):
    """One Arabic and one Latin spelling of the same person: true only when our authors table holds the
    Latin spelling (name_latin or an alias) for the Arabic name, with the same family name."""
    ar, lat = (name_a, name_b) if _LATIN.search(name_b or "") else (name_b, name_a)
    if not ar or not lat or _LATIN.search(ar) or not _LATIN.search(lat):
        return False
    fam = _family(lat)
    if len(fam) < 3:
        return False
    conn = get_connection()
    try:
        rows = conn.execute("SELECT name_ar, name_latin, aliases FROM authors").fetchall()
    except Exception:  # noqa: BLE001
        return False
    finally:
        conn.close()
    want = norm(ar)
    for name_ar, name_latin, aliases in rows:
        try:
            al = json.loads(aliases or "[]") if str(aliases or "").startswith("[") else []
        except ValueError:
            al = []
        if norm(name_ar or "") != want and want not in {norm(x) for x in al if isinstance(x, str) and not _LATIN.search(x)}:
            continue
        if any(_family(x) == fam for x in [name_latin] + al if isinstance(x, str) and _LATIN.search(x)):
            return True
    return False


_NAME_DROP = {"د", "دكتور", "الدكتور", "دكتوره", "الدكتوره", "الشيخ", "شيخ", "بن", "بنت", "dr", "al", "el", "bin", "the"}


def _name_words(name):
    """The words of a person's name for comparing two spellings in the same script: accents, hamza seats
    and titles («د.») left out; «عبد الرحمن» = «عبدالرحمن», «أبو», «ابن» joined to the next word; a leading
    «ال» of a long word dropped («الشرقاوي» = «شرقاوي»); Latin in lower case without «al-»."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(name or ""))
    t = norm("".join(c for c in t if not unicodedata.combining(c)))
    t = re.sub(r"(?:^| )(عبد|ابو|ابي|ابن|abd|abu|ibn) (?=\S)", lambda m: " " + m.group(1), t)
    words = [w for w in t.split() if w not in _NAME_DROP]
    return [w[2:] if w.startswith("ال") and len(w) >= 5 else w for w in words]


def _same_name(name_a, name_b):
    """Two spellings in the same script: the same words (spaces aside), or the shorter name (two words at
    least) inside the longer one with the same first and last word («عبد الرحمن منيف» / «عبد الرحمن بن
    إبراهيم منيف»). «أحمد خالد توفيق» / «أحمد مراد» or «أحمد خالد» share words but are not the same name."""
    wa, wb = _name_words(name_a), _name_words(name_b)
    if not wa or not wb:
        return False
    if "".join(wa) == "".join(wb):
        return True
    short, long_ = sorted((wa, wb), key=len)
    return len(short) >= 2 and set(short) <= set(long_) and short[0] == long_[0] and short[-1] == long_[-1]


def _same_record(name_a, name_b):
    """Both names belong to one record of our authors table (its Arabic name, Latin name or an alias)."""
    ka, kb = "".join(_name_words(name_a)), "".join(_name_words(name_b))
    if not ka or not kb:
        return False
    conn = get_connection()
    try:
        rows = conn.execute("SELECT name_ar, name_latin, aliases FROM authors").fetchall()
    except Exception:  # noqa: BLE001
        return False
    finally:
        conn.close()
    for name_ar, name_latin, aliases in rows:
        try:
            al = json.loads(aliases or "[]") if str(aliases or "").startswith("[") else []
        except ValueError:
            al = []
        keys = {"".join(_name_words(x)) for x in [name_ar, name_latin] + list(al) if isinstance(x, str) and x.strip()}
        if ka in keys and kb in keys:
            return True
    return False


def same_author(name_a, name_b):
    """Whether a page's author is our author: an Arabic and a Latin spelling through same_person, two spellings
    in one script by the whole name or one record of our authors table (audit 2026-10-12 evening #2)."""
    if not (name_a or "").strip() or not (name_b or "").strip():
        return False
    if bool(_LATIN.search(name_a)) != bool(_LATIN.search(name_b)):
        return same_person(name_a, name_b)
    return _same_name(name_a, name_b) or _same_record(name_a, name_b)


def brief(row, language="ar"):
    """What the chat may say about the visitor's book, with its sources (for <facts>)."""
    if not row or row.get("status") != "found":
        return None
    src = []
    for u in row.get("sources") or []:
        name = "Goodreads" if "goodreads" in u else "ويكيبيديا" if "wikipedia" in u else "ويكي بيانات" if "wikidata" in u else "Open Library" if "openlibrary" in u else None
        if name and name not in src:
            src.append(name)
    return {k: v for k, v in {
        "title": row.get("title") or row.get("asked_title"), "author": row.get("author"), "year": row.get("year"),
        "genres": (row.get("genres") or [])[:5], "subjects": (row.get("subjects") or [])[:5],
        "summary": (row.get("description") or row.get("blurb") or "")[:500] or None, "rating": row.get("rating"),
        "ratings_count": row.get("ratings_count"),
        "sources": src, "in_our_stock": False}.items() if v not in (None, [], "")}


_HISTORY = re.compile(r"تأليف|صدرت|صدر[ت]? |نشرت|طبع|طبعة|ترجم|مبيع|جائزة|القائمة|دار |للنشر|ISBN|published|award|best ?sell|translated", re.I)


def text_for_meaning(row):
    """What the visitor's book is about, for the meaning search: its genres, subjects and blurb (or the
    summary without its sentences about who published, translated or awarded it), without its title (the
    title's own words found «حذاء لك» and «مقاس 27» for «اخلع حذاءك»)."""
    title = row.get("title") or row.get("asked_title") or ""
    desc = row.get("blurb") or ""
    if not desc:
        desc = " ".join(x for x in re.split(r"(?<=[.!؟?])\s+", row.get("description") or "") if not _HISTORY.search(x))
    for t in {title, row.get("asked_title") or ""}:
        if t:
            desc = desc.replace(t, " ")
    parts = [", ".join(row.get("genres") or []), ", ".join(row.get("subjects") or []), " ".join(desc.split())]
    text = " | ".join(p for p in parts if p)
    return text[:2000] if len(text) >= 40 else None
