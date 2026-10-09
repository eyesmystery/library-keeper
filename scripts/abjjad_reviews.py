"""Fetch reader reviews from Abjjad for researched books (pilot 2026-09-30).

For each book: find its Abjjad page (same title, same author), read the first review pages and save
the reviews (rating, text, date, link) to <out>/<book_id>.json for a researcher to read. It never
writes to the database and never decides attributes.

Usage: backend/venv/bin/python scripts/abjjad_reviews.py --ids 942,1019 --out research/reviews_pilot [--pages 2]
"""
import argparse, glob, html, json, os, re, sqlite3, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lookup_sources as L  # noqa: E402

ROOT = L.ROOT


def _clean(s):
    s = re.sub(r"<br\s*/?>|</p>\s*<p>", "\n", s or "")
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).replace(" ", " ").strip()


def parse_reviews(page):
    out = []
    for blk in page.split('itemtype="https://schema.org/Review"')[1:]:
        body = re.search(r'itemprop="reviewBody".*?class="blurringBody">(.*?)</div>', blk, re.S)
        if not body:
            continue
        text = re.sub(r"[ \t]+", " ", _clean(body.group(1)))
        rating = re.search(r'itemprop="ratingValue" content="(\d)"', blk)
        link = re.search(r'href="(/review/\d+)"', blk)
        date = re.search(r'datetime="([\d-]+)', blk)
        lang = "ar" if re.search(r'class="guts\s+ar"', blk) else "other"
        out.append({"rating": int(rating.group(1)) if rating else None, "text": text, "lang": lang,
                        "date": date.group(1) if date else None,
                        "url": "https://www.abjjad.com" + link.group(1) if link else None})
    return out


def _same_author(a, b):
    """Arabic spellings of foreign names vary (ليو/ليف تولستوي): the family name decides."""
    a, b = L.norm_ar(a or ""), L.norm_ar(b or "")
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    last = lambda x: x.split()[-1].replace("ي", "").replace("و", "")
    return last(a) == last(b)


def find_page(title, author):
    """Abjjad book page with the same title and the same author. When the author spelling does not match but
    exactly one Abjjad book has this exact title, it is taken with author_checked=False (recorded, reviewed later)."""
    key = L.norm_ar(title)
    queries = [title] + ([f"{title} {author}"] if author and author != "غير معروف" else [])
    for i, q in enumerate(queries):  # Abjjad's search is fuzzy; adding the author helps short or common titles
        if i:
            time.sleep(2)
        cands = L.abjjad(q, n=5)
        exact = [c for c in cands if L.norm_ar(c["title"]) == key]
        for c in cands:
            t = L.norm_ar(c["title"])
            if (t == key or t.startswith(key + " ")) and _same_author(author, c["author"]):
                return dict(c, author_checked=True)
        if len(exact) == 1 and i == len(queries) - 1:
            return dict(exact[0], author_checked=False)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids"); ap.add_argument("--status", default=None); ap.add_argument("--out", required=True)
    ap.add_argument("--pages", type=int, default=2); ap.add_argument("--delay", type=float, default=3)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, "data", "library.db"), uri=True)
    if args.ids:
        ids = [int(x) for x in args.ids.split(",")]
    else:  # all researched books of that status, most copies first
        ids = [r[0] for r in con.execute("SELECT book_id FROM books WHERE research_status=? AND active=1 "
                                         "ORDER BY quantity DESC, book_id", (args.status or "matched",))]
    for bid in ids:
        path = os.path.join(args.out, f"{bid}.json")
        if os.path.exists(path):
            continue
        title, author = con.execute("SELECT title, author FROM books WHERE book_id=?", (bid,)).fetchone()
        recs = sorted((p for p in glob.glob(os.path.join(ROOT, "research", "*", f"{bid}.json")) if "reviews" not in p), key=os.path.getmtime)
        if recs:  # the researched display title is cleaner than Rewaa's
            title = json.load(open(recs[-1], encoding="utf-8")).get("title_display") or title
        try:
            rec = {"book_id": bid, "title": title, "author": author, "abjjad": None, "review_count": None, "ratings": [], "reviews": []}
            page = find_page(L.clean_title(title), author)
            time.sleep(args.delay)
            if page:
                rec["abjjad"] = page["url"]
                rec["abjjad_author"] = page.get("author")
                rec["author_checked"] = page.get("author_checked")
                for n in range(1, args.pages + 1):
                    p = L.get(page["url"].rstrip("/") + f"/reviews/{n}")
                    if rec["review_count"] is None:
                        m = re.search(r'itemprop="reviewCount" content="(\d+)"', p)
                        rec["review_count"] = int(m.group(1)) if m else None
                    got = parse_reviews(p)
                    rec["ratings"] += [g["rating"] for g in got if g["rating"]]
                    rec["reviews"] += [g for g in got if g["text"]]  # many Abjjad reviews are a rating only
                    time.sleep(args.delay)
                    if len(got) < 10:  # a full page shows about 20; fewer than 10 means the last page
                        break
        except Exception as e:  # network trouble: skip this book, it is retried on the next run
            print(bid, "error:", e, flush=True)
            time.sleep(30)
            continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        print(bid, title, "->", rec["abjjad"] and f"{len(rec['reviews'])} written reviews, {len(rec['ratings'])} ratings read (of {rec['review_count']})" or "no Abjjad page", flush=True)


if __name__ == "__main__":
    main()
