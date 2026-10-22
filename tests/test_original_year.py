"""Original publication year of the work (ORIGYR, Ibrahim 2026-10-11): books.original_year with its
source and date; «صدر سنة …» from it in the chat, «طبعتنا سنة …» only for the edition; «كتب جديدة /
صدر حديثًا / بعد ٢٠٢٠» by it, falling back to the edition year; the admin card and staff editing;
the filling script's checks (same work, translation joined to its original, never after our edition)."""
import json, os, sqlite3, sys, tempfile, time
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm, chat, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
cols = {r["name"] for r in con.execute("PRAGMA table_info(books)")}
ok({"original_year", "original_year_source", "original_year_ref", "original_year_checked_at"} <= cols, "migration adds the four columns")

# ---- three available novels: a recent reprint of a classic, a recent work, a recent edition with no original year
ids = [r["book_id"] for r in con.execute("SELECT book_id FROM books WHERE active=1 AND quantity>0 AND genre='رواية' ORDER BY book_id LIMIT 3")]
ok(len(ids) == 3, "fixture has three available novels")
classic, recent, unknown = ids
yr = time.localtime().tm_year
con.execute("UPDATE books SET publication_year=NULL, original_year=NULL")
con.execute("UPDATE books SET publication_year=?, original_year=1899, original_year_source='goodreads', original_year_ref='https://www.goodreads.com/book/show/1', original_year_checked_at='2026-10-11 22:00' WHERE book_id=?", (yr - 1, classic))
con.execute("UPDATE books SET publication_year=?, original_year=?, original_year_source='wikidata' WHERE book_id=?", (yr - 1, yr - 2, recent))
con.execute("UPDATE books SET publication_year=?, original_year=NULL WHERE book_id=?", (yr - 1, unknown))
con.commit()
B = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}

f = A.book_fact(B[classic], {}, [])
ok(f.get("first_published") == 1899 and f.get("edition_year") == yr - 1 and "year" not in f,
   "facts: first_published 1899 and edition_year apart, no ambiguous «year»")
f = A.book_fact(B[unknown], {}, [])
ok("first_published" not in f and f.get("edition_year") == yr - 1, "no original year: only the edition year")
ok(A.year_for_new(B[classic]) == 1899 and A.year_for_new(B[unknown]) == yr - 1 and A.year_for_new(B[recent]) == yr - 2,
   "«new» goes by the original year, then the edition year")
ok(A.first_published({"original_year": "abc"}) is None and A.edition_year({"publication_year": "2019-05"}) == 2019, "bad values ignored")

# ---- the chat filter
seen = {}
def fake_respond(conv, facts_json, guidance, active, *a):
    seen["facts"] = json.loads(facts_json); seen["guidance"] = guidance
    return {"reply": "x", "show_book_ids": [x["id"] for x in seen["facts"]][:4], "suggestions": []}
chat.respond = fake_respond
chat.interpret = lambda conv, active, *a: {"intent": "recommend", "prefs": llm.clean_prefs({"genres": ["رواية"]}), "mentioned_titles": [],
                                          "wants_new_options": False, "references": [], "author_request": {}}
def say(*msgs):
    return A.run_chat([{"role": "user" if i % 2 == 0 else "assistant", "content": m} for i, m in enumerate(msgs)], active=("fake", "m"))
say("ابغى رواية صدرت حديثًا")
got = {x["id"] for x in seen["facts"]}
ok(classic not in got, "«صدر حديثًا»: a recent reprint of an 1899 classic is not new")
ok(recent in got and unknown in got, "«صدر حديثًا»: a recent work, and a recent edition with no original year (fallback), are")
ok("first_published" in seen["guidance"], "guidance names first_published / edition_year")
say("روايات بعد 2020")
ok(classic not in {x["id"] for x in seen["facts"]}, "«بعد ٢٠٢٠» by the original year")

# ---- reply rules
ok("first_published" in chat.RESPOND_SYSTEM_AR and "صدر سنة" in chat.RESPOND_SYSTEM_AR and "طبعتنا" in chat.RESPOND_SYSTEM_AR,
   "Arabic rules: «صدر سنة …» from first_published, «طبعتنا» for edition_year")
ok("first_published" in chat.RESPOND_SYSTEM_EN and "edition_year" in chat.RESPOND_SYSTEM_EN, "English rules name both years")
ok("سنة نشر الطبعة هي year" not in chat.RESPOND_SYSTEM_AR, "the old «year» rule is gone")

# ---- staff editing and the admin card
c = login(A.app.test_client())
r = c.patch(f"/api/books/{unknown}", json={"original_year": 1950})
row = con.execute("SELECT original_year, original_year_source, original_year_checked_at FROM books WHERE book_id=?", (unknown,)).fetchone()
ok(r.status_code == 200 and row["original_year"] == 1950 and row["original_year_source"] == "staff" and row["original_year_checked_at"],
   "staff set the original year: source «staff», dated")
ok(c.patch(f"/api/books/{unknown}", json={"original_year": 3000}).status_code == 400, "a future year is refused")
r = c.patch(f"/api/books/{unknown}", json={"original_year": None})
row = con.execute("SELECT original_year, original_year_source FROM books WHERE book_id=?", (unknown,)).fetchone()
ok(row["original_year"] is None and row["original_year_source"] is None, "staff can clear it")
card = c.get(f"/api/admin/books/{classic}").get_json()
ok(card["book"]["original_year"] == 1899 and card["book"]["original_year_source"] == "goodreads", "admin card carries the year and its source")
lib = c.get("/api/admin/catalog").get_json()
ok(any(x.get("original_year") == 1899 for x in (lib if isinstance(lib, list) else [])), "admin library rows carry original_year")
html = open(os.path.join("..", "admin", "index.html"), encoding="utf-8").read()
ok("سنة الصدور الأصلية" in html and 'id="bOrigYear"' in html and "origYearText" in html and "سنة طبعتنا" in html,
   "admin card: «سنة الصدور الأصلية» shown and editable, «سنة طبعتنا» beside it")

# ---- the filling script
sys.path.insert(0, os.path.join("..", "scripts"))
import fill_original_year as S
tmp = tempfile.mkdtemp()
S.OUT, S.GR_DIR, S.WD_FILE, S.OL_FILE = tmp, os.path.join(tmp, "gr"), os.path.join(tmp, "wd.jsonl"), os.path.join(tmp, "ol.jsonl")
os.makedirs(S.GR_DIR)
ok(S.same_author(["هربرت جورج ويلز", "H. G. Wells"], "H.G. Wells") and not S.same_author(["بول أوستر"], "Paul Auster")
   and S.same_author(["بول أوستر", "Paul Auster"], ["Paul Auster"]), "author check: through the Latin name, never a guess")
page = ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"apolloState": {
    "Book:1": {"title": "النائم يستيقظ", "webUrl": "https://www.goodreads.com/book/show/9", "details": {"publicationTime": 1735689600000},
               "primaryContributorEdge": {"node": {"__ref": "Contributor:1"}}},
    "Contributor:1": {"name": "H.G. Wells"},
    "Work:1": {"details": {"publicationTime": -2240524800000, "originalTitle": "When the Sleeper Wakes"}}}}}}) + "</script>")
g = S.parse_gr(page)
ok(g["work_year"] == 1899 and g["original_title"] == "When the Sleeper Wakes" and g["author"] == "H.G. Wells" and g["edition_year"] == 2025,
   "Goodreads page: Work.details.publicationTime (1899), original title, author")
book = {"book_id": 1, "title": "النائم يستيقظ", "author": "هربرت جورج ويلز", "publication_year": 2025, "translator": "شهرت العالم",
        "original_language": "en", "language": "ar"}
names = ["هربرت جورج ويلز", "H. G. Wells"]
gr = {1: dict(g, found_by="search", fetched_at="2026-10-11 22:00", url="u")}
ok(S.choose(book, names, gr, {}, {})[:2] == (1899, "goodreads"), "translated book, Goodreads work joined to the original: 1899")
gr2 = {1: dict(gr[1], original_title="النائم يستيقظ", work_year=2025)}
ok(S.choose(book, names, gr2, {}, {})[0] is None, "translated book, Goodreads work not joined to an original: refused")
wd = {1: {"hit": {"year": 1899, "ref": "https://www.wikidata.org/wiki/Q1"}, "checked_at": "x"}}
ok(S.choose(book, names, gr2, {}, wd)[:2] == (1899, "wikidata"), "then Wikidata")
ol = {1: {"hit": {"year": 2026, "ref": "r"}, "checked_at": "x"}}
y, src, *_rest = S.choose(book, names, {}, ol, {})
ok(y is None and "after our edition" in _rest[-1], "a year after our edition is refused")
ok(S.choose(dict(book, translator=None, original_language="ar"), ["هربرت جورج ويلز"], {1: dict(g, found_by="search", fetched_at="t", url="u", author="Someone Else")}, {}, {})[0] is None,
   "Goodreads page found by title search with another author: refused")

ol2 = {1: {"hit": {"year": 1850, "ref": "ol"}, "checked_at": "x"}}
wd2 = {1: {"hit": {"year": 1853, "ref": "wd"}, "checked_at": "x"}}
r = S.choose(dict(book, translator=None, original_language="en", language="en"), names, {}, ol2, wd2)
ok(r[:2] == (1853, "wikidata") and "disagrees" in r[-1], "Open Library and Wikidata disagree: Wikidata's date, noted")
ok(S.choose(dict(book, translator=None, original_language="en", language="en"), names, {}, ol2, {})[:2] == (1850, "openlibrary"),
   "Open Library only when no other source has the work")

ok(S.choose(dict(book, original_language="sq"), ["إسماعيل كاداريه"], {}, {1: {"hit": {"year": 2004, "ref": "ol"}, "checked_at": "x"}}, {})[0] is None,
   "Open Library is not used for a work first written in another language than English")

# apply on a copy of the database: writes source + date, never over a staff year
dbcopy = os.path.join(tmp, "copy.db")
src_db = sqlite3.connect(models.DB_PATH); dst = sqlite3.connect(dbcopy); src_db.backup(dst); dst.close(); src_db.close()
d2 = sqlite3.connect(dbcopy)
d2.execute("UPDATE books SET research_status='matched', original_year=NULL, original_year_source=NULL")
d2.execute("UPDATE books SET original_year=1777, original_year_source='staff' WHERE book_id=?", (recent,))
d2.commit(); d2.close()
json.dump({"book_id": unknown, "work_year": 1901, "author": B[unknown]["author"], "found_by": "isbn", "fetched_at": "2026-10-11 22:05",
           "final_url": "https://www.goodreads.com/book/show/5", "original_title": None}, open(os.path.join(S.GR_DIR, f"{unknown}.json"), "w"))
json.dump({"book_id": recent, "work_year": 1901, "author": B[recent]["author"], "found_by": "isbn", "fetched_at": "t", "url": "u"},
          open(os.path.join(S.GR_DIR, f"{recent}.json"), "w"))
class Args: apply = True; db = dbcopy
_real_db = models.DB_PATH
S.apply(Args())
models.DB_PATH = _real_db
d2 = sqlite3.connect(dbcopy); d2.row_factory = sqlite3.Row
u = d2.execute("SELECT original_year, original_year_source, original_year_ref, original_year_checked_at, translator, original_language, language FROM books WHERE book_id=?", (unknown,)).fetchone()
t = d2.execute("SELECT original_year, original_year_source FROM books WHERE book_id=?", (recent,)).fetchone()
expect_unknown = 1901 if not (u["translator"] or (u["original_language"] or "ar") not in ("", u["language"] or "ar")) else None
ok(u["original_year"] == expect_unknown and (expect_unknown is None or (u["original_year_source"] == "goodreads" and u["original_year_ref"].endswith("/5") and u["original_year_checked_at"])),
   f"apply writes year, source, page and date ({dict(u)})")
ok(t["original_year"] == 1777 and t["original_year_source"] == "staff", "apply never overwrites a staff year")

# --only-missing (MGR 2026-10-15): fills books with no year, never changes a year already set
d2.execute("UPDATE books SET original_year=1850, original_year_source='wikidata' WHERE book_id=?", (unknown,))
d2.execute("UPDATE books SET original_year=NULL, original_year_source=NULL WHERE book_id=?", (classic,)); d2.commit(); d2.close()
json.dump({"book_id": classic, "work_year": 1902, "author": B[classic]["author"], "found_by": "isbn", "fetched_at": "2026-10-15 22:40",
           "final_url": "https://www.goodreads.com/book/show/7", "original_title": None}, open(os.path.join(S.GR_DIR, f"{classic}.json"), "w"))
class Args2: apply = True; db = dbcopy; only_missing = True
S.apply(Args2())
models.DB_PATH = _real_db
d2 = sqlite3.connect(dbcopy); d2.row_factory = sqlite3.Row
u2 = d2.execute("SELECT original_year, original_year_source FROM books WHERE book_id=?", (unknown,)).fetchone()
t2 = d2.execute("SELECT original_year, original_year_source FROM books WHERE book_id=?", (recent,)).fetchone()
ok(u2["original_year"] == 1850 and u2["original_year_source"] == "wikidata", "--only-missing keeps a year already set")
c2 = d2.execute("SELECT original_year, original_year_source, translator, original_language, language FROM books WHERE book_id=?", (classic,)).fetchone()
expect_classic = 1902 if not (c2["translator"] or (c2["original_language"] or "ar") not in ("", c2["language"] or "ar")) else None
ok(c2["original_year"] == expect_classic and (expect_classic is None or c2["original_year_source"] == "goodreads"),
   f"--only-missing still fills a book with no year (AUDFIX 2026-10-15) ({dict(c2)})")
ok(t2["original_year"] == 1777 and t2["original_year_source"] == "staff", "--only-missing never overwrites a staff year")
print(f"TOTAL PASS={P} FAIL={F}")
sys.exit(1 if F else 0)
