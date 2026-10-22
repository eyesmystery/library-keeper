"""Original publication year of each work (Ibrahim 2026-10-11, track ORIGYR).

books.publication_year is the year of OUR edition (from Rewaa). This fills books.original_year with
the year the work itself was first published (for a translated book: the original's first
publication), plus where it came from (original_year_source), the page (original_year_ref) and
when it was looked up (original_year_checked_at). Nothing is guessed: a book no source settles
stays empty, and the chat then falls back to the edition year.

Sources, in this order of trust (Ibrahim's order was Goodreads, Open Library, Wikidata; Wikidata
was moved before Open Library after spot checks, see choose()):
  1. Goodreads — the book's page from research/reviews_gr/<id>.json; Work.details.publicationTime
     (the work's first publication) and Work.details.originalTitle. Goodreads sometimes answers 202
     with an empty page (bot check): the fetch rests and tries again later.
  2. Wikidata — P577 of the work item (by the works of our authors' Wikidata ids, then by exact
     title), only when the item's author (P50) is our author and the item is a work, not an edition.
  3. Open Library — first_publish_year of the main record (most editions), searched by the ORIGINAL
     (Latin-script) title and the author's Latin name only, so a translation's own record is not
     taken for the work.

Steps (each resumable; caches in research/original_year_2026-10-11/):
  backend/venv/bin/python scripts/fill_original_year.py fetch-gr [--pace 25] [--limit N]
  backend/venv/bin/python scripts/fill_original_year.py fetch-wd-authors      (Wikidata, by the authors' ids)
  backend/venv/bin/python scripts/fill_original_year.py fetch-wd-titles       (Wikidata, exact titles in batches)
  backend/venv/bin/python scripts/fill_original_year.py fetch-wd [--pace 1]   (Wikidata, one search per book: slow, optional)
  backend/venv/bin/python scripts/fill_original_year.py fetch-ol [--pace 1]
  backend/venv/bin/python scripts/backup_db.py --reason original_year
  backend/venv/bin/python scripts/fill_original_year.py apply [--apply] [--db PATH]

apply never overwrites a year set by staff (original_year_source = 'staff').
"""
import argparse, datetime, glob, json, os, re, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import models                      # noqa: E402
import outside_books as ob         # noqa: E402

OUT = os.path.join(ROOT, "research", "original_year_2026-10-11")
GR_DIR = os.path.join(OUT, "gr")
WD_FILE = os.path.join(OUT, "wd.jsonl")
OL_FILE = os.path.join(OUT, "ol.jsonl")
REVIEWS_GR = os.path.join(ROOT, "research", "reviews_gr")
THIS_YEAR = datetime.date.today().year


def now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")


def log(msg):
    print(f"{now()} {msg}", flush=True)


# ---------------------------------------------------------------------------- names
def _toks(s):
    return [t for t in ob.norm(s).split() if len(t) >= 3]


def author_names(conn, book):
    """Every spelling we know of the book's author(s): the text on the book, the author records'
    Arabic and Latin names and their aliases."""
    names = [book["author"] or ""]
    for r in conn.execute("""SELECT a.name_ar, a.name_latin, a.aliases FROM book_authors ba JOIN authors a USING(author_id)
                             WHERE ba.book_id = ? AND ba.role = 'author'""", (book["book_id"],)):
        names += [r["name_ar"] or "", r["name_latin"] or ""]
        try:
            names += [x for x in json.loads(r["aliases"] or "[]") if isinstance(x, str)]
        except ValueError:
            pass
    return [n for n in names if n.strip()]


def latin_names(names):
    return [n for n in names if re.search(r"[A-Za-z]", n) and not re.search(r"[؀-ۿ]", n)]


def same_author(ours, found):
    """True when a found author name is one of ours: the family name (last word) agrees, or two
    words of three letters or more agree. «Paul Auster» = «بول أوستر» only through the Latin alias."""
    if not found:
        return False
    found_names = found if isinstance(found, (list, tuple)) else [found]
    for f in found_names:
        ft = _toks(f)
        if not ft:
            continue
        for o in ours:
            ot = _toks(o)
            if not ot:
                continue
            if ft[-1] == ot[-1] or len(set(ft) & set(ot)) >= 2:
                return True
    return False


def same_work_title(ours, found):
    a, f = ob.norm(ours), ob.norm(found)
    if not a or not f:
        return False
    if ob.same_title(ours, found):
        return True
    # a subtitle on one side only: «The Sleeper Awakes» / «When the Sleeper Wakes» are not caught, kept strict
    a1, f1 = re.split(r"\s*[:؛—-]\s*", str(ours))[0], re.split(r"\s*[:؛—-]\s*", str(found))[0]
    return ob.norm(a1) == ob.norm(f1) and len(ob.norm(a1)) >= 4


# ---------------------------------------------------------------------------- books
def books_to_do(conn):
    return conn.execute("""SELECT * FROM books WHERE research_status = 'matched' AND active = 1
                           ORDER BY (quantity > 0) DESC, book_id""").fetchall()


def translated(book):
    """Our copy is a translation: a translator is named, or the work's language is not the copy's."""
    lang = (book["original_language"] or "").strip()
    copy = (book["language"] or "ar").strip() or "ar"
    return bool((book["translator"] or "").strip()) or (lang not in ("", copy))


# ---------------------------------------------------------------------------- Goodreads
def gr_year(ms):
    if ms is None:
        return None
    try:
        return (datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=int(ms))).year
    except (OverflowError, ValueError, TypeError):
        return None


def parse_gr(page):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S)
    if not m:
        return None
    st = json.loads(m.group(1))["props"]["pageProps"].get("apolloState") or {}
    out = {}
    for k, v in st.items():
        if k.startswith("Work:") and isinstance(v.get("details"), dict):
            d = v["details"]
            out["work_year"] = gr_year(d.get("publicationTime"))
            out["original_title"] = (d.get("originalTitle") or "").strip() or None
            out["work_url"] = d.get("webUrl")
        elif k.startswith("Book:") and v.get("title") and "title" not in out:
            out["title"] = v.get("title")
            out["edition_year"] = gr_year((v.get("details") or {}).get("publicationTime"))
            out["book_url"] = v.get("webUrl")
            edge = v.get("primaryContributorEdge") or {}
            out["_author_ref"] = ((edge.get("node") or {}).get("__ref"))
    if out.get("_author_ref") and isinstance(st.get(out["_author_ref"]), dict):
        out["author"] = st[out["_author_ref"]].get("name")
    out.pop("_author_ref", None)
    return out if out.get("title") else None


def fetch_gr(args):
    os.makedirs(GR_DIR, exist_ok=True)
    conn = models.get_connection()
    wanted = {b["book_id"] for b in books_to_do(conn)}
    files = sorted(glob.glob(os.path.join(REVIEWS_GR, "*.json")), key=lambda f: int(re.sub(r"\D", "", os.path.basename(f)) or 0))
    todo = []
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except ValueError:
            continue
        bid = d.get("book_id")
        if bid not in wanted or not d.get("goodreads"):
            continue
        if os.path.exists(os.path.join(GR_DIR, f"{bid}.json")):
            continue
        todo.append(d)
    log(f"Goodreads: {len(todo)} pages to fetch (pace {args.pace}s)")
    rests, done = 0, 0
    for d in todo[: args.limit or None]:
        url = d["goodreads"]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ob.UA_WEB, "Accept-Language": "en"})
            with ob._opener.open(req, timeout=25) as r:
                page, status, final = r.read().decode("utf-8", "replace"), r.status, r.geturl()
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            if code == 404:
                json.dump({"book_id": d["book_id"], "url": url, "error": "404", "fetched_at": now()},
                          open(os.path.join(GR_DIR, f"{d['book_id']}.json"), "w", encoding="utf-8"), ensure_ascii=False)
                time.sleep(args.pace)
                continue
            log(f"{d['book_id']}: {e!r}; rest {args.rest}s")
            rests += 1
            if rests > args.max_rests:
                break
            time.sleep(args.rest)
            continue
        if status == 202 or not page.strip():
            rests += 1
            log(f"{d['book_id']}: Goodreads challenge (202, empty page); rest {args.rest}s ({rests}/{args.max_rests})")
            if rests > args.max_rests:
                break
            time.sleep(args.rest)
            continue
        info = parse_gr(page) or {}
        info.update(book_id=d["book_id"], url=url, final_url=final, found_by=d.get("found_by"),
                    our_title=d.get("title"), our_author=d.get("author"), fetched_at=now())
        json.dump(info, open(os.path.join(GR_DIR, f"{d['book_id']}.json"), "w", encoding="utf-8"), ensure_ascii=False)
        done += 1
        rests = 0
        log(f"{d['book_id']}: work {info.get('work_year')} · edition {info.get('edition_year')} · {info.get('original_title') or ''}")
        time.sleep(args.pace)
    log(f"Goodreads: {done} fetched this run")


def load_gr():
    out = {}
    for f in glob.glob(os.path.join(GR_DIR, "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
            out[int(d["book_id"])] = d
        except (ValueError, KeyError):
            pass
    # REV's own Goodreads pages (research/reviews_gr/<id>.json) already carry the work's first
    # publication (page.first_published_ms = Work.details.publicationTime) — read them instead of
    # fetching the same page again (Ibrahim 2026-10-15: sources other than our own Goodreads fetch)
    for f in glob.glob(os.path.join(REVIEWS_GR, "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
            bid = int(d["book_id"])
        except (ValueError, KeyError, TypeError):
            continue
        p = d.get("page") or {}
        if bid in out and out[bid].get("work_year"):
            continue
        y = gr_year(p.get("first_published_ms"))
        if not y or not d.get("goodreads"):
            continue
        out[bid] = {"book_id": bid, "work_year": y, "original_title": (p.get("original_title") or "").strip() or None,
                    "author": d.get("gr_author"), "found_by": d.get("found_by"), "url": d["goodreads"],
                    "final_url": d["goodreads"], "edition_year": gr_year(p.get("published_ms")), "via": "reviews_gr",
                    "fetched_at": datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")}
    return out


_SAVED = None


def saved_original_titles():
    """Original (Latin-script) titles written down by the earlier identification and review research
    (research/batch_*, reviews, reviews_cards, pilot). Used only as a search key: the year still has
    to come from a source page whose title and author agree."""
    global _SAVED
    if _SAVED is None:
        _SAVED = {}
        for f in glob.glob(os.path.join(ROOT, "research", "*", "*.json")):
            if "original_year_" in f:
                continue
            try:
                d = json.load(open(f, encoding="utf-8"))
            except (ValueError, OSError, UnicodeDecodeError):
                continue
            for x in (d if isinstance(d, list) else [d]):
                if isinstance(x, dict) and isinstance(x.get("original_title"), str) and x.get("book_id"):
                    ot = x["original_title"].strip()
                    if ot and not re.search(r"[؀-ۿ]", ot):
                        try:
                            _SAVED.setdefault(int(x["book_id"]), ot)
                        except (TypeError, ValueError):
                            pass
    return _SAVED


def original_title(book_id, gr):
    """Goodreads' original title when it is in another script, else the one saved by earlier research."""
    ot = ((gr.get(book_id) or {}).get("original_title") or "").strip()
    if ot and not re.search(r"[؀-ۿ]", ot):
        return ot
    return saved_original_titles().get(book_id)


# ---------------------------------------------------------------------------- Wikidata
EDITION_CLASSES = {"Q3331189", "Q1279564"}   # version / edition / translation; literary-work subclasses pass


def _done_ids(path):
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try:
                d = json.loads(line)
                k = int(d["book_id"])
                if k in out and out[k].get("hit") and not d.get("hit"):
                    continue          # a later try that found nothing never hides an earlier find
                out[k] = d
            except (ValueError, KeyError):
                pass
    return out


def wd_entities(ids):
    d = ob._json("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props=claims|labels"
                 "&languages=ar|en&ids=" + "|".join(ids), 15)
    return d.get("entities") or {}


def _claims(e, p):
    out = []
    for c in (e.get("claims") or {}).get(p) or []:
        if c.get("rank") == "deprecated":
            continue
        v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        out.append(v)
    return [v for v in out if v]


def wd_year(e):
    years = []
    for v in _claims(e, "P577"):
        t = v.get("time", "") if isinstance(v, dict) else ""
        m = re.match(r"([+-])(\d{1,4})-", t)
        if m and m.group(1) == "+" and v.get("precision", 9) >= 9:
            years.append(int(m.group(2)))
    return min(years) if years else None


def wd_search(title, lang):
    d = ob._json("https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&type=item&limit=7"
                 f"&language={lang}&uselang={lang}&search=" + urllib.parse.quote(title), 15)
    return d.get("search") or []


def wd_lookup(book, names, original_title=None):
    tries = [(book["title"], "ar" if re.search(r"[؀-ۿ]", book["title"]) else "en")]
    if original_title:
        tries.append((original_title, "en"))
    seen = set()
    for t, lang in tries:
        hits = [h for h in wd_search(t, lang) if h["id"] not in seen and same_work_title(t, h.get("label") or "")]
        seen |= {h["id"] for h in hits}
        if not hits:
            continue
        ents = wd_entities([h["id"] for h in hits])
        for h in hits:
            e = ents.get(h["id"]) or {}
            kinds = {(v or {}).get("id") for v in _claims(e, "P31") if isinstance(v, dict)}
            if kinds & EDITION_CLASSES or _claims(e, "P629"):        # an edition of a work: not the work
                continue
            if ob._NOT_WORK.search(h.get("description") or ""):
                continue
            auth_ids = [(v or {}).get("id") for v in _claims(e, "P50") if isinstance(v, dict)]
            if not auth_ids:
                continue
            aents = wd_entities(auth_ids[:5])
            alabels = [((a.get("labels") or {}).get(l) or {}).get("value") for a in aents.values() for l in ("ar", "en")]
            if not same_author(names, [x for x in alabels if x]):
                continue
            y = wd_year(e)
            if y:
                return {"year": y, "qid": h["id"], "label": h.get("label"), "authors": [x for x in alabels if x],
                        "ref": "https://www.wikidata.org/wiki/" + h["id"]}
    return None


def fetch_wd(args):
    conn = models.get_connection()
    done = _done_ids(WD_FILE)
    gr = load_gr()
    todo = [b for b in books_to_do(conn) if b["book_id"] not in done and b["book_id"] % args.shards == args.shard]
    log(f"Wikidata: {len(todo)} books")
    with open(WD_FILE, "a", encoding="utf-8") as fh:
        for i, b in enumerate(todo[: args.limit or None]):
            names = author_names(conn, b)
            hit, failed = None, True
            for attempt in range(6):
                try:
                    hit, failed = wd_lookup(b, names, original_title(b["book_id"], gr)), False
                    break
                except Exception as e:  # noqa: BLE001
                    # 429: wait what Wikidata asks (Retry-After), longer each time, then the same book again
                    wait = getattr(e, "headers", None) and e.headers.get("Retry-After")
                    wait = max(int(wait) if str(wait or "").isdigit() else 0, 10 * (attempt + 1))
                    log(f"{b['book_id']}: {e!r}; wait {wait}s")
                    time.sleep(wait)
            if failed:
                continue
            fh.write(json.dumps({"book_id": b["book_id"], "hit": hit, "checked_at": now()}, ensure_ascii=False) + "\n")
            fh.flush()
            if hit:
                log(f"{b['book_id']} {b['title']}: {hit['year']} ({hit['qid']} «{hit['label']}»)")
            if i % 200 == 0:
                log(f"Wikidata: {i}/{len(todo)}")
            time.sleep(args.pace)


SPARQL = "https://query.wikidata.org/sparql"
SPARQL_WORKS = """SELECT ?author ?work ?lar ?len ?date WHERE {
  VALUES ?author { %s }
  ?work wdt:P50 ?author ; wdt:P577 ?date .
  FILTER NOT EXISTS { ?work wdt:P629 [] }
  FILTER NOT EXISTS { ?work wdt:P31 wd:Q3331189 }
  OPTIONAL { ?work rdfs:label ?lar FILTER(LANG(?lar) = "ar") }
  OPTIONAL { ?work rdfs:label ?len FILTER(LANG(?len) = "en") }
}"""


def author_qids(conn, book_id):
    out = []
    for r in conn.execute("""SELECT a.source_urls FROM book_authors ba JOIN authors a USING(author_id)
                             WHERE ba.book_id = ? AND ba.role = 'author'""", (book_id,)):
        out += re.findall(r"wikidata\.org/wiki/(Q\d+)", r["source_urls"] or "")
    return out


def fetch_wd_authors(args):
    """Wikidata by author: the works of every author we hold a Wikidata id for (author research),
    a few SPARQL queries instead of a title search per book; matched to our books by title (ours,
    or Goodreads' original title). A book whose author has an id but no matching work is marked
    tried, so the slower title search skips it."""
    conn = models.get_connection()
    gr = load_gr()
    books = [(b, author_qids(conn, b["book_id"])) for b in books_to_do(conn)]
    books = [(b, q) for b, q in books if q]
    qids = sorted({q for _, qs in books for q in qs})
    log(f"Wikidata by author: {len(qids)} authors, {len(books)} books")
    works = {}
    for i in range(0, len(qids), 80):
        q = SPARQL_WORKS % " ".join("wd:" + x for x in qids[i:i + 80])
        for attempt in range(5):
            try:
                req = urllib.request.Request(SPARQL + "?format=json&query=" + urllib.parse.quote(q),
                                             headers={"User-Agent": ob.UA_API, "Accept": "application/sparql-results+json"})
                with ob._opener.open(req, timeout=90) as r:
                    rows = json.loads(r.read().decode("utf-8"))["results"]["bindings"]
                break
            except Exception as e:  # noqa: BLE001
                log(f"SPARQL batch {i}: {e!r}; wait {30 * (attempt + 1)}s")
                time.sleep(30 * (attempt + 1))
        else:
            continue
        for row in rows:
            a = row["author"]["value"].rsplit("/", 1)[1]
            w = row["work"]["value"].rsplit("/", 1)[1]
            m = re.match(r"(\d{4})-", row["date"]["value"])
            if not m:
                continue
            e = works.setdefault(a, {}).setdefault(w, {"labels": set(), "year": int(m.group(1))})
            e["year"] = min(e["year"], int(m.group(1)))
            for k in ("lar", "len"):
                if row.get(k):
                    e["labels"].add(row[k]["value"])
        log(f"SPARQL: {min(i + 80, len(qids))}/{len(qids)} authors")
        time.sleep(2)
    hits = 0
    with open(WD_FILE, "a", encoding="utf-8") as fh:
        for b, qs in books:
            titles = [b["title"]] + ([original_title(b["book_id"], gr)] if original_title(b["book_id"], gr) else [])
            found = None
            for a in qs:
                for w, e in (works.get(a) or {}).items():
                    lab = next((l for l in e["labels"] for t in titles if same_work_title(t, l)), None)
                    if lab and (found is None or e["year"] < found["year"]):
                        found = {"year": e["year"], "qid": w, "label": lab, "authors": [a], "via": "author",
                                 "ref": "https://www.wikidata.org/wiki/" + w}
            hits += bool(found)
            fh.write(json.dumps({"book_id": b["book_id"], "hit": found, "checked_at": now(), "via": "author"}, ensure_ascii=False) + "\n")
    log(f"Wikidata by author: {hits} books matched to a work")


SPARQL_TITLES = """SELECT ?t ?work ?date ?al WHERE {
  VALUES ?t { %s }
  ?work rdfs:label ?t ; wdt:P577 ?date ; wdt:P50 ?a .
  FILTER NOT EXISTS { ?work wdt:P629 [] }
  FILTER NOT EXISTS { ?work wdt:P31 wd:Q3331189 }
  ?a rdfs:label ?al . FILTER(LANG(?al) IN ("ar", "en"))
}"""


def _sparql(q):
    for attempt in range(5):
        try:
            req = urllib.request.Request(SPARQL, data=urllib.parse.urlencode({"query": q, "format": "json"}).encode(),
                                         headers={"User-Agent": ob.UA_API, "Accept": "application/sparql-results+json"})
            with ob._opener.open(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))["results"]["bindings"]
        except Exception as e:  # noqa: BLE001
            log(f"SPARQL: {e!r}; wait {30 * (attempt + 1)}s")
            time.sleep(30 * (attempt + 1))
    return None


def _lit(text, lang):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"@' + lang


def fetch_wd_titles(args):
    """Wikidata by exact title, in batches (SPARQL): our title and its usual spellings in Arabic,
    plus the original title in English; the work must name our author (P50) and must not be an
    edition. Replaces the slow one-search-per-book API for the books the author pass did not settle."""
    conn = models.get_connection()
    done = _done_ids(WD_FILE)
    gr = load_gr()
    todo = [b for b in books_to_do(conn) if not (done.get(b["book_id"]) or {}).get("hit")
            and (done.get(b["book_id"]) or {}).get("via") != "title-sparql"]
    log(f"Wikidata by title: {len(todo)} books")
    keys = {}
    for b in todo:
        variants = set()
        for t in ob.spellings(b["title"]):
            t = re.sub(r"\s+", " ", t).strip()
            lang = "ar" if re.search(r"[؀-ۿ]", t) else "en"
            variants |= {(t, lang)} | ({(t + " (رواية)", "ar")} if lang == "ar" else set())
        ot = original_title(b["book_id"], gr)
        if ot:
            variants.add((ot, "en"))
        for v in variants:
            keys.setdefault(v, []).append(b["book_id"])
    found = {}
    items = list(keys)
    for i in range(0, len(items), 60):
        rows = _sparql(SPARQL_TITLES % " ".join(_lit(t, l) for t, l in items[i:i + 60]))
        if rows is None:
            continue
        works = {}
        for r in rows:
            m = re.match(r"(\d{4})-", r["date"]["value"])
            if not m:
                continue
            w = r["work"]["value"].rsplit("/", 1)[1]
            e = works.setdefault((r["t"]["value"], r["t"].get("xml:lang"), w), {"year": int(m.group(1)), "authors": set()})
            e["year"] = min(e["year"], int(m.group(1)))
            e["authors"].add(r["al"]["value"])
        for (t, l, w), e in works.items():
            for bid in keys.get((t, l), []):
                b = next(x for x in todo if x["book_id"] == bid)
                if not same_author(author_names(conn, b), list(e["authors"])):
                    continue
                if bid not in found or e["year"] < found[bid]["year"]:
                    found[bid] = {"year": e["year"], "qid": w, "label": t, "authors": sorted(e["authors"])[:4], "via": "title",
                                  "ref": "https://www.wikidata.org/wiki/" + w}
        log(f"SPARQL titles: {min(i + 60, len(items))}/{len(items)} · {len(found)} books found")
        time.sleep(1.5)
    with open(WD_FILE, "a", encoding="utf-8") as fh:
        for b in todo:
            fh.write(json.dumps({"book_id": b["book_id"], "hit": found.get(b["book_id"]), "checked_at": now(), "via": "title-sparql"},
                                ensure_ascii=False) + "\n")
    log(f"Wikidata by title: {len(found)} books matched")


# ---------------------------------------------------------------------------- Open Library
def ol_lookup(book, names, original_title):
    """By the Latin-script title (the original, or our title when our copy is in English) and the
    author's Latin name: an Arabic title would find the translation's own record."""
    title = original_title if original_title and not re.search(r"[؀-ۿ]", original_title) else (
        book["title"] if not re.search(r"[؀-ۿ]", book["title"] or "") else None)
    lat = latin_names(names)
    if not title or not lat:
        return None
    best = None
    for author in lat[:2]:
        d = ob._json("https://openlibrary.org/search.json?limit=10&fields=key,title,author_name,first_publish_year,edition_count"
                     "&title=" + urllib.parse.quote(title) + "&author=" + urllib.parse.quote(author), 15)
        # one work is often split over several Open Library records, and the stray ones carry odd
        # years (Hypatia 1800): the main record, the one with the most editions
        for doc in d.get("docs") or []:
            if not same_work_title(title, doc.get("title") or "") or not same_author(names, doc.get("author_name") or []):
                continue
            y = doc.get("first_publish_year")
            if isinstance(y, int) and (best is None or (doc.get("edition_count") or 0) > best["editions"]):
                best = {"year": y, "title": doc.get("title"), "authors": (doc.get("author_name") or [])[:3],
                        "editions": doc.get("edition_count") or 0, "ref": "https://openlibrary.org" + doc.get("key", "")}
        if best:
            break
    return best


def fetch_ol(args):
    conn = models.get_connection()
    done = _done_ids(OL_FILE)
    gr = load_gr()
    wd = _done_ids(WD_FILE)
    todo = [b for b in books_to_do(conn) if b["book_id"] not in done and b["book_id"] % args.shards == args.shard]
    log(f"Open Library: {len(todo)} books")
    with open(OL_FILE, "a", encoding="utf-8") as fh:
        for i, b in enumerate(todo[: args.limit or None]):
            names = author_names(conn, b)
            orig = original_title(b["book_id"], gr)
            if not orig:
                w = (wd.get(b["book_id"]) or {}).get("hit") or {}
                orig = w.get("label") if w.get("label") and not re.search(r"[؀-ۿ]", w["label"]) else None
            try:
                hit = ol_lookup(b, names, orig)
            except Exception as e:  # noqa: BLE001
                log(f"{b['book_id']}: {e!r}; rest 60s")
                time.sleep(60)
                continue
            if hit is None and not (orig or not re.search(r"[؀-ۿ]", b["title"] or "")):
                continue          # nothing to search with yet: tried again once a Goodreads original title arrives
            fh.write(json.dumps({"book_id": b["book_id"], "hit": hit, "searched": orig or b["title"], "checked_at": now()},
                                ensure_ascii=False) + "\n")
            fh.flush()
            if hit:
                log(f"{b['book_id']} {b['title']}: {hit['year']} ({hit['ref']})")
            if i % 200 == 0:
                log(f"Open Library: {i}/{len(todo)}")
            time.sleep(args.pace)


# ---------------------------------------------------------------------------- choosing
def choose(book, names, gr, ol, wd):
    """-> (year, source, ref, checked_at, note) or (None, None, None, None, why). In order: Goodreads,
    Open Library, Wikidata. A year after our own edition's year cannot be the first publication and
    is refused; so is a year before 1000 or in the future."""
    edition = book["publication_year"]
    tried = []
    g = gr.get(book["book_id"])
    if g and g.get("work_year"):
        ok_author = same_author(names, g.get("author")) or g.get("found_by") in ("isbn", "record", None)
        # a translation Goodreads has not joined to its original carries the translation's own year:
        # for a translated book the work must name an original title in another script
        ot = g.get("original_title") or ""
        joined = not translated(book) or (ot and not re.search(r"[؀-ۿ]", ot))
        if ok_author and joined:
            tried.append((g["work_year"], "goodreads", g.get("final_url") or g.get("url"), g["fetched_at"]))
    # Wikidata before Open Library (changed from Ibrahim's order on evidence, 2026-10-11): Open
    # Library's first_publish_year is the earliest dated edition it holds, and wrongly dated editions
    # pull it off even on the main record (Tancred 1830 for 1847, Bleak House 1850 for 1853, Atomic
    # Habits 2016 for 2018). Open Library is used only when neither Goodreads nor Wikidata has the work.
    w = (wd.get(book["book_id"]) or {}).get("hit")
    if w:
        tried.append((w["year"], "wikidata", w["ref"], wd[book["book_id"]]["checked_at"]))
    o = (ol.get(book["book_id"]) or {}).get("hit")
    work_lang = (book["original_language"] or "").strip() or ((book["language"] or "").strip() if not translated(book) else "")
    if o and work_lang != "en":
        # searched by an English title, a work first written in another language finds its English
        # translation's record (Kadare's «من أعاد دورونتين؟» -> 2004): Open Library only for English works
        o = None
    if o:
        tried.append((o["year"], "openlibrary", o["ref"], ol[book["book_id"]]["checked_at"]))
    why = []
    srcs = {t[1]: t for t in tried}
    if "openlibrary" in srcs and len(srcs) > 1 and abs(srcs["openlibrary"][0] - tried[0][0]) > 1:
        why.append(f"openlibrary {srcs['openlibrary'][0]} disagrees with {tried[0][1]} {tried[0][0]}")
    for y, src, ref, at in tried:
        if not 1000 <= y <= THIS_YEAR:
            why.append(f"{src} {y}: out of range")
            continue
        if edition and y > int(edition):
            why.append(f"{src} {y}: after our edition {edition}")
            continue
        return y, src, ref, at, ("; ".join(why) or None)
    return None, None, None, None, "; ".join(why) or "no source"


def apply(args):
    if args.db:
        models.DB_PATH = args.db
    models.init_db()                       # adds the four columns when the database is older
    conn = models.get_connection()
    gr, ol, wd = load_gr(), _done_ids(OL_FILE), _done_ids(WD_FILE)
    rows, report, counts = [], [], {"goodreads": 0, "openlibrary": 0, "wikidata": 0, "none": 0, "staff_kept": 0, "conflict": 0}
    for b in books_to_do(conn):
        if b["original_year_source"] == "staff":
            counts["staff_kept"] += 1
            continue
        if getattr(args, "only_missing", False) and b["original_year"] is not None:
            continue
        names = author_names(conn, b)
        y, src, ref, at, why = choose(b, names, gr, ol, wd)
        if y is None:
            counts["none"] += 1
            if why and why != "no source":
                counts["conflict"] += 1
                report.append({"book_id": b["book_id"], "title": b["title"], "edition_year": b["publication_year"], "refused": why})
            continue
        counts[src] += 1
        rows.append((y, src, ref, at, b["book_id"]))
        report.append({"book_id": b["book_id"], "title": b["title"], "author": b["author"], "edition_year": b["publication_year"],
                       "original_year": y, "source": src, "ref": ref, "translated": translated(b), **({"note": why} if why else {})})
    json.dump(report, open(os.path.join(OUT, "apply_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if args.apply:
        conn.executemany("""UPDATE books SET original_year = ?, original_year_source = ?, original_year_ref = ?,
                            original_year_checked_at = ? WHERE book_id = ? AND COALESCE(original_year_source, '') != 'staff'""", rows)
        conn.commit()
    conn.close()
    print(json.dumps({"books_with_year": len(rows), **counts, "applied": bool(args.apply)}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("step", choices=["fetch-gr", "fetch-wd-authors", "fetch-wd-titles", "fetch-wd", "fetch-ol", "apply"])
    p.add_argument("--pace", type=float, default=None)
    p.add_argument("--rest", type=int, default=1800)
    p.add_argument("--max-rests", type=int, default=6)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--only-missing", action="store_true")   # fill books with no year yet; never change a year already set
    p.add_argument("--db")
    p.add_argument("--shard", type=int, default=0)     # several fetch-wd / fetch-ol processes side by side:
    p.add_argument("--shards", type=int, default=1)    # each takes the books whose id % shards == shard
    a = p.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.pace is None:
        a.pace = 25 if a.step == "fetch-gr" else 1.0
    {"fetch-gr": fetch_gr, "fetch-wd-authors": fetch_wd_authors, "fetch-wd-titles": fetch_wd_titles, "fetch-wd": fetch_wd, "fetch-ol": fetch_ol, "apply": apply}[a.step](a)


if __name__ == "__main__":
    main()
