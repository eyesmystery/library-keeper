"""Goodreads title-search phase of the reader-review fetch (2026-10-03).

For matched books with no Goodreads page in their research record and no ISBN: search Goodreads by title and
accept a result only when it is the same title by the same author. This script only resolves page ids. It
never fetches reviews and never writes to the database.

  accepted  -> <out>/_search_found.json   {book_id: {gid, gr_title, gr_authors, query, ratings}}
               scripts/goodreads_reviews.py reads this file and fetches the page (found_by "search")
  no result -> <out>/_search_none.json    {book_id: {queries, candidates}}   (kept for a later manual look)

Rules (Ibrahim, 2026-10-02: the page must be the same book and the same author):
  title   the Goodreads title (text in brackets dropped) equals ours, or one begins with the other followed
          by more words (a subtitle or series label); Latin titles are compared the same way. Part numbers
          must agree: a page for another volume, or a numbered page for an unnumbered title, is refused.
  author  a contributor listed as author (not translator/editor/illustrator) is the same person:
          Arabic names must be equal word for word (stored aliases count; an extra or different name word
          is a different person); Latin names need the same family name and the same first name or initial.
          An Arabic name is compared with a Latin one only through the Latin name stored in `authors`.
Up to two searches per book. Goodreads throttles per computer: a throttled page makes the run rest (--rest)
and retry, up to --max-rests times. Resumable.

Usage: backend/venv/bin/python scripts/goodreads_title_search.py --ids-file research/reviews_gr/_search_ids.txt --out research/reviews_gr
"""
import argparse, json, os, re, sqlite3, sys, time, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lookup_sources as L  # noqa: E402

ROOT = L.ROOT
NOT_AUTHOR = re.compile(r"transl|ترجم|مترجم|edit|تحرير|محرر|illustr|رسوم|رسام|introduc|تقديم|مقدم|narrat|foreword|مراجع|تحقيق|إعداد", re.I)


def ar_key(s):
    """Arabic name key: norm_ar, titles dropped, «عبد X» joined, hamza forms unified."""
    s = L.norm_ar(re.sub(r"\(.*?\)", " ", s or ""))
    s = re.sub(r"[ؤئء]", "ء", s)
    s = re.sub(r"^(د|دكتور|الدكتور|ا د|أ د|م|الشيخ|الامام)\s+", "", s)
    return re.sub(r"\bعبد\s+", "عبد", s).strip()


def lat_tokens(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return [t for t in re.sub(r"[^a-z ]", " ", s.replace(".", " ").replace("-", " ")).split() if t]


def same_latin(a, b):
    a, b = lat_tokens(a), lat_tokens(b)
    if len(a) < 2 or len(b) < 2:
        return bool(a) and a == b
    if a[-1] != b[-1]:
        return False
    x, y = a[0], b[0]
    return x == y or (x[0] == y[0] and min(len(x), len(y)) == 1)


def same_person(person, name):
    """person = {"ar": [Arabic spellings], "latin": Latin name or None}; name = a Goodreads contributor name."""
    if re.search(r"[A-Za-z]", name or ""):
        return bool(person.get("latin")) and same_latin(person["latin"], name)
    k = ar_key(name)
    return bool(k) and any(ar_key(x) == k for x in person.get("ar") or [])


_DIG = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_VOLWORD = re.compile(r"(^|\s)(ال)?(جزء|مجلد|اجزاء|مجلدات)(\s|$)")


def volumes(title):
    """Part/volume numbers written in a title (edition markers and years are not volumes)."""
    t = re.sub(r"\(\s*\d{4}\s*\)|ط\s*\d+", " ", (title or "").translate(_DIG))
    return {int(x) for x in re.findall(r"\d+", t) if len(x) < 4}


def same_volume(ours, theirs):
    """Both titles name no part, or the Goodreads title names a part that ours names too («4/1» holds 1 and 4)."""
    a, b = volumes(ours), volumes(theirs)
    return (not a and not b) or (bool(a) and bool(b) and b <= a)


def same_title(ours, theirs):
    if not same_volume(ours, theirs):
        return False
    if re.search(r"[A-Za-z]", ours or "") and not re.search(r"[؀-ۿ]", ours):
        a, b = " ".join(lat_tokens(L.clean_title(ours))), " ".join(lat_tokens(re.sub(r"\(.*?\)", " ", theirs or "")))
        return len(a) > 3 and len(b) > 3 and (a == b or b.startswith(a + " ") or a.startswith(b + " "))
    a = L.norm_ar(L.clean_title(ours).replace("_", " "))
    b = L.norm_ar(re.sub(r"\(.*?\)", " ", theirs or "").replace("_", " "))  # Goodreads has hashtag-style titles («باتمان_حلوان#»)
    if not a or not b:
        return False
    if a == b:
        return True
    if b.startswith(a + " "):
        tail = b[len(a):]
    elif a.startswith(b + " ") and len(b.split()) >= 2:
        tail = a[len(b):]
    else:
        return False
    return not _VOLWORD.search(tail)  # «… الجزء الثاني» on one side only is another volume


def pick(title, persons, cands):
    """The accepted candidate (most ratings first) or None."""
    good = []
    for c in cands:
        if not same_title(title, c.get("title")):
            continue
        names = [x.get("name") for x in c.get("authors") or [] if not NOT_AUTHOR.search(x.get("role") or "")]
        if any(same_person(p, n) for p in persons for n in names):
            good.append(c)
    return max(good, key=lambda c: c.get("ratings") or 0) if good else None


def persons_of(con, bid, author):
    rows = con.execute("SELECT a.name_ar, a.name_latin, a.aliases FROM book_authors ba JOIN authors a USING(author_id) "
                       "WHERE ba.book_id=? AND ba.role='author' ORDER BY ba.position", (bid,)).fetchall()
    out = []
    for ar, lat, al in rows:
        try:
            al = json.loads(al or "[]")
        except ValueError:
            al = []
        names = [ar] + [x for x in al if isinstance(x, str)]
        latin = lat or next((x for x in names if re.search(r"[A-Za-z]", x) and not re.search(r"[؀-ۿ]", x)), None)
        out.append({"ar": [x for x in names if re.search(r"[؀-ۿ]", x)], "latin": latin})
    if not out and author:
        first = author.split("،")[0].strip()
        out.append({"ar": [first] if re.search(r"[؀-ۿ]", first) else [], "latin": None if re.search(r"[؀-ۿ]", first) else first})
    return out


def queries(title, persons, translated):
    t = L.clean_title(title)
    main = L.clean_title(re.split(r"\s+-\s+|:|\(", title or "")[0])
    if len(t.split()) >= 7 and main and main != t:  # a long title with a subtitle is searched by its main part
        t = main
    p = persons[0] if persons else {}
    ar, lat = (p.get("ar") or [None])[0], p.get("latin")
    if translated or not ar:
        qs = [f"{t} {lat}" if lat else None, t]
    else:
        qs = [f"{t} {ar}", t]
    return [q for q in dict.fromkeys(qs) if q]


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _load(path):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}


def P(*a, **k):
    """print with the local time first, so the logs show how many answers come per hour."""
    k.pop("flush", None)
    print(time.strftime("%H:%M:%S"), *a, flush=True, **k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True); ap.add_argument("--ids-file", required=True)
    ap.add_argument("--delay", type=float, default=30); ap.add_argument("--rest", type=float, default=1800)
    ap.add_argument("--max-rests", type=int, default=40, help="stop after this many rests in total (0 = no total limit)")
    ap.add_argument("--max-idle-rests", type=int, default=0, help="stop after this many rests in a row with no search answered (0 = off)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, "data", "library.db"), uri=True)
    fp, np_ = os.path.join(args.out, "_search_found.json"), os.path.join(args.out, "_search_none.json")
    found, none = _load(fp), _load(np_)
    st = [0, 0]
    while True:
        # the id file, then the books whose ISBN gave no usable page (written by goodreads_reviews.py while it runs)
        ids = [int(x) for x in open(args.ids_file).read().replace("\n", ",").split(",") if x.strip()]
        ids += [int(x) for x in _load(os.path.join(args.out, "_isbn_failed.json")) if int(x) not in set(ids)]
        todo = [b for b in ids if str(b) not in found and str(b) not in none
                and not os.path.exists(os.path.join(args.out, f"{b}.json"))]
        if args.limit:
            todo = todo[:args.limit]
        P(f"{len(todo)} books to search", flush=True)
        if not todo:
            return 4
        stopped = _run(args, con, todo, found, none, fp, np_, st)
        if stopped or args.limit:
            return 3 if stopped else 0


def _run(args, con, todo, found, none, fp, np_, st):
    """One pass over todo. True = stop the whole run (too many rests)."""
    rests, idle = st
    for bid in todo:
        row = con.execute("SELECT title, author, translator FROM books WHERE book_id=?", (bid,)).fetchone()
        if not row or not row[1] or row[1] == "غير معروف":
            continue
        title, author, translator = row
        persons = persons_of(con, bid, author)
        seen, hit, used = [], None, []
        for q in queries(title, persons, bool(translator)):
            while True:
                try:
                    cands = L.goodreads(q, n=8)
                    idle = 0
                    break
                except Exception as e:
                    rests += 1
                    idle += 1
                    if (args.max_rests and rests > args.max_rests) or (args.max_idle_rests and idle > args.max_idle_rests):
                        P(f"stopping after {rests} rests ({idle - 1} in a row without an answer):", e, flush=True)
                        return True
                    P(f"{bid}: {e}; resting {args.rest:.0f} s ({rests}/{args.max_rests or '-'}, {idle} in a row)", flush=True)
                    time.sleep(args.rest)
            used.append(q)
            seen += [c for c in cands if c["id"] not in {x["id"] for x in seen}]
            hit = pick(title, persons, cands)
            time.sleep(args.delay)
            if hit:
                break
        if hit:
            found[str(bid)] = {"gid": hit["id"], "gr_title": hit["title"], "gr_authors": [x["name"] for x in hit["authors"]],
                               "ratings": hit.get("ratings"), "query": used[-1], "title": title, "author": author}
            _save(fp, found)
            P(bid, title, "/", author, "->", hit["id"], hit["title"], "/", ", ".join(x["name"] for x in hit["authors"]),
                  f"({hit.get('ratings')} ratings)", flush=True)
        else:
            none[str(bid)] = {"title": title, "author": author, "queries": used,
                              "candidates": [{"id": c["id"], "title": c["title"], "authors": [x["name"] for x in c["authors"]],
                                              "ratings": c.get("ratings")} for c in seen[:6]]}
            _save(np_, none)
            P(bid, title, "/", author, "-> no safe result", f"({len(seen)} candidates)", flush=True)
        st[0], st[1] = rests, idle
    st[0], st[1] = rests, idle
    return False


if __name__ == "__main__":
    sys.exit(main() or 0)  # 3 = stopped because Goodreads kept refusing, 4 = nothing left to search
