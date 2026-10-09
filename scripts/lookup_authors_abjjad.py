"""Author style from Abjjad (أبجد): identity, reader rating, books and the categories of their books.

Run on the Mac (needs internet):
  python3 scripts/lookup_authors_abjjad.py fetch <db_path> <out_dir> [--status thin,needs_sources] [--limit N] [--delay 2.0]
  python3 scripts/lookup_authors_abjjad.py apply <db_path> <out_dir>/results.json [--apply]

fetch  searches Abjjad by the author's Arabic name, keeps the author link whose name is exactly ours
       (normalized), reads the author page (rating, reviews, book list — a title shared with our books
       confirms the person) and up to three of the author's book pages for their Abjjad categories.
       Writes results.json (matched / not_found / ambiguous). The database is only read.
apply  for matched authors: subgenres and moods from the categories that recur across their books
       (only fields still empty are filled; researched values are kept), formats when empty, known works
       when empty, the Abjjad URL in source_urls and a note; needs_sources becomes thin, and a thin author
       whose identity a shared title confirmed and who now has a style becomes researched.
       Without --apply: dry run on a copy. Report: <out_dir>/apply_report.json.
"""
import html, json, os, re, sqlite3, sys, tempfile, time, urllib.parse
from collections import Counter
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import authors  # noqa: E402
from lookup_sources import get  # noqa: E402  (shared browser-like fetch with cookies and TLS)

BASE = "https://www.abjjad.com"
NOT_PERSONS = {authors.key(x) for x in ("مجموعة مؤلفين", "مؤلف مجهول", "Various", "غير معروف")}
# Abjjad category -> (our format, subgenre label, moods)
CAT = {
    "روايات رعب": ("novel", "رعب", ["dark", "tense"]), "روايات غموض": ("novel", "غموض", ["mysterious"]),
    "روايات إثارة وتشويق": ("novel", "تشويق", ["suspenseful"]), "روايات بوليسية": ("novel", "رواية بوليسية", ["suspenseful"]),
    "روايات جريمة": ("novel", "جريمة", ["suspenseful", "dark"]), "روايات جاسوسية": ("novel", "جاسوسية", ["suspenseful"]),
    "روايات رومانسية": ("novel", "رواية رومانسية", ["romantic"]), "روايات وقصص ساخرة": ("novel", "رواية ساخرة", ["funny"]),
    "أدب ساخر": ("other", "سخرية", ["funny"]), "روايات فلسفية": ("novel", "رواية فلسفية", ["contemplative"]),
    "روايات نفسية": ("novel", "رواية نفسية", ["contemplative"]), "روايات تاريخية": ("novel", "رواية تاريخية", []),
    "روايات خيال علمي": ("novel", "خيال علمي", []), "روايات فانتازيا": ("novel", "فانتازيا", []),
    # «روايات خيالية» is ignored: Abjjad attaches it to almost every novel (seen 2026-10-01)
    "روايات ديستوبيا": ("novel", "ديستوبيا", ["dark"]),
    "روايات اجتماعية": ("novel", "رواية اجتماعية", []), "روايات سياسية": ("novel", "رواية سياسية", []),
    "روايات الحرب": ("novel", "رواية حرب", ["sad"]), "روايات أدب السجون": ("novel", "أدب السجون", ["dark"]),
    "روايات مغامرات": ("novel", "مغامرات", ["suspenseful"]), "روايات لليافعين": ("young_adult", "أدب اليافعين", []),
    "روايات رمزية": ("novel", "رواية رمزية", []), "روايات واقعية": ("novel", "رواية واقعية", []),
    "روايات روحانية": ("novel", "رواية روحانية", ["inspiring"]), "قصص قصيرة": ("short_stories", "قصص قصيرة", []),
    "دواوين شعر": ("poetry", "شعر", []), "مسرحيات": ("play", "مسرحية", []), "نقد أدبي": ("thought", "نقد أدبي", []),
    "دراسات ومقالات أدبية": ("thought", "مقالات", []), "السير والمذكرات": ("biography", "سيرة", []),
    "سير ذاتية": ("biography", "سيرة ذاتية", []), "تطوير الذات": ("self_dev", "تطوير ذات", ["inspiring"]),
    "علم النفس": ("psychology", "علم نفس", []), "أدب الرحلات": ("travel", "أدب رحلات", []),
    "أدب الأطفال": ("children", "أدب أطفال", []), "التصوف والزهد": ("religious", "تصوف", ["contemplative"]),
}


def _a(text):
    return html.unescape(text or "").strip()


def search_authors(name):
    page = get(BASE + "/search?kw=" + urllib.parse.quote(name))
    found = {}
    for m in re.finditer(r'href="(/author/(\d+)/[^"]+)"[^>]*>([^<]+)<', page):
        found.setdefault(m.group(2), {"id": m.group(2), "url": BASE + m.group(1), "name": _a(m.group(3))})
    return list(found.values())


def author_page(url):
    page = get(url)
    rating = re.search(r'([0-9.]+)\s*</[^>]+>\s*<[^>]+>\s*معدل التقييمات', page) or re.search(r'>\s*([0-9]\.[0-9])\s*<', page)
    reviews = re.search(r'>\s*([0-9,]+)\s*<[^>]*>\s*مراجعة', page)
    books = []
    for m in re.finditer(r'href="(/book/\d+/[^"]+)"[^>]*>\s*([^<]{2,150}?)\s*<', page):
        t = _a(m.group(2))
        if t and t not in [b["title"] for b in books] and not t.startswith(("تحميل", "اشترك")):
            books.append({"url": BASE + m.group(1), "title": t})
    return {"rating": rating.group(1) if rating else None, "reviews": reviews.group(1) if reviews else None, "books": books[:20]}


def book_categories(url):
    page = get(url)
    i = page.find('book-category-genre')
    if i < 0:
        return []
    block = page[i:page.find('</div>\n</div>', i) + 1 if page.find('</div>\n</div>', i) > 0 else i + 3000]
    return [_a(x) for x in re.findall(r'class="tag[^"]*"[^>]*>([^<]+)<', block)]


def _titles_match(ours, theirs):
    a = [authors.key(t) for t in ours if authors.key(t)]
    b = [authors.key(t) for t in theirs if authors.key(t)]
    return any(x == y or (len(x) > 6 and (x in y or y in x)) for x in a for y in b)


def fetch(db, out_dir, statuses, limit, delay, ids=None, authors_only=False):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    rows = (con.execute(f"SELECT * FROM authors WHERE author_id IN ({','.join('?' * len(ids))})", ids).fetchall() if ids else
            con.execute(f"SELECT * FROM authors WHERE research_status IN ({','.join('?' * len(statuses))}) ORDER BY author_id", statuses).fetchall())
    titles = {}
    for r in con.execute("SELECT ba.author_id, b.title FROM book_authors ba JOIN books b USING(book_id)"):
        titles.setdefault(r[0], []).append(r[1])
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "results.json")
    out = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    done = {x["author_id"] for x in out}
    if authors_only:  # translators/illustrators have no reading style to find on Abjjad
        with_role = {x[0] for x in con.execute("SELECT DISTINCT author_id FROM book_authors WHERE role = 'author'")}
        rows = [r for r in rows if r["author_id"] in with_role]
    todo = [r for r in rows if r["author_id"] not in done and authors.key(r["name_ar"]) not in NOT_PERSONS
            and not authors._is_latin(r["name_ar"])][:limit or None]
    errors = 0
    for n, r in enumerate(todo, 1):
        try:
            names = [r["name_ar"]] + [a for a in authors._load(r["aliases"]) if not authors._is_latin(a)][:2]
            keys = {authors.key(x) for x in names}
            rec = {"author_id": r["author_id"], "name": r["name_ar"], "status": "not_found"}
            cands = []
            for nm in names:
                cands += [c for c in search_authors(nm) if authors.key(c["name"]) in keys and c["id"] not in [x["id"] for x in cands]]
                time.sleep(delay)
                if cands:
                    break
            checked = []
            for c in cands[:3]:
                info = author_page(c["url"]); time.sleep(delay)
                c.update(info, confirmed=_titles_match(titles.get(r["author_id"], []), [b["title"] for b in info["books"]]))
                checked.append(c)
            confirmed = [c for c in checked if c["confirmed"]]
            pick = confirmed[0] if len(confirmed) == 1 else (checked[0] if len(checked) == 1 else None)
            if pick:
                cats = []
                for b in pick["books"][:3]:
                    cats.append(book_categories(b["url"])); time.sleep(delay)
                rec.update(status="matched", url=pick["url"], abjjad_name=pick["name"], confirmed=pick["confirmed"],
                           rating=pick["rating"], reviews=pick["reviews"], books=[b["title"] for b in pick["books"]], categories=cats)
            elif checked:
                rec.update(status="ambiguous", candidates=[{k: c.get(k) for k in ("url", "name", "confirmed")} for c in checked])
            out.append(rec)
        except Exception as e:
            errors += 1
            print(f"error for {r['name_ar']}: {e}", flush=True)
            time.sleep(20)
        if n % 10 == 0 or n == len(todo):
            json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"{n}/{len(todo)} matched={sum(1 for x in out if x['status']=='matched')} errors={errors}", flush=True)
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: sum(1 for x in out if x["status"] == k) for k in ("matched", "ambiguous", "not_found")}))


def style_from(categories):
    """Categories that recur across the author's books (or all of a single book's) -> formats, subgenres, moods."""
    per_book = [set(c for c in cats if c in CAT) for cats in categories if cats]
    if not per_book:
        return [], [], []
    count = Counter(c for s in per_book for c in s)
    need = 2 if len(per_book) >= 2 else 1
    keep = [c for c, k in count.most_common() if k >= need][:5]
    formats = list(dict.fromkeys(CAT[c][0] for c in keep))[:3]
    subs = list(dict.fromkeys(CAT[c][1] for c in keep))[:4]
    moods = list(dict.fromkeys(m for c in keep for m in CAT[c][2]))[:3]
    return formats, subs, moods


def apply(db, results_path, do_apply):
    if not do_apply:
        tmp = os.path.join(tempfile.mkdtemp(), "copy.db")
        src, dst = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(tmp)
        src.backup(dst); src.close(); dst.close(); db = tmp
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = {"applied": do_apply, "updated": [], "skipped": []}
    for rec in json.load(open(results_path, encoding="utf-8")):
        if rec["status"] != "matched":
            continue
        if not rec.get("confirmed"):
            # a same-name author on Abjjad without one of our titles may be another person
            # (seen: محمد ناجي, محمد محمود الصياد) — nothing is written for it
            report["skipped"].append({"author_id": rec["author_id"], "why": "name match only, no shared title"}); continue
        row = con.execute("SELECT * FROM authors WHERE author_id = ?", (rec["author_id"],)).fetchone()
        if row is None or authors.find(con, rec["name"]) != rec["author_id"]:
            report["skipped"].append({"author_id": rec["author_id"], "why": "author record changed"}); continue
        formats, subs, moods = style_from(rec.get("categories") or [])
        L = lambda f: authors._load(row[f])
        new = {"formats": L("formats") or formats, "subgenres": L("subgenres") or subs, "moods": L("moods") or moods,
               "known_for": L("known_for") or rec.get("books", [])[:5]}
        urls = L("source_urls")
        if rec["url"] not in urls:
            urls.append(rec["url"])
        status = row["research_status"]
        if status in ("needs_sources", "pending"):
            status = "thin"
        if status == "thin" and rec.get("confirmed") and (new["subgenres"] or new["moods"]):
            status = "researched"
        note = (row["research_note"] or "").split(" | Abjjad")[0] + \
            f" | Abjjad: {'identity confirmed by a shared title' if rec.get('confirmed') else 'name match only'}; categories of {len([c for c in rec.get('categories') or [] if c])} books" + \
            (f"; rating {rec['rating']} ({rec['reviews']} reviews)" if rec.get("rating") else "")
        con.execute("""UPDATE authors SET formats=?, subgenres=?, moods=?, known_for=?, source_urls=?, research_note=?,
                       research_status=?, updated_at=? WHERE author_id=?""",
                    (json.dumps(new["formats"]), json.dumps(new["subgenres"], ensure_ascii=False), json.dumps(new["moods"]),
                     json.dumps(new["known_for"], ensure_ascii=False), json.dumps(urls, ensure_ascii=False), note, status, now, row["author_id"]))
        report["updated"].append({"author_id": row["author_id"], "name": row["name_ar"], "status": status, "subgenres": new["subgenres"],
                                  "moods": new["moods"], "confirmed": rec.get("confirmed")})
    con.commit(); con.close()
    json.dump(report, open(os.path.join(os.path.dirname(results_path), "apply_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"updated": len(report["updated"]), "skipped": len(report["skipped"]),
                      "now_researched": sum(1 for x in report["updated"] if x["status"] == "researched"), "applied": do_apply}))


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[4:]
    opt = lambda k, d: args[args.index(k) + 1] if k in args else d
    if cmd == "fetch":
        fetch(sys.argv[2], sys.argv[3], opt("--status", "thin,needs_sources").split(","), int(opt("--limit", 0)), float(opt("--delay", 2.0)),
              [int(x) for x in opt("--ids", "").split(",") if x], "--authors-only" in args)
    elif cmd == "apply":
        apply(sys.argv[2], sys.argv[3], "--apply" in args)
