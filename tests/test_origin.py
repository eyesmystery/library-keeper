"""Language of the work: Arabic original / translated / a book in another language."""
import os, sys, json, sqlite3, subprocess, tempfile, shutil
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
cols = {r[1] for r in sqlite3.connect(models.DB_PATH).execute("PRAGMA table_info(books)")}
ok({"original_language", "translator"} <= cols, "migration adds original_language and translator")
L = A.origin_label
ok(L({"language": "ar", "original_language": "ar"}) == "عمل عربي أصلي", "Arabic original")
ok(L({"language": "ar", "original_language": "ru", "translator": "سامي الدروبي"}) == "مترجم عن الروسية، ترجمة سامي الدروبي", "translated with translator")
ok(L({"language": "ar", "original_language": "en"}) == "مترجم عن الإنجليزية", "translated, translator unknown")
ok(L({"language": "en", "original_language": "en"}).startswith("كتاب باللغة الإنجليزية"), "a book in another language")
ok(L({"language": "ar", "original_language": None}) is None and L({}) is None, "unknown is never guessed")

c = login(A.app.test_client())
bid = c.get("/api/books").get_json()[0]["book_id"]
r = c.patch(f"/api/books/{bid}", json={"original_language": "fr", "translator": "مترجم تجريبي"})
ok(r.status_code == 200, "staff can set original language and translator")
b = next(x for x in c.get("/api/books").get_json() if x["book_id"] == bid)
ok(b["origin"] == "مترجم عن الفرنسية، ترجمة مترجم تجريبي", "origin label in /api/books")
d = c.get(f"/api/admin/books/{bid}").get_json()
ok(d["book"]["origin"] == b["origin"] and any(l["value"] == "fr" for l in d["languages"]), "admin card shows origin and language options")
ok(c.patch(f"/api/books/{bid}", json={"original_language": "xx"}).status_code == 400, "unknown language code rejected")
ok(c.patch(f"/api/books/{bid}", json={"original_language": "", "translator": ""}).status_code == 200
   and next(x for x in c.get("/api/books").get_json() if x["book_id"] == bid)["origin"] is None, "cleared back to unknown")
c.patch(f"/api/books/{bid}", json={"language": "en"})
ok(next(x for x in c.get("/api/books").get_json() if x["book_id"] == bid)["origin"].startswith("كتاب باللغة"), "English copy shown as another-language book")
c.patch(f"/api/books/{bid}", json={"language": "ar", "original_language": "ru"})
cards = models.get_profile_cards()
book = dict(models.get_book(bid))
ok(A.book_fact(book, cards.get(bid), "x")["origin"] == "مترجم عن الروسية", "chat facts carry origin for the model")
r = c.post("/api/recommend", json={"query": "رواية"}).get_json()
ok(all("origin" in x for x in r["results"]), "recommend results carry origin")
r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "رواية"}]}).get_json()
ok(all("origin" in x for x in r["books"]), "chat books carry origin")

# importer: language fields from records, name normalization, backfill without new books
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "work.db"); shutil.copy(models.DB_PATH, db)
seed = os.path.join(tmp, "seed.json"); shutil.copy("../data/seed_books.json", seed)
os.makedirs(os.path.join(tmp, "recs", "new"))
title_known = sqlite3.connect(db).execute("select title from books where original_language is null limit 1").fetchone()[0]
rows = [{"name": "moods", "value": "warm", "confidence": 0.8, "review_count": 3, "conflict": False, "source_site": "goodreads",
         "edition_scope": "original_or_any", "summary": "دافئ", "urls": ["https://example.org/a"]},
        {"name": "pace", "value": "medium", "confidence": 0.8, "review_count": 3, "conflict": False, "source_site": "goodreads",
         "edition_scope": "original_or_any", "summary": "متوسط", "urls": ["https://example.org/a"]},
        {"name": "emotional_weight", "value": "light", "confidence": 0.8, "review_count": 3, "conflict": False,
         "source_site": "abjjad", "edition_scope": "arabic_edition", "summary": "خفيف", "urls": ["https://example.org/b"]}]
recs = [{"key": "1-1", "category": "x", "title_ar": "كتاب اختبار مترجم جديد", "author_ar": "مؤلف", "original_title": "Test Book",
         "original_language": "English", "edition_language": "ar", "translator_ar": "مترجم", "catalog_genre": "رواية", "rows": rows},
        {"key": "1-2", "category": "x", "title_ar": title_known, "author_ar": "مؤلف", "original_language": "Russian",
         "translator_ar": "م. ر", "catalog_genre": "رواية", "rows": rows}]
for r_ in recs:
    json.dump(r_, open(os.path.join(tmp, "recs", "new", r_["key"] + ".json"), "w"), ensure_ascii=False)
script = os.path.join(os.path.dirname(os.getcwd()), "scripts", "import_enrichment.py")
subprocess.run([sys.executable, script, db, seed, os.path.join(tmp, "recs"), "--backfill-language", "--no-new", "--apply"],
               check=True, capture_output=True)
con = sqlite3.connect(db)
ok(con.execute("select original_language, translator from books where title=?", (title_known,)).fetchone() == ("ru", "م. ر"),
   "backfill fills an existing book (name 'Russian' -> ru)")
ok(con.execute("select count(*) from books where title='كتاب اختبار مترجم جديد'").fetchone()[0] == 0, "--no-new inserts nothing")
subprocess.run([sys.executable, script, db, seed, os.path.join(tmp, "recs"), "--apply"], check=True, capture_output=True)
ok(con.execute("select language, original_language, translator from books where title='كتاب اختبار مترجم جديد'").fetchone()
   == ("ar", "en", "مترجم"), "new book imported with edition/original language and translator")
ok(any(b_.get("original_language") == "en" for b_ in json.load(open(seed)) if b_["title"] == "كتاب اختبار مترجم جديد"),
   "seed file carries the language fields")

# ---------- visitor wish: original language / translated (2026-10-03, «مترجم من الإيطالية»)
import llm, keyword_prefs
OA = A.origin_allowed
def wish(**kw): return A.prefs_from_ai(llm.clean_prefs(kw), "")
pr = wish(original_languages=["it"], translated="yes")[0]
ok(OA({"language": "ar", "original_language": "it"}, pr), "translated from Italian satisfies the wish")
ok(not OA({"language": "ar", "original_language": "en"}, pr) and not OA({"language": "ar", "original_language": None, "translator": "م"}, pr),
   "another or unknown original language never satisfies a language wish")
ok(not OA({"language": "it", "original_language": "it"}, pr), "an Italian-language copy is not a translation")
ok(OA({"language": "it", "original_language": None}, wish(original_languages=["it"])[0]), "a copy in the language itself counts when 'translated' was not asked")
pr = wish(translated="yes")[0]
ok(OA({"language": "ar", "original_language": "ru"}, pr) and OA({"language": "ar", "translator": "م"}, pr)
   and not OA({"language": "ar", "original_language": "ar"}, pr) and not OA({"language": "ar"}, pr), "translated=yes: known translations only")
pr = wish(translated="no")[0]
ok(OA({"language": "ar", "original_language": "ar"}, pr) and not OA({"language": "ar", "original_language": "fr"}, pr)
   and not OA({"language": "ar"}, pr), "translated=no: known Arabic originals only")
ok(OA({"language": "ar"}, wish()[0]), "no wish: nothing filtered")
ok(wish(original_languages=["ja"], genres=["أدب"])[0]["genres"] == set() and wish(genres=["أدب"])[0]["genres"] == {"أدب"}
   and wish(original_languages=["ja"], genres=["رواية"])[0]["genres"] == {"رواية"}, "«أدب ياباني» does not become the genre «أدب»")
ok(llm.clean_prefs({"original_languages": ["it", "xx"], "translated": "maybe"})["original_languages"] == ["it"]
   and llm.clean_prefs({"translated": "maybe"})["translated"] is None, "unknown language codes and values are dropped")
ok("original_languages" in llm.PREFS_SCHEMA["required"] and "translated" in llm.PREFS_SCHEMA["required"]
   and "original_languages" in llm.PREFS_RULES, "interpretation schema and rules carry the two fields")
con2 = sqlite3.connect(models.DB_PATH)
ids = [r_[0] for r_ in con2.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 3")]
con2.execute("update books set language='ar', original_language='ar'")
con2.execute(f"update books set original_language='it' where book_id in ({','.join(map(str, ids))})")
con2.commit()
allb = [dict(b_) for b_ in models.list_books(only_active=True)]
prefs_, pp_, ex_, _u = wish(original_languages=["it"], translated="yes")
got, labels = A.constrain(allb, prefs_, pp_, ex_, models.get_profile_cards())
ok({b_["book_id"] for b_ in got} == set(ids) and "مترجم عن الإيطالية" in labels, "constrain keeps only books translated from Italian and names the wish")
ok(A.localize_reason("مترجم عن الإيطالية") == "translated from Italian" and A.localize_reason("عربي غير مترجم") == "originally Arabic", "English wording of the wish")
k = keyword_prefs.understand(["ابغى رواية ايطالية مترجمة"])
ok(k["original_languages"] == ["it"] and k["translated"] == "yes", "keyword fallback reads the language and 'translated'")
ok(keyword_prefs.understand(["كتاب عن ايطاليا"])["original_languages"] == [], "a country as a topic is not a language")
# audit 2026-10-05: Arabic and English wording in the keyword fallback; edition language is not the origin
ku = lambda t: keyword_prefs.understand([t])
ok(ku("ابغى رواية عربية")["original_languages"] == ["ar"] and ku("رواية عربية غير مترجمة")["translated"] == "no", "«رواية عربية» is read as written in Arabic")
ok(ku("Italian novels")["original_languages"] == ["it"] and ku("a translated Russian novel")["translated"] == "yes", "English adjectives are read too")
ok(ku("رواية روسية باللغة العربية")["original_languages"] == ["ru"] and ku("رواية يابانية مترجمة للعربية")["original_languages"] == ["ja"]
   and ku("a novel in english")["original_languages"] == [], "the language of the copy is not taken for the origin")
ok(not OA({"language": "en", "original_language": "fr"}, wish(original_languages=["en"])[0])
   and OA({"language": "en", "original_language": "en"}, wish(original_languages=["en"])[0])
   and OA({"language": "ar", "original_language": "en"}, wish(original_languages=["en"])[0]), "an English edition of a French work is not originally English")
pr_no = wish(original_languages=["it"], translated="no")[0]
ok(not OA({"language": "ar", "original_language": "it"}, pr_no) and OA({"language": "it", "original_language": "it"}, pr_no)
   and not OA({"language": "it", "original_language": None, "translator": "م"}, pr_no), "a language with 'not translated': only copies in that language")
r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "أبغى كتاب مترجم من الإيطالية"}]}).get_json()
ok(r["books"] and all(x["book_id"] in ids for x in r["books"]), "chat (keyword mode): only books translated from Italian are shown")
r = c.post("/api/recommend", json={"query": "أبغى كتاب مترجم من الإيطالية"}).get_json()
ok(r["results"] and all(x["book_id"] in ids for x in r["results"]), "recommend: only books translated from Italian")

# ---------- a named subgenre that leaves nothing is relaxed, never the other constraints (2026-10-03)
prefs_, pp_, ex_, _u = wish(original_languages=["it"], subgenres=["تصنيف غير موجود إطلاقًا"])
got, labels, used, dropped = A.constrain_relaxing(allb, prefs_, pp_, ex_, models.get_profile_cards())
ok({b_["book_id"] for b_ in got} == set(ids) and dropped == ["تصنيف غير موجود إطلاقًا"] and used["subgenres"] == []
   and "تصنيف غير موجود إطلاقًا" not in labels, "missing subgenre is dropped; the language wish is kept")
prefs_, pp_, ex_, _u = wish(original_languages=["hu"], subgenres=["تصنيف غير موجود إطلاقًا"])
got, labels, used, dropped = A.constrain_relaxing(allb, prefs_, pp_, ex_, models.get_profile_cards())
ok(got == [] and dropped == [], "nothing is relaxed when the other constraints leave no book either")
prefs_, pp_, ex_, _u = wish(original_languages=["it"])
got, labels, used, dropped = A.constrain_relaxing(allb, prefs_, pp_, ex_, models.get_profile_cards())
ok(dropped == [] and len(got) == len(ids), "no subgenre wish: nothing relaxed")

# ---------- mood-like subgenres: same result whichever field the model used (2026-10-04)
a_ = wish(subgenres=["رومانسية"]); b_ = wish(moods_include=["romantic"]); c_ = wish(subgenres=["رواية رومانسية"], moods_include=["romantic"])
ok(all(x[0]["subgenres"] == [] and x[1]["moods"] == {"romantic"} and "رومانسية" in x[0]["soft_subgenres"] for x in (a_, b_, c_)),
   "«رومانسية» as subgenre, as mood, or both gives the same preferences")
ok(wish(subgenres=["رعب", "غموض"])[0]["subgenres"] == ["رعب"] and wish(subgenres=["رعب", "غموض"])[1]["moods"] == {"mysterious"}
   and wish(subgenres=["رواية تاريخية"])[0]["subgenres"] == ["رواية تاريخية"], "real subgenres stay hard constraints")
ok(wish(subgenres=["رومانسية"], moods_exclude=["romantic"])[1]["moods"] == set(), "an excluded mood is not re-added")
got, labels = A.constrain(allb, a_[0], a_[1], a_[2], models.get_profile_cards())
base_, _l = A.constrain(allb, wish()[0], wish()[1], wish()[2], models.get_profile_cards())
ok(len(got) == len(base_) > 0, "«رومانسية» no longer excludes any book")
bk = dict(allb[0], subgenres='["رواية رومانسية"]'); plain = dict(allb[1], subgenres='[]')
ok(A.score_book(bk, a_[0])[0] >= A.score_book(plain, a_[0])[0] + 6 and A.score_book(bk, b_[0])[0] == A.score_book(bk, a_[0])[0],
   "a book whose subgenre names the mood ranks higher, the same for both wordings")
print(f"TOTAL PASS={P} FAIL={F}")
