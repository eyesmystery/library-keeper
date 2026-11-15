"""[SETS] Ibrahim 2026-11-13: «بعض الكتب في المكتبة مسجلة كمجموعة تنباع مع بعض. مثلا كتابين مسجلين تحت رقم واحد. هذي تحتاج معالجة
خاصة عدد صفحات الكتاب والمعلومات اللي عادة تخص كتاب واحد مو اكثر».

books.volumes / set_kind / volumes_source (backend/book_sets.py). Reading a record (title, then summary); the chat (page limits,
shortest, longest, short, the card and the facts; a collection carries no one book's year, rating, prize or ISBN; a work in parts
keeps its own); relations (no set as a shorter alternative); the card's reader level; the admin (fields, validation, list badge,
page markers); the update (the column reaches an old library database, CORRECTABLE, staff edits from the library)."""
import json, os, sqlite3, sys, importlib.util
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import book_sets as S
import card_review, honors, relations
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

ROOT = os.path.dirname(os.getcwd())

# ---------------------------------------------------------------- reading a record
def det(title, summary="", genre=None):
    return S.detect(title, summary, genre)
d = det("ذهب مع الريح (جزءان)")
ok(d and d["volumes"] == 2 and d["kind"] == "one_work" and d["source"] == "title" and d["sure"], f"1456 «(جزءان)» → 2, one work ({d})")
d = det("الأنوار والمراقب (7 أجزاء)")
ok(d and d["volumes"] == 7 and d["kind"] == "one_work", "2626 «(7 أجزاء)» → 7")
ok(det("ديفيد كوبرفيلد (1-3)")["volumes"] == 3 and det("مقدمة ابن خلدون 1/3")["volumes"] == 3
   and det("الأخوة كارامازوف 4/1")["volumes"] == 4 and det("الشياطين 2/1")["volumes"] == 2, "«(1-3)», «1/3», «4/1», «2/1»")
d = det("Magic Tree House 1-4 Treasury Boxed Set")
ok(d and d["volumes"] == 4 and d["kind"] == "collection", f"4902 Boxed Set 1-4 → 4 books, a collection ({d})")
ok(det("Al Furusiyya (2 volumes) - In Box")["volumes"] == 2 and det("سلسلة المريخ (11 جزءًا)")["volumes"] == 11
   and det("سلسلة المريخ (11 جزءًا)")["kind"] == "collection", "“(2 volumes)”, «(11 جزءًا)» (a series: different books)")
ok(det("موسوعة الفروق اللغوية (ج1-ج10)")["volumes"] == 10 and det("زمن السيداف (ج1، ج2)")["volumes"] == 2, "«(ج1-ج10)», «(ج1، ج2)»")
ok(det("مجموعة روايات همنجواي (3 أجزاء)")["kind"] == "collection" and det("الأعمال النثرية الكاملة (1-3)")["kind"] == "collection"
   and det("القطط غدًا، ملكة القطط، كوكب القطط (1-3)")["kind"] == "collection", "«مجموعة» / «الأعمال» / three titles listed → collection")
d = det("سلسلة أنا والعالم", "مجموعة من خمسة كتب مصورة بجمل قصيرة ورسوم جذابة", "أطفال")
ok(d and d["volumes"] == 5 and d["kind"] == "collection" and d["source"] == "summary" and d["sure"], f"2655 summary «خمسة كتب» → 5 ({d})")
d = det("سلسلة قصص الانبياء ٢", "المجموعة الثانية من سلسلة قصص الأنبياء للصغار جدًا: أربعة كتيبات مصورة بصفحات كرتونية", "أطفال")
ok(d and d["volumes"] == 4 and d["sure"], f"5617 «أربعة كتيبات» → 4 ({d})")
d = det("البؤساء", "رواية هوغو … في ترجمة كاملة عن الفرنسية في أربعة مجلدات.", "رواية")
ok(d and d["volumes"] == 4 and d["kind"] == "one_work" and d["sure"], f"5682 «في أربعة مجلدات» → one work in 4 ({d})")
d = det("التغريبة الفلسطينية (1: أيام البلاد، 2: حكايا المخيم)", "رواية وليد سيف في جزأين: «أيام البلاد» …")
ok(d and d["volumes"] == 2 and d["sure"], "1666 two parts named + «في جزأين» → 2")
d = det("دوريت الصغيرة (1: الفقر، 2: الغنى)", "رواية ديكنز عن دوريت الصغيرة")
ok(d and not d["sure"], "1697 the novel's two books named, the summary silent → for staff to check")
# not sets
for t, s in (("ثلاثية غرناطة", "تضم غرناطة ومريمة والرحيل"), ("القطائع: ثلاثية ابن طولون", "رواية في ثلاثة أجزاء عن أحمد بن طولون"),
             ("جهينة ج2", "الجزء الثاني من ثلاثية جهينة"), ("الأعمال الشعرية ج3", ""), ("Absolute Batman Vol. 1", ""),
             ("وهذا باب في التوريق التوريقات 1-100", ""), ("الرسائل (1875-1904)", ""), ("موساشي (الجزء 1 من 7)", ""),
             ("الأعمال الكاملة (مجلد)", ""), ("Magic Knight Rayearth 2, Vol. 1", ""), ("دراسات عن المماليك في مصر 1250 - 1517", "")):
    ok(det(t, s) is None, f"not a set: «{t}»")
d = det("سلسلة التَّربية الصّحيَّة: نوْمًا هنيئًا يا كريم", "قصة من سلسلة التربية الصحية … ضمن سلسلة من ثمانية كتب تهدف", "أطفال")
ok(d is None or not d.get("volumes"), "«ضمن سلسلة من ثمانية كتب»: one booklet of an eight-book series")
d = det("السياسة", "كتاب أرسطو في الحكم والدولة، في ثمانية كتب: يبدأ بالأسرة", "فكر")
ok(d is not None and not d["sure"], "Aristotle's «في ثمانية كتب» (the books inside one work) is left for staff, not written")
ok(not det("الأعمال القصصية الكاملة")["sure"] and not det("The Camel through the Ages - with Box")["sure"],
   "«الأعمال الكاملة» / “with Box” without a count: for staff to check")

# ---------------------------------------------------------------- labels and the facts' entry
ok(S.label({"volumes": 2, "set_kind": "one_work"}) == "في جزأين" and S.label({"volumes": 7, "set_kind": "one_work"}) == "في ٧ أجزاء"
   and S.label({"volumes": 11, "set_kind": "one_work"}) == "في ١١ جزءًا", "Arabic: «في جزأين»، «في ٧ أجزاء»، «في ١١ جزءًا»")
ok(S.label({"volumes": 5, "set_kind": "collection"}) == "مجموعة من ٥ كتب" and S.label({"volumes": 2, "set_kind": "collection"}) == "مجموعة من كتابين"
   and S.label({"volumes": 3, "set_kind": "collection"}, "en") == "a set of 3 books" and S.label({"volumes": 2, "set_kind": "one_work"}, "en") == "in 2 volumes",
   "«مجموعة من ٥ كتب»، «مجموعة من كتابين»، English")
ok(S.label({"volumes": 1}) is None and S.label({"volumes": None}) is None and not S.is_set({"volumes": "x"}), "empty / 1 = one book")
ok(S.kind({"volumes": 3}) == "collection", "a count with no kind is read as a collection (nothing of one book assumed)")
ok(S.single_pages({"volumes": 3, "page_count": 900}) is None and S.single_pages({"page_count": 120}) == 120, "single_pages")

# ---------------------------------------------------------------- the chat's view
bks = {1: {"book_id": 1, "volumes": 4, "set_kind": "collection", "page_count": 16, "original_year": 2020, "goodreads_rating": 4.5,
           "goodreads_ratings_count": 300, "isbn": "9786039126997"},
       2: {"book_id": 2, "volumes": 2, "set_kind": "one_work", "page_count": 1000, "original_year": 1936, "isbn": "x"},
       3: {"book_id": 3, "page_count": 120, "original_year": 2001}}
cds = {1: {"perceived_length": "very_short", "use_cases": ["quick_read", "gift"],
           "confidence": {("perceived_length", "very_short"): (0.9, False), ("use_cases", "quick_read"): (0.9, False), ("moods", "warm"): (0.8, False)}},
       3: {"perceived_length": "short", "use_cases": ["quick_read"], "confidence": {("perceived_length", "short"): (0.9, False)}}}
S.chat_view(bks, cds)
ok(bks[1]["page_count"] is None and bks[1]["set_pages_recorded"] == 16 and bks[1]["set_label"] == "مجموعة من ٤ كتب", "a set's pages moved aside")
ok(bks[1]["original_year"] is None and bks[1]["goodreads_rating"] is None and bks[1]["isbn"] is None, "a collection: no one year, rating, ISBN")
ok(bks[2]["original_year"] == 1936 and bks[2]["page_count"] is None and bks[2]["isbn"] == "x", "a work in parts keeps its year (pages aside)")
ok(bks[3]["page_count"] == 120 and cds[3]["perceived_length"] == "short", "one book untouched")
ok(cds[1]["perceived_length"] is None and cds[1]["use_cases"] == ["gift"] and ("perceived_length", "very_short") not in cds[1]["confidence"]
   and ("use_cases", "quick_read") not in cds[1]["confidence"] and ("moods", "warm") in cds[1]["confidence"], "the set's card: no length, no quick read")
S.chat_view(bks, cds)
ok(bks[1]["set_pages_recorded"] == 16, "a second view changes nothing")
ok(A.is_long({"volumes": 2, "page_count": 100}, None) and not A.is_long({"page_count": 100}, None), "a set is never «short»")
_sc, _rs = A.score_book({"book_id": 9, "title": "t", "author": "a", "volumes": 7, "page_count": 2120},
                        dict(A.empty_prefs() if hasattr(A, "empty_prefs") else {}, **{"length": "long", "genres": set(), "tokens": [], "authors": [],
                                                                                        "subgenres": [], "soft_subgenres": []}))
ok(not any("صفحة" in r for r in _rs), f"«رواية طويلة»: a set's 2,120 pages are no reason ({_rs})")
ok(A.card_line("Matches what you asked for: novel.", {"genre": "رواية", "volumes": 2, "set_kind": "one_work", "page_count": None,
                                                      "original_language": "en"}, "en") == "Novel, originally English, in 2 volumes.",
   "English card line: «in 2 volumes», never a page count")

# ---------------------------------------------------------------- the whole chat path on synthetic books
base = [dict(b) for b in A.list_books(only_active=False)][:30]
A.get_profile_cards = lambda: {}
A.embeddings.embed = lambda *a, **k: None
A.embeddings.search = lambda *a, **k: {}
A.embeddings.similar_to = lambda *a, **k: []
A.llm.complete_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected live-model call"))
CUR = {}
def interp(*a, **k):
    it = {"intent": "recommend", "mentioned_titles": [], "title_variants": [], "wants_new_options": False, "references": [],
          "author_request": {}, "order_by": None, "about_shown": False, "reader_age": None}
    s = dict(CUR)
    it["prefs"] = A.llm.clean_prefs(s.pop("prefs", {}))
    it.update(s)
    return it
A.chat.interpret = interp
captured = []
def respond(conv, facts_json, guidance, *a, **k):
    facts = json.loads(facts_json); captured.append({"facts": facts, "guidance": guidance})
    return {"reply": "ok", "show_book_ids": [f["id"] for f in facts][:3], "suggestions": [], "unanswered": ""}
A.chat.respond = respond
KID = dict(genre="أطفال", age_min=4, age_max=8)
SPEC = [
    dict(KID, title="الأرنب الصغير", page_count=120, summary="قصة أرنب يتعلم المشاركة"),                                          # 0
    dict(KID, title="سلسلة قصص الأنبياء للصغار", page_count=16, volumes=4, set_kind="collection", volumes_source="summary",
         summary="أربعة كتيبات مصورة عن الأنبياء"),                                                                               # 1
    dict(title="رواية قصيرة جدا", page_count=180, summary="رواية عن مدينة صغيرة"),                                                 # 2
    dict(title="الأنوار والمراقب (7 أجزاء)", page_count=2120, volumes=7, set_kind="one_work", volumes_source="title",
         original_year=1850, goodreads_rating=4.3, goodreads_ratings_count=5000, summary="عمل في سبعة أجزاء"),                      # 3
    dict(title="الرواية الطويلة", page_count=1100, summary="ملحمة عائلية طويلة"),                                                    # 4
    dict(title="مجموعة روايات الكاتب (5 أجزاء)", page_count=3000, volumes=5, set_kind="collection", volumes_source="title",
         original_year=1900, goodreads_rating=4.5, goodreads_ratings_count=9000, isbn="9780000000002", summary="خمس روايات"),        # 5
    dict(title="ذهب مع الريح (جزءان)", page_count=None, volumes=2, set_kind="one_work", volumes_source="title", original_year=1936,
         summary="رواية مارغريت ميتشل عن الجنوب الأمريكي"),                                                                       # 6
    dict(KID, title="قصص قبل النوم", page_count=40, summary="قصص قصيرة للأطفال"),                                                  # 7
] + [dict(title=f"رواية عادية {n}", page_count=300 + n, summary="رواية عن الحياة اليومية") for n in range(10)]                 # 8–17
books = []
for b, sp in zip([dict(x) for x in base], SPEC):
    b.update(quantity=3, active=1, author="كاتب " + str(b["book_id"]), original_year=None, summary="", hook="", keywords="", subjects="",
             subgenres=None, series_name=None, series_position=None, goodreads_url=None, goodreads_rating=None, goodreads_ratings_count=None,
             language="ar", age_min=None, age_max=None, genre="رواية", publication_year=2015, setting=None, volumes=None, set_kind=None,
             volumes_source=None, isbn=None, original_language="ar")
    b.update(sp)
    books.append(b)
A.list_books = lambda *a, **k: [dict(x) for x in books]
A.topics._cache.update(key=None)
I = {k: books[k]["book_id"] for k in range(len(SPEC))}
HON = {I[3]: {"awards": ["فاز بجائزة ما 1900"], "author_awards": [], "adaptations": ["فيلم 1950"]},
       I[5]: {"awards": ["فاز بجائزة ما 1910"], "author_awards": ["مؤلفه فاز بنوبل"], "adaptations": ["فيلم 1960"]}}
_for_book = honors.for_book
A.honors.for_book = lambda bid: HON.get(bid, {"awards": [], "author_awards": [], "adaptations": []})

def run(msgs, language="ar", **it):
    CUR.clear(); CUR.update(it)
    captured.clear()
    m = [{"role": "user", "content": x} if isinstance(x, str) else x for x in msgs]
    r = A._run_chat_once(m, active=("synthetic", "no-network"), language=language)
    c = captured[0] if captured else {"facts": [], "guidance": ""}
    return r, c["facts"], c["guidance"], r.get("books") or []

# «ابي رواية أقل من ٢٠٠ صفحة للأطفال»
_r, _f, _g, _b = run(["ابي رواية أقل من ٢٠٠ صفحة للأطفال"], prefs={"genres": ["أطفال"], "audience": "children"})
_ids = [f["id"] for f in _f]
ok(I[1] not in _ids and I[0] in _ids, f"«أقل من ٢٠٠ صفحة للأطفال»: the four-booklet set (16 pages) is not offered; a 120-page book is ({_ids})")
_r, _f, _g, _b = run(["كتب أقل من ٢٠٠ صفحة"], prefs={})
_ids = [f["id"] for f in _f]
ok(I[1] not in _ids and I[2] in _ids, f"«أقل من ٢٠٠ صفحة» (no shelf): no set passes the page limit ({_ids})")
_r, _f, _g, _b = run(["ابي رواية فوق الألف صفحة"], prefs={"genres": ["رواية"]})
_ids = [f["id"] for f in _f]
ok(I[4] in _ids and I[3] not in _ids and I[5] not in _ids, f"«فوق الألف صفحة»: the 1,100-page novel; the 7-part (2,120) and 5-novel (3,000) records do not pass ({_ids})")

# «أطول رواية عندكم»
_r, _f, _g, _b = run(["أطول رواية عندكم"], prefs={"genres": ["رواية"]}, order_by="longest")
_ids = [f["id"] for f in _f]
ok(_ids[:1] == [I[4]], f"«أطول رواية»: the 1,100-page novel is the longest, not the 7-part (2,120) or the 5-novel (3,000) records ({_ids[:4]})")
ok(I[3] not in _ids[:3] and I[5] not in _ids[:3] and "2120" not in _g and "3000" not in _g and "٢١٢٠" not in _g, "…and the sets are not in the ordered list")

# «أقصر كتاب أطفال»
_r, _f, _g, _b = run(["وش أقصر كتاب أطفال عندكم؟"], prefs={"genres": ["أطفال"], "audience": "children"}, order_by="shortest")
_ids = [f["id"] for f in _f]
ok(_ids[:1] == [I[7]] and (I[1] not in _ids or _ids.index(I[1]) > 0), f"«أقصر»: the 40-page book first, not the 16-page set ({_ids[:4]})")

# «ابي كتاب قصير لبنتي عمرها 6»
_r, _f, _g, _b = run(["ابي كتاب قصير لبنتي عمرها 6"], prefs={"genres": ["أطفال"], "audience": "children", "length": "short"})
_ids = [f["id"] for f in _f]
ok(I[1] not in _ids and (I[0] in _ids or I[7] in _ids), f"«كتاب قصير لبنتي»: no set is offered for a short book ({_ids})")
# the set is still offered when the visitor wants it
_r, _f, _g, _b = run(["ابي سلسلة قصص الأنبياء للصغار"], prefs={"genres": ["أطفال"], "audience": "children"},
                     mentioned_titles=["سلسلة قصص الأنبياء للصغار"], intent="about_book")
_ids = [f["id"] for f in _f]
ok(I[1] in _ids, f"a set asked for by name is offered ({_ids})")
_fs = next((f for f in _f if f["id"] == I[1]), {})
ok(_fs.get("set", {}).get("volumes") == 4 and _fs["set"]["say"] == "مجموعة من ٤ كتب" and _fs.get("pages_approx") is None,
   f"its facts: «مجموعة من ٤ كتب», no page count ({_fs.get('set')}, {_fs.get('pages_approx')})")

# «عندكم ذهب مع الريح؟ كم صفحة؟»
_r, _f, _g, _b = run(["عندكم ذهب مع الريح؟ كم صفحة؟"], mentioned_titles=["ذهب مع الريح"], intent="about_book")
_fg = next((f for f in _f if f["id"] == I[6]), {})
ok(_fg.get("set", {}).get("say") == "في جزأين" and _fg.get("pages_approx") is None and _fg.get("first_published") == 1936,
   f"«ذهب مع الريح»: «في جزأين», no page count, the work's year kept ({_fg.get('set')}, {_fg.get('first_published')})")
ok("set.say" in _g and "ما عندنا عدد صفحات الجزء الواحد" in _g, "the guidance says how to speak of a set and of its pages")
_bg = next((b for b in _b if b["book_id"] == I[6]), {})
ok(_bg.get("set_label") == "في جزأين" and _bg.get("volumes") == 2 and not _bg.get("page_count"), f"the visitor's card carries the set ({_bg.get('set_label')})")
_r, _f, _g, _b = run(["Do you have Gone with the Wind? How many pages?"], mentioned_titles=["ذهب مع الريح"], intent="about_book", language="en")
_fg = next((f for f in _f if f["id"] == I[6]), {})
ok(_fg.get("set", {}).get("say") == "in 2 volumes" and "set.say" in _g and "no page count of one volume" in _g, "English: «in 2 volumes»")

# a collection vs a work in parts: year, rating, prize, ISBN
_r, _f, _g, _b = run(["عندكم مجموعة روايات الكاتب؟"], mentioned_titles=["مجموعة روايات الكاتب"], intent="about_book")
_fc = next((f for f in _f if f["id"] == I[5]), {})
ok(_fc and "first_published" not in _fc and "goodreads_rating" not in _fc and "awards" not in _fc and "adaptations" not in _fc,
   f"a collection: no one first year, rating, prize or adaptation ({sorted(_fc)})")
ok(_fc.get("author_awards") == ["مؤلفه فاز بنوبل"] and _fc.get("set", {}).get("kind") == "مجموعة كتب مختلفة", "…its author's own prize stays")
_r, _f, _g, _b = run(["عندكم الأنوار والمراقب؟"], mentioned_titles=["الأنوار والمراقب"], intent="about_book")
_fw = next((f for f in _f if f["id"] == I[3]), {})
ok(_fw.get("first_published") == 1850 and _fw.get("awards") and _fw.get("goodreads_rating") and _fw.get("pages_approx") is None
   and _fw.get("set", {}).get("say") == "في ٧ أجزاء", f"a work in 7 parts keeps year, prize, rating; no page count ({sorted(_fw)})")
_fbook = A.book_fact(dict(books[5]), None, [], "ar")   # a path that never took the chat's view
ok("first_published" not in _fbook and "goodreads_rating" not in _fbook and "awards" not in _fbook and _fbook.get("pages_approx") is None,
   "book_fact on a raw collection record: the same")
ok(A.edition_fact(dict(books[3])).get("set") == "في ٧ أجزاء" and "pages_approx" not in A.edition_fact(dict(books[3])), "edition facts too")
A.honors.for_book = _for_book

# ---------------------------------------------------------------- prizes and adaptations of a collection (honors.load)
con = models.get_connection()
two = [r[0] for r in con.execute("SELECT book_id FROM books ORDER BY book_id LIMIT 2")]
con.execute("UPDATE books SET volumes = 5, set_kind = 'collection', volumes_source = 'title' WHERE book_id = ?", (two[0],))
con.execute("UPDATE books SET volumes = 2, set_kind = 'one_work', volumes_source = 'title' WHERE book_id = ?", (two[1],))
con.commit(); con.close()
honors.ensure_table(models.get_connection())
for bid in two:
    honors.add("book_awards", bid, {"award_key": "pulitzer", "year": 1937, "result": "winner"}, "https://example.org/a")
honors.clear_cache()
_d = honors.load(0)
ok(two[0] not in _d["book"] and two[1] in _d["book"], "honors: a collection's prize is left out of what the visitor is told; a work in parts keeps it")
ok(honors.rows_for_book(two[0])["awards"], "…the admin card still lists the row (staff can see and remove it)")
ok(not honors.for_book(two[0])["awards"] and honors.for_book(two[1])["awards"], "honors.for_book")

# ---------------------------------------------------------------- relations: no set as a shorter alternative
_bk = [{"book_id": 1, "title": "أ", "author": "س", "language": "ar"}, {"book_id": 2, "title": "ب", "author": "ص", "language": "ar", "volumes": 4},
       {"book_id": 3, "title": "ج", "author": "ع", "language": "ar"}]
_pr = {i: {"overall_status": "enriched", "format": "novel", "themes": '["الحب", "الحرب"]', "moods": '["dark"]', "perceived_length": pl,
           "emotional_weight": "medium", "language_difficulty": "medium", "pace": "slow"} for i, pl in ((1, "long"), (2, "very_short"), (3, "short"))}
_rel = relations.compute(_bk, _pr)
_short = {(r[0], r[1]) for r in _rel if r[2] == "shorter_alternative"}
ok((1, 3) in _short and (1, 2) not in _short and not any(2 in k for k in _short),
   f"relations: the set is no shorter alternative (and is compared with nothing) ({sorted(_short)})")
con = models.get_connection()
con.execute("INSERT INTO book_relations (source_book_id, target_book_id, relation_type, reason_ar, confidence) VALUES (?, ?, 'shorter_alternative', 'أقصر', 0.7)",
            (two[1] + 5, two[0]))
con.execute("INSERT INTO book_relations (source_book_id, target_book_id, relation_type, reason_ar, confidence) VALUES (?, ?, 'similar', 'مشابه', 0.7)",
            (two[1] + 5, two[0]))
con.commit(); con.close()
_fr = relations.for_books([two[1] + 5]).get(two[1] + 5, [])
ok([r["relation_type"] for r in _fr if r["target_book_id"] == two[0]] == ["similar"], "a stored «shorter» relation to a set is dropped when read")
ok(all(r["relation_type"] != "shorter_alternative" or r["target_book_id"] != two[0] for r in relations.for_book(two[1] + 5)), "relations.for_book too")

# ---------------------------------------------------------------- the card's reader level
con = models.get_connection()
kid = [r[0] for r in con.execute("SELECT book_id FROM books ORDER BY book_id LIMIT 4 OFFSET 2")]
for bid in kid[:2]:
    con.execute("DELETE FROM attribute_evidence WHERE book_id = ? AND attribute_name IN ('language_difficulty', 'perceived_length', 'pace', 'emotional_weight', 'reader_level')", (bid,))
    con.execute("INSERT OR IGNORE INTO book_profiles (book_id, overall_status) VALUES (?, 'pending')", (bid,))
    card_review._insert_staff_row(con, bid, "language_difficulty", "easy", "test", "2026-11-13T00:00:00+00:00")
    card_review._insert_staff_row(con, bid, "perceived_length", "very_short", "test", "2026-11-13T00:00:00+00:00")
con.execute("UPDATE books SET volumes = 4, set_kind = 'collection' WHERE book_id = ?", (kid[0],))
con.execute("UPDATE books SET volumes = NULL, set_kind = NULL WHERE book_id = ?", (kid[1],))
for bid in kid[:2]:
    card_review.recompute_card(con, bid)
con.commit()
_lv = {bid: con.execute("SELECT reader_level FROM book_profiles WHERE book_id = ?", (bid,)).fetchone()[0] for bid in kid[:2]}
con.close()
ok(_lv[kid[1]] == "beginner" and _lv[kid[0]] == "multiple", f"recompute_card: a set's «very short» says nothing of the level ({_lv})")
# the targeted refresh (used by the staff save and the data pass): only the derived level, nothing else of the card
con = models.get_connection()
rb = [r[0] for r in con.execute("SELECT book_id FROM books ORDER BY book_id LIMIT 2 OFFSET 12")]
for bid in rb:
    con.execute("INSERT OR IGNORE INTO book_profiles (book_id, overall_status) VALUES (?, 'pending')", (bid,))
    con.execute("""UPDATE book_profiles SET language_difficulty = 'easy', perceived_length = 'very_short', pace = NULL, emotional_weight = NULL,
                   reader_level = 'beginner', overall_status = 'enriched', age_suitability = 'children' WHERE book_id = ?""", (bid,))
    con.execute("DELETE FROM attribute_evidence WHERE book_id = ? AND attribute_name = 'reader_level'", (bid,))
    con.execute("INSERT INTO attribute_evidence (book_id, attribute_name, attribute_value, confidence, source_site) VALUES (?, 'reader_level', 'beginner', 0.7, 'derived')", (bid,))
con.execute("UPDATE books SET volumes = 4, set_kind = 'collection' WHERE book_id = ?", (rb[0],))
con.execute("UPDATE books SET volumes = NULL, set_kind = NULL WHERE book_id = ?", (rb[1],))
_ch = [card_review.refresh_reader_level(con, bid) for bid in rb]
con.commit()
_pf = {bid: tuple(con.execute("SELECT reader_level, overall_status, age_suitability, perceived_length FROM book_profiles WHERE book_id = ?", (bid,)).fetchone())
       for bid in rb}
_ev = con.execute("SELECT attribute_value FROM attribute_evidence WHERE book_id = ? AND attribute_name = 'reader_level'", (rb[0],)).fetchone()[0]
con.close()
ok(_ch == ["multiple", None] and _pf[rb[0]] == ("multiple", "enriched", "children", "very_short") and _ev == "multiple"
   and _pf[rb[1]] == ("beginner", "enriched", "children", "very_short"),
   f"refresh_reader_level: the set's derived level only; status, age and stored length untouched ({_ch}, {_pf})")

# ---------------------------------------------------------------- the admin
src = open(os.path.join(ROOT, "admin", "index.html"), encoding="utf-8").read()
fe = open(os.path.join(ROOT, "frontend", "app.js"), encoding="utf-8").read()
ok('id="bVolumes"' in src and 'id="bSetKind"' in src and "عدد الأجزاء/الكتب في السجل" in src and "عمل واحد مقسوم على أجزاء" in src
   and "مجموعة كتب مختلفة" in src, "the admin card: the two fields and their labels")
ok("setBadge(b)" in src and "مجموعة · ${num(b.volumes)}" in src and "'sets', 'مجموعة (أكثر من كتاب)'" in src, "the «مجموعة» badge and its filter")
ok("volumes: $('bVolumes').value.trim()" in src and "k === 'volumes'" in src and "اختر نوع المجموعة" in src, "the save sends them, a set needs its kind")
ok("function lengthText(book)" in fe and "setOf: (n, kind, f)" in fe and "$('dPages').textContent = lengthText(book);" in fe
   and "const pages = lengthText(book);" in fe, "the visitor's card and page show the set, not a page count")
st = login(A.app.test_client(), "staff")
bid = kid[2]
res = st.patch(f"/api/books/{bid}", json={"volumes": 3, "set_kind": "one_work"})
row = dict(models.get_book(bid))
ok(res.status_code == 200 and row["volumes"] == 3 and row["set_kind"] == "one_work" and row["volumes_source"] == "staff",
   f"staff saves «٣ أجزاء، عمل واحد»; the source is staff ({res.status_code}, {row['volumes']}, {row['set_kind']}, {row['volumes_source']})")
con = models.get_connection()
aud = con.execute("SELECT details FROM audit_log WHERE action = 'book.update' AND target = ? ORDER BY log_id DESC LIMIT 1", (str(bid),)).fetchone()
con.close()
ok(aud and '"volumes": [null, 3]' in aud[0] and '"set_kind": [null, "one_work"]' in aud[0], "…written in the audit (old and new)")
ok(st.patch(f"/api/books/{bid}", json={"volumes": 4}).status_code == 200 and dict(models.get_book(bid))["set_kind"] == "one_work",
   "the count alone changes (the kind kept)")
ok(st.patch(f"/api/books/{kid[3]}", json={"volumes": 2}).status_code == 400, "a set with no kind is refused")
ok(st.patch(f"/api/books/{bid}", json={"volumes": 0}).status_code == 400 and st.patch(f"/api/books/{bid}", json={"volumes": 100}).status_code == 400
   and st.patch(f"/api/books/{bid}", json={"set_kind": "box"}).status_code == 400, "0, 100 and an unknown kind are refused")
res = st.patch(f"/api/books/{bid}", json={"volumes": 1})
row = dict(models.get_book(bid))
ok(res.status_code == 200 and row["volumes"] == 1 and row["set_kind"] is None and row["volumes_source"] == "staff",
   "«١» = one book, said by staff: the kind goes")
res = st.patch(f"/api/books/{bid}", json={"volumes": None})
ok(res.status_code == 200 and dict(models.get_book(bid))["volumes_source"] is None, "emptied: no source either")
st.patch(f"/api/books/{bid}", json={"volumes": 5, "set_kind": "collection"})
cat = {b["book_id"]: b for b in st.get("/api/admin/catalog").get_json()}
ok(cat[bid]["volumes"] == 5 and cat[bid]["set_kind"] == "collection" and cat[kid[3]]["volumes"] is None, "the catalogue list carries the badge's data")
card = st.get(f"/api/admin/books/{bid}").get_json()
ok(card["book"]["volumes"] == 5 and card["book"]["volumes_source"] == "staff", "the admin card carries the fields")
ok(A.app.test_client().patch(f"/api/books/{bid}", json={"volumes": 2, "set_kind": "one_work"}).status_code in (401, 403), "not signed in: refused")
con = models.get_connection()
con.execute("UPDATE books SET volumes = NULL, set_kind = NULL WHERE book_id = ?", (rb[1],))
con.execute("UPDATE book_profiles SET reader_level = 'beginner', overall_status = 'enriched' WHERE book_id = ?", (rb[1],))
con.commit(); con.close()
ok(st.patch(f"/api/books/{rb[1]}", json={"volumes": 3, "set_kind": "collection"}).status_code == 200, "staff marks a carded book as a set")
con = models.get_connection()
_pf = tuple(con.execute("SELECT reader_level, overall_status, age_suitability FROM book_profiles WHERE book_id = ?", (rb[1],)).fetchone())
con.close()
ok(_pf == ("multiple", "enriched", "children"), f"…its derived level follows; the card's status and age stay ({_pf})")
import inbox_export
ok(inbox_export.FIELD_AR.get("volumes") and inbox_export.FIELD_AR.get("set_kind") and "volumes_source" in inbox_export.SKIP_FIELDS,
   "the inbox file names the two fields")

# ---------------------------------------------------------------- the update
spec = importlib.util.spec_from_file_location("apply_update_sets", os.path.join(ROOT, "scripts", "apply_update.py"))
U = importlib.util.module_from_spec(spec); spec.loader.exec_module(U)
ok({"volumes", "set_kind", "volumes_source"} <= U.CORRECTABLE and not {"volumes", "set_kind"} & set(U.BOOK_LOCAL_COLS),
   "CORRECTABLE has the three; they are catalogue columns (not the library's own)")
W = os.path.join(ROOT, "sets-upd"); os.makedirs(W, exist_ok=True)
old = os.path.join(W, "library_old.db")
U.copy_db(models.DB_PATH, old)
c = sqlite3.connect(old)
for col in ("volumes", "set_kind", "volumes_source"):
    c.execute(f"ALTER TABLE books DROP COLUMN {col}")
c.commit(); c.close()
base_cat = U.build_catalog(old, os.path.join(W, "base.db"), {"version": "0.9.21 beta"}, {})
new_src = os.path.join(W, "dev.db")
U.copy_db(models.DB_PATH, new_src)
c = sqlite3.connect(new_src)
c.execute("UPDATE books SET volumes = 2, set_kind = 'one_work', volumes_source = 'title' WHERE book_id = ?", (kid[3],))
c.commit(); c.close()
new_cat = U.build_catalog(new_src, os.path.join(W, "new.db"), {"version": "0.9.23 beta"}, {})
lib = os.path.join(W, "library.db")
U.copy_db(old, lib)
ok("volumes" not in U.columns_of(sqlite3.connect(lib), "books"), "the library's database is an old one (no column)")
rep = U.merge(lib, new_cat, base_cat)
c = sqlite3.connect(lib)
ok({"volumes", "set_kind", "volumes_source"} <= set(U.columns_of(c, "books")), "after the update the library has the three columns")
ok(c.execute("SELECT volumes, set_kind, volumes_source FROM books WHERE book_id = ?", (kid[3],)).fetchone() == (2, "one_work", "title"),
   "…and the values written on the developer's side")
c.close()
upd = os.path.join(W, "upd"); os.makedirs(upd, exist_ok=True)
json.dump({"items": [{"book_id": kid[1], "field": "volumes", "expect": None, "value": 3, "source": "title «(1-3)»"},
                     {"book_id": kid[1], "field": "set_kind", "expect": None, "value": "one_work", "source": "title"}]},
          open(os.path.join(upd, "corrections.json"), "w", encoding="utf-8"))
res = U.apply_corrections(lib, upd, "0.9.23 beta")
c = sqlite3.connect(lib)
ok(res["error"] is None and len(res["applied"]) == 2 and c.execute("SELECT volumes, set_kind FROM books WHERE book_id = ?", (kid[1],)).fetchone() == (3, "one_work"),
   f"a correction of the count travels through CORRECTABLE ({res})")
c.close()
spec2 = importlib.util.spec_from_file_location("apply_library_edits_sets", os.path.join(ROOT, "scripts", "apply_library_edits.py"))
E = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(E)
ok(E.LABEL_FIELD.get("عدد الأجزاء أو الكتب في السجل") == "volumes" and "volumes" in E.BOOK_FIELDS and "set_kind" in E.BOOK_FIELDS
   and E.value("volumes", "3") == 3, "a staff edit made at the library comes back here (inbox §5)")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
