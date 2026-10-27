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
from datetime import datetime, timezone

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
    if title and not goodreads_id and not isbn:
        if O.same_title("الأرض بعد طوفان نوح", title):
            return {"title": "الأرض بعد طوفان نوح: سيرة أبناء نوح", "author": "شريف سامي", "description": "عن أبناء نوح بعد الطوفان.",
                    "sources": ["https://www.goodreads.com/book/show/60290879"], "goodreads_id": "60290879"}
        return None   # Goodreads has no page for this title
    raise RuntimeError("goodreads challenge")
O.from_wikidata, O.from_openlibrary, O.goodreads_page = fake_wd, fake_ol, fake_gr
O.wiki_summary = lambda lang, page, timeout=4.0: (None, None)

r = O.lookup("اخلع حذائك", background=False)
ok(r and r["status"] == "found" and r["author"] == "Yāsir Ḥārib" and r["isbn"] == "9789948466390", "found in Open Library by another spelling")
ok(O.enrich(O.key_of("اخلع حذائك")), "Goodreads page by ISBN read after the answer")
r = O.get("اخلع حذاءك")
ok(r["rating"] == 3.7 and r["ratings_count"] == 5101 and "Self Help" in r["genres"] and r["description"],
   "stored: rating, count, genres, description")
# reader-review text is never stored, even when a source hands it over (AGENTS.md; audit 2026-10-12 #6)
REVIEW = "كتاب جميل يعلمك أن السعادة في الرحلة"
raw = con.execute("SELECT * FROM outside_books WHERE key=?", (O.key_of("اخلع حذائك"),)).fetchone()
ok(r["reviews"] == [] and REVIEW not in " ".join(str(x) for x in raw),
   "no reader-review text stored: the reviews column stays [] and the text is nowhere in the row")
ok(r["author"] == "Yāsir Ḥārib", "the author found first is kept (Goodreads does not overwrite it)")
before = dict(calls)
again = O.lookup("كتاب اخلع حذاءك", background=False)
ok(again and calls == before and again["times_asked"] >= 2, "next time: from storage, no outside call, counted")

r = O.lookup("ساق البامبو", background=False)
ok(r and r["author"] == "سعود السنعوسي" and r["wikidata"] == "Q1", "found in Wikidata")
ok(O.enrich(O.key_of("ساق البامبو")) is False and not O.get("ساق البامبو").get("enriched_at"),
   "a Goodreads bot check is not «no such book»: tried again on a later visit")

# VREVFIX (2026-10-27): a lookup in which Goodreads was not asked (resting after the bot check above) is not remembered as not found
ok(O.lookup("كتاب لا وجود له ابدا", background=False) is None and O.get("كتاب لا وجود له ابدا") is None,
   "Goodreads not asked: nothing remembered, the next visitor's lookup tries again")
O._down.clear()
ok(O.lookup("كتاب لا وجود له ابدا", background=False) is None, "not found anywhere -> None")
row = O.get("كتاب لا وجود له ابدا")
ok(row and row["status"] == "not_found", "… and remembered as not found")
n = calls["wd"]
O.lookup("كتاب لا وجود له ابدا", background=False)
ok(calls["wd"] == n, "not asked again within a week")

b = O.brief(O.get("اخلع حذائك"))
ok(b["author"] and "Goodreads" in b["sources"] and b["in_our_stock"] is False, "brief for the chat: author, sources, not our stock")
import json as _json
ok("reader_reviews" not in b and "reviews" not in b and REVIEW not in _json.dumps(b, ensure_ascii=False),
   "brief passes no reader-review text to the chat (even from an old row that still held some)")
# an old row written before 2026-10-12 with review text: the brief ignores it, and the next save empties it
con.execute("UPDATE outside_books SET reviews=? WHERE key=?",
            (_json.dumps([{"rating": 4, "text": REVIEW}], ensure_ascii=False), O.key_of("اخلع حذائك")))
con.commit()
old = O.get("اخلع حذائك")
ok(REVIEW not in _json.dumps(O.brief(old), ensure_ascii=False), "brief of an old row with review text: no review text")
O._save(O.key_of("اخلع حذائك"), "اخلع حذائك", {"rating": 3.8}, "keep")
ok(con.execute("SELECT reviews FROM outside_books WHERE key=?", (O.key_of("اخلع حذائك"),)).fetchone()[0] == "[]",
   "… and the next save of that row empties its old review text")
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

# ---------- the author of a Goodreads page found by title alone must be the same person (audit 2026-10-12 #1)
con.execute("INSERT INTO authors (name_ar, name_latin, aliases) VALUES (?,?,?)",
            ("عبد الرحمن الشرقاوي", None, _json.dumps(["Abdel Rahman al-Sharqawi"])))
con.commit()
O._down.clear()
def gr_by_title(pages):
    return lambda goodreads_id=None, isbn=None, title=None, timeout=12.0: pages.get(title)
def stored(title, author):
    O._save(O.key_of(title), title, {"title": title, "author": author, "year": 1954}, "found")
    return O.key_of(title)

k = stored("الأرض", "عبد الرحمن الشرقاوي")
O.goodreads_page = gr_by_title({"الأرض": {"title": "الأرض", "author": "John Steinbeck", "rating": 4.1,
                                          "description": "STEINBECK-BLURB a family leaves the dust bowl for California",
                                          "genres": ["Classics"], "sources": ["https://www.goodreads.com/book/show/1"]}})
ok(O.enrich(k) is False, "«الأرض» of الشرقاوي: a Goodreads page of the same title by John Steinbeck is refused")
r = O.get("الأرض")
ok(r["author"] == "عبد الرحمن الشرقاوي" and not r.get("description") and not r.get("blurb") and not r.get("rating")
   and r["genres"] == [] and r.get("enriched_at"),
   "… nothing of the other work is stored (no blurb, rating or genres), and the page is not tried again")
ok(not O.brief(r).get("summary") and "Goodreads" not in O.brief(r).get("sources", []), "… and the chat gets no summary from it")

k = stored("الفلاح", "عبد الرحمن الشرقاوي")
O.goodreads_page = gr_by_title({"الفلاح": {"title": "الفلاح", "author": "Abd al-Rahman al-Sharqawi", "rating": 3.6,
                                          "description": "قرية مصرية وفلاحوها في مواجهة الإقطاع",
                                          "genres": ["Novels"], "sources": ["https://www.goodreads.com/book/show/2"]}})
ok(O.enrich(k) is True and O.get("الفلاح").get("blurb") and O.get("الفلاح")["rating"] == 3.6,
   "a Latin page author whose spelling we hold as the author's alias (same family name) is accepted")
ok(O.get("الفلاح")["author"] == "عبد الرحمن الشرقاوي", "… and our Arabic author name is kept")

k = stored("ليل المدينة", "سالم الوردي")
O.goodreads_page = gr_by_title({"ليل المدينة": {"title": "ليل المدينة", "author": "Salem Al-Wardi", "description": "x" * 50,
                                               "sources": ["https://www.goodreads.com/book/show/3"]}})
ok(O.enrich(k) is False and not O.get("ليل المدينة").get("blurb"),
   "a Latin name we do not hold for that author is not taken on trust, even with a likely family name")
ok(O.same_person("عبد الرحمن الشرقاوي", "Abdel Rahman Al-Sharqawi") and not O.same_person("عبد الرحمن الشرقاوي", "John Steinbeck"),
   "same_person: stored alias yes, a different Latin name no")

# ---------- two Arabic authors sharing one word are not one person (audit 2026-10-12 evening #2)
k = stored("مشترك تجريبي", "أحمد خالد توفيق")
O.goodreads_page = gr_by_title({"مشترك تجريبي": {"title": "مشترك تجريبي", "author": "أحمد مراد", "rating": 3.2,
                                                "description": "OTHER-WORK رواية أخرى لمؤلف آخر", "genres": ["Thriller"],
                                                "sources": ["https://www.goodreads.com/book/show/4"]}})
ok(O.enrich(k) is False, "«أحمد خالد توفيق» / page by «أحمد مراد» (only «أحمد» shared): refused")
r = O.get("مشترك تجريبي")
ok(r["author"] == "أحمد خالد توفيق" and not r.get("description") and not r.get("blurb") and not r.get("rating")
   and r["genres"] == [] and "OTHER-WORK" not in _json.dumps(r, ensure_ascii=False),
   "… nothing of the other work is stored")
k = stored("ظلال الليل", "محمد عبد الله")
O.goodreads_page = gr_by_title({"ظلال الليل": {"title": "ظلال الليل", "author": "محمد حسن علوان", "description": "x" * 40,
                                              "sources": ["https://www.goodreads.com/book/show/6"]}})
ok(O.enrich(k) is False and not O.get("ظلال الليل").get("blurb"), "«محمد عبد الله» / «محمد حسن علوان»: a common first name is not enough")
k = stored("ممر الضوء", "أحمد خالد توفيق")
O.goodreads_page = gr_by_title({"ممر الضوء": {"title": "ممر الضوء", "author": "أحمد خالد", "description": "x" * 40,
                                             "sources": ["https://www.goodreads.com/book/show/7"]}})
ok(O.enrich(k) is False, "«أحمد خالد» is not taken for «أحمد خالد توفيق» (another last name may follow)")
# the same person in other spellings is still accepted
for ours, page in (("عبد الرحمن منيف", "عبدالرحمن منيف"), ("د. أحمد خالد توفيق", "أحمد خالد توفيق"),
                   ("عبد الرحمن منيف", "عبد الرحمن بن إبراهيم منيف"), ("يوسف زيدان", "يُوسُف زَيدان"),
                   ("عمرو عبد الحميد", "عمرو عبدالحميد"), ("إبراهيم نصر الله", "ابراهيم نصرالله")):
    k = stored("كتاب " + page, ours)
    O.goodreads_page = gr_by_title({"كتاب " + page: {"title": "كتاب " + page, "author": page, "description": "وصف العمل نفسه " * 3,
                                                    "sources": ["https://www.goodreads.com/book/show/8"]}})
    ok(O.enrich(k) is True and O.get("كتاب " + page).get("blurb"), f"«{ours}» / «{page}»: the same person, accepted")
con.execute("INSERT INTO authors (name_ar, name_latin, aliases) VALUES (?,?,?)", ("محمد المخزنجي", None, _json.dumps(["م. المخزنجي"])))
con.commit()
ok(O.same_author("محمد المخزنجي", "م. المخزنجي") and not O.same_author("محمد المخزنجي", "محمد المنسي قنديل"),
   "an Arabic alias held on the author's record counts; another «محمد» does not")
ok(not O.same_author("أحمد خالد توفيق", "") and not O.same_author("أحمد مراد", "أحمد خالد توفيق"), "same_author: empty or one shared word -> no")

# ---------- the time budget holds even when a source is slower than it (audit 2026-10-12 #5)
O._down.clear()
def slow(seconds, result=None):
    def f(*a, **kw):
        time.sleep(seconds)
        return result
    return f
O.from_wikidata = slow(2.0)
O.from_openlibrary = slow(2.0)
t0 = time.time()
r = O.lookup("كتاب بطيء المصادر", budget=0.3, background=False)
took = time.time() - t0
ok(r is None and took < 0.8, f"both sources slower than a 0.3 s budget: back in {took:.2f} s, not after the 2 s sources")
ok(O.get("كتاب بطيء المصادر") is None, "… and a timed-out lookup is not remembered as «not found»")

O.from_openlibrary = lambda title, timeout=4.0: ({"title": "نهر سريع", "author": "كاتب", "year": 2001, "isbn": "222",
                                                  "subjects": [], "sources": ["https://openlibrary.org/works/OL3W"]}
                                                 if O.same_title("نهر سريع", title) else None)
O.goodreads_page = slow(3.0)
O.ENRICH_WAIT, keep_wait = 0.3, O.ENRICH_WAIT
t0 = time.time()
r = O.lookup("نهر سريع", budget=0.5)
took = time.time() - t0
O.ENRICH_WAIT = keep_wait
ok(r and r["author"] == "كاتب" and took < 0.5 + 0.3 + 0.5,
   f"one fast source, one slow source and a slow Goodreads: the answer comes in {took:.2f} s (budget + the short Goodreads wait)")

# ---------- VREVFIX (2026-10-27): a book suggested for the shelves — Goodreads asked together with the others
O._down.clear()
slow = {"n": 0}
def slow_ol(title, timeout=4.0):
    import time as _t
    slow["n"] += 1
    _t.sleep(1.2)
    return None
O.from_openlibrary, O.from_wikidata, O.goodreads_page = slow_ol, fake_wd, fake_gr
ok(O.lookup("الأرض بعد طوفان نوح", budget=1.0, background=False) is None and O.get("الأرض بعد طوفان نوح") is None,
   "chat lookup: Open Library slower than the budget, Goodreads not reached -> None, nothing remembered as not found")
r = O.lookup("الأرض بعد طوفان نوح", budget=3.0, background=False, goodreads_too=True)
ok(r and r["status"] == "found" and r["author"] == "شريف سامي" and "goodreads" in " ".join(r["sources"]),
   "shelf suggestion: Goodreads asked at the same time finds the Arabic book")
c3 = models.get_connection()
c3.execute("INSERT OR REPLACE INTO outside_books (key, asked_title, status, looked_at) VALUES (?,?,?,?)",
           (O.key_of("كتاب منسي"), "كتاب منسي", "not_found", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
c3.commit(); c3.close()
before = calls["gr"]
O.lookup("كتاب منسي", budget=2.0, background=False, goodreads_too=True)
ok(calls["gr"] == before + 1, "shelf suggestion: a title remembered as not found is tried again")

# AGY-20261027-02: Goodreads asked together with the others is kept when Open Library also knows the title
def stub_ol(title, timeout=4.0):
    if O.same_title("الأرض بعد طوفان نوح", title):
        return {"title": "الأرض بعد طوفان نوح", "author": None, "year": 2021, "isbn": None, "subjects": [], "publisher": None,
                "sources": ["https://openlibrary.org/works/OL9W"]}
    return None
O.from_openlibrary = stub_ol
c5 = models.get_connection(); c5.execute("DELETE FROM outside_books WHERE key=?", (O.key_of("الأرض بعد طوفان نوح"),)); c5.commit(); c5.close()
r = O.lookup("الأرض بعد طوفان نوح", budget=3.0, background=False, goodreads_too=True)
ok(r and r["author"] == "شريف سامي" and r.get("blurb") and any("goodreads" in u for u in r["sources"]) and any("openlibrary" in u for u in r["sources"]),
   "AGY-02: an Open Library stub does not drop Goodreads: its author, blurb and source are kept")

print(f"\nTOTAL pass={P} fail={F}")
sys.exit(1 if F else 0)
