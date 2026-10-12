"""Books we do not have, looked up outside and kept for next time (2026-10-11). Network is faked."""
import os, sys, sqlite3, time
for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LIBRARY_KEEPER_PROVIDER", "LIBRARY_KEEPER_MODEL", "LIBRARY_KEEPER_LLM"]:
    os.environ.pop(k, None)
sys.path.insert(0, os.getcwd())
import models
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
con = sqlite3.connect(models.DB_PATH)
ok("outside_books" in {r[0] for r in con.execute("select name from sqlite_master where type='table'")}, "migration creates outside_books")
import outside_books as O

# ---------- names
ok(O.key_of("اخلع حذائك") == O.key_of("اخلع حذاءك") == O.key_of("كتاب اخلع حذاءك"), "«حذائك» / «حذاءك» / «كتاب …» are one key")
ok("اخلع حذاءك" in O.spellings("اخلع حذائك"), "spellings try ء for ئ")
ok(O.same_title("ساق البامبو", "ساق البامبو") and O.same_title("الأب الغني والأب الفقير", "الأب الغني والأب الفقير: ما يعلمه الأغنياء")
   and not O.same_title("الأرض", "الأرض الطيبة"), "same title: equal, or with a subtitle; a short title never by prefix")

# ---------- off in the suites
os.environ["LIBRARY_KEEPER_OUTSIDE"] = "0"
ok(O.lookup("اخلع حذائك") is None, "LIBRARY_KEEPER_OUTSIDE=0: no lookup")
os.environ["LIBRARY_KEEPER_OUTSIDE"] = "1"

# ---------- fake sources
calls = {"wd": 0, "ol": 0, "gr": 0}
def fake_wd(title, timeout=4.0):
    calls["wd"] += 1
    if O.same_title("ساق البامبو", title):
        return {"title": "ساق البامبو", "author": "سعود السنعوسي", "year": 2012, "genres": ["رواية"], "subjects": ["الهوية"],
                "isbn": None, "goodreads_id": "13637412", "wikidata": "Q1", "wiki": None, "kind": "رواية", "sources": ["https://www.wikidata.org/wiki/Q1"]}
    return None
def fake_ol(title, timeout=4.0):
    calls["ol"] += 1
    if O.same_title("اخلع حذاءك", title) or O.same_title("اخلع حذائك", title):
        return {"title": "إخلع حذاءك", "author": "Yāsir Ḥārib", "year": 2015, "isbn": "9789948466390", "subjects": [],
                "publisher": None, "sources": ["https://openlibrary.org/works/OL1W"]}
    return None
def fake_gr(goodreads_id=None, isbn=None, title=None, timeout=12.0):
    calls["gr"] += 1
    if isbn == "9789948466390":
        return {"title": "اخلع حذاءك", "author": "ياسر حارب", "description": "قصص قصيرة وتأملات عن الأمل والسعادة ومراجعة النفس",
                "genres": ["Nonfiction", "Short Stories", "Self Help"], "rating": 3.7, "ratings_count": 5101,
                "reviews": [{"rating": 4, "text": "كتاب جميل يعلمك أن السعادة في الرحلة نفسها وليست في الوصول إليها أبدا"}],
                "sources": ["https://www.goodreads.com/book/show/24500966"], "goodreads_id": "24500966"}
    raise RuntimeError("goodreads challenge")
O.from_wikidata, O.from_openlibrary, O.goodreads_page = fake_wd, fake_ol, fake_gr
O.wiki_summary = lambda lang, page, timeout=4.0: (None, None)

r = O.lookup("اخلع حذائك", background=False)
ok(r and r["status"] == "found" and r["author"] == "Yāsir Ḥārib" and r["isbn"] == "9789948466390", "found in Open Library by another spelling")
ok(O.enrich(O.key_of("اخلع حذائك")), "Goodreads page by ISBN read after the answer")
r = O.get("اخلع حذاءك")
ok(r["rating"] == 3.7 and r["ratings_count"] == 5101 and r["reviews"] and "Self Help" in r["genres"] and r["description"],
   "stored: rating, count, genres, description, a reader review")
ok(r["author"] == "Yāsir Ḥārib", "the author found first is kept (Goodreads does not overwrite it)")
before = dict(calls)
again = O.lookup("كتاب اخلع حذاءك", background=False)
ok(again and calls == before and again["times_asked"] >= 2, "next time: from storage, no outside call, counted")

r = O.lookup("ساق البامبو", background=False)
ok(r and r["author"] == "سعود السنعوسي" and r["wikidata"] == "Q1", "found in Wikidata")
ok(O.enrich(O.key_of("ساق البامبو")) is False and not O.get("ساق البامبو").get("enriched_at"),
   "a Goodreads bot check is not «no such book»: tried again on a later visit")

ok(O.lookup("كتاب لا وجود له ابدا", background=False) is None, "not found anywhere -> None")
row = O.get("كتاب لا وجود له ابدا")
ok(row and row["status"] == "not_found", "… and remembered as not found")
n = calls["wd"]
O.lookup("كتاب لا وجود له ابدا", background=False)
ok(calls["wd"] == n, "not asked again within a week")

b = O.brief(O.get("اخلع حذائك"))
ok(b["author"] and "Goodreads" in b["sources"] and b["in_our_stock"] is False and b["reader_reviews"], "brief for the chat: author, sources, not our stock")
t = O.text_for_meaning(O.get("اخلع حذائك"))
ok(t and "حذاء" not in t and "السعادة" in t, "meaning text: genres and summary, without the title's own words")


# a first lookup that knows only the title, author and year waits a little for Goodreads, so the visitor's
# answer already knows what the book is about
O.from_openlibrary = lambda title, timeout=4.0: ({"title": "سماء قريبة", "author": "ياسر", "year": 2016, "isbn": "111",
                                                  "subjects": [], "sources": ["https://openlibrary.org/works/OL2W"]}
                                                 if O.same_title("سماء قريبة", title) else None)
O.goodreads_page = lambda goodreads_id=None, isbn=None, title=None, timeout=12.0: (
    {"title": "سماء قريبة", "description": "تأملات في الحياة والسعادة والسفر والناس الذين نلتقيهم", "genres": ["Nonfiction"],
     "sources": ["https://www.goodreads.com/book/show/9"], "goodreads_id": "9"} if isbn == "111" else None)
O._down.clear()   # the bot check above put Goodreads aside for 15 minutes
r = O.lookup("سماء قريبة")
ok(r and r.get("description") and O.text_for_meaning(r), "first lookup with only basic data waits for Goodreads' summary")

# Wikidata busy (429) and Open Library silent: Goodreads' «book by title» page, only when it is the same title
def wd_busy(title, timeout=4.0):
    raise RuntimeError("HTTP Error 429: Too Many Requests")
O.from_wikidata, O.from_openlibrary = wd_busy, (lambda title, timeout=4.0: None)
O.goodreads_page = lambda goodreads_id=None, isbn=None, title=None, timeout=12.0: (
    {"title": "عزازيل", "author": "يوسف زيدان", "description": "راهب في القرن الخامس بين مصر وسوريا وصراع الإيمان والشك",
     "genres": ["Historical Fiction"], "rating": 3.9, "sources": ["https://www.goodreads.com/book/show/5"], "goodreads_id": "5"}
    if title == "عزازيل" else {"title": "كتاب آخر تماما", "author": "أحد"})
r = O.lookup("عزازيل")
ok(r and r["author"] == "يوسف زيدان" and r["blurb"] and r["enriched_at"], "Goodreads by title when the other sources have nothing")
O._down.clear()
ok(O.lookup("عنوان مختلف") is None, "… and a Goodreads page of another title is not taken")

# the summary's sentences about its publisher, prize or translation say nothing about what the book is about
t = O.text_for_meaning({"title": "عزازيل", "genres": ["خيال تاريخي"], "description":
    "عزازيل هي رواية من تأليف يوسف زيدان، صدرت عن دار الشروق سنة 2008. تدور أحداثها في القرن الخامس بين مصر وسوريا وصراع الكنيسة."})
ok(t and "الشروق" not in t and "القرن الخامس" in t, "meaning text leaves out the publishing history")

print(f"\nTOTAL pass={P} fail={F}")
sys.exit(1 if F else 0)
