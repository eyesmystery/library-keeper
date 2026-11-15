"""[DECIDE1115] Ibrahim 2026-11-15 «موافق عالتوصيات» on QADFIX38 (a)–(c), QADFIX39 (a)–(d), SETS (1)–(3). The items with code:
(Q39b) a book whose summary says it is one of a series («الجزء الثاني من ثلاثية…»، «من سلسلة…») counts as one for a standalone wish,
       series fields empty; «سلسلة من الأحداث»، «الجزء الأكبر من حياته» do not;
(Q39c) a reader of 10–13 on the children's path asking about a subject: books whose recorded age covers him or is within a year
       («كتاب الغابة» 9–11 for 12) come first, then books with no recorded age;
(Q38a) a child under six, nothing on her subject for her age: say so, then two picture books for her age on other subjects;
(Q38b) editions of one work («فروسية (جزءان) - غلاف لين / فاخر / بالعلبة») are one card, the reply names editions and prices;
(SETS2) a Rewaa import marks a set by itself only when the title says so plainly;
(EDITS) apply_library_edits shows a field decided here (release/library_corrections.json) as «DECIDED HERE», never «APPLY».
Each with the round's wording, a fresh one and negatives."""
import json, os, re, subprocess, sys, tempfile, importlib.util, sqlite3
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import book_sets
import inbox_export as IE
import rewaa_import as R
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

ROOT = os.path.dirname(os.getcwd())
_ss = getattr(A, "summary_series1115", lambda s: False)
_wt = getattr(A, "work_title1115", lambda t: A.chat_rules.title_key(t))
_ek = getattr(A, "edition_key1115", lambda b, lang_strict=False: (A.chat_rules.title_key(b.get("title") or ""), b.get("author")))
_clear = getattr(book_sets, "clear_from_title", lambda t: None)

# ---------------------------------------------------------------- Q39b: the summary says «one of a series»
for s in ("الجزء الثاني من ثلاثية غرناطة: تعود مريمة…", "رواية تشويق من سلسلة رجل المستحيل", "الكتاب الأول في السلسلة، وفيه يبدأ…",
          "الجزء الأول من مذكرات باراك أوباما", "الجزء الثالث", "Book two of the Stormlight trilogy", "The first book in the Mistborn series"):
    ok(_ss(s), f"Q39b: «{s[:40]}» says one of a series")
for s in ("يتعرض البطل لسلسلة من الأحداث الغريبة", "سلسلة من الجرائم تهز المدينة", "قضى الجزء الأكبر من حياته في المنفى",
          "يروي الجزء الأول من حياته في القرية", "في الجزء الثاني يواجه البطل خصمه", "ينقسم الكتاب إلى ثلاثة أجزاء، الجزء الأول عن الطفولة",
          "السلسلة الغذائية في البحر", "a series of murders in Oslo", "adapted into a TV series", "رواية عن الحب والحرب"):
    ok(not _ss(s), f"Q39b: «{s[:40]}» is no series")

# ---------------------------------------------------------------- Q38b: the same work (unit)
ok(_wt("فروسية (جزءان) - غلاف لين، 2010") == _wt("فروسية (جزءان) - غلاف فاخر بالعلبة، 2010") == _wt("فروسية (جزءان)")
   and _wt("Al Furusiyya (2 volumes) - In Box") == _wt("Al Furusiyya (2 volumes) - Luxurious Cover")
   and _wt("شروق وغروب - غلاف قاسي") == _wt("شروق وغروب - غلاف لين"),
   "Q38b: a binding / box / year at the end of a title is no other work")
ok(_wt("الأعمال الكاملة - مجلد 2") != _wt("الأعمال الكاملة - مجلد 1") and _wt("الحرب والسلم - الجزء 1") != _wt("الحرب والسلم - الجزء 2")
   and _wt("حياتي - قصة") != _wt("حياتي"), "Q38b negative: «مجلد 2» / «الجزء 1» / a word that is no edition word keep the titles apart")
_b = lambda **k: dict(dict(book_id=1, title="فروسية (جزءان) - غلاف لين، 2010", author="اللجنة التنفيذية", language="ar", volumes=2,
                           series_position=None), **k)
ok(_ek(_b()) == _ek(_b(book_id=2, title="فروسية (جزءان) - غلاف فاخر، 2010")) and _ek(_b()) is not None,
   "Q38b: two bindings of «فروسية» share the work key")
ok(_ek(_b()) != _ek(_b(book_id=2, author="مؤلف آخر")) and _ek(_b()) != _ek(_b(book_id=2, volumes=None))
   and _ek(_b()) != _ek(_b(book_id=2, series_position=2)) and _ek(_b(author="غير معروف")) is None,
   "Q38b negative: another author, another set count (SETS), another part, or no real author → never one work")
ok(_ek(dict(_b(), title="1984", language="en"), lang_strict=True) != _ek(dict(_b(), title="1984"), lang_strict=True)
   and _ek(dict(_b(), title="1984", language="en")) == _ek(dict(_b(), title="1984")),
   "Q38b: copies in two languages stay apart when the visitor asked a copy language (one work otherwise)")

# ---------------------------------------------------------------- whole path on synthetic books
base = [dict(b) for b in A.list_books(only_active=False)][:110]
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
SHOW = {"n": 2}
def respond(conv, facts_json, guidance, *a, **k):
    facts = json.loads(facts_json); captured.append({"facts": facts, "guidance": guidance})
    return {"reply": "ok", "show_book_ids": [f["id"] for f in facts][:SHOW["n"]], "suggestions": [], "unanswered": ""}
A.chat.respond = respond
FAN = '["فانتازيا"]'
LJ = "اللجنة التنفيذية بمكتبة الملك عبدالعزيز العامة"
SPEC = [
    dict(title="سيف الظلال", subgenres=FAN, summary="الجزء الثاني من ثلاثية الظلال: يعود الفارس إلى المملكة المسحورة"),        # 0
    dict(title="مدينة النجوم", subgenres=FAN, summary="رواية فانتازيا عن مدينة سحرية تختفي كل ليلة"),                          # 1
    dict(title="حرب السحرة", subgenres=FAN, summary="سلسلة من الأحداث الغريبة تقود ساحرًا شابًا إلى برج قديم"),                 # 2
    dict(title="قصص الحيوان في القرآن", genre="أطفال", page_count=None, summary="قصص الحيوانات التي ذكرت في القرآن الكريم"),       # 3
    dict(title="كتاب الغابة", genre="أطفال", age_min=9, age_max=11, keywords='["حيوانات", "غابة"]', summary="طفل تربيه الذئاب في الغابة"),  # 4
    dict(title="حيوانات المزرعة", genre="أطفال", page_count=None, summary="تعرف على الحيوانات في المزرعة"),                       # 5
    dict(title="جيم والحيوانات", genre="أطفال", age_min=6, summary="قصة جيم مع الحيوانات"),                                      # 6
    dict(title="قمر وقطط الحي", genre="أطفال", age_min=9, age_max=12, page_count=150, summary="قصة عن القطط في الحي"),            # 7
    dict(title="حكايات القط الكبيرة", genre="أطفال", page_count=672, summary="حكايات مرحة عن قط شقي وصديقاته القطط"),             # 8
    dict(title="البطة الصغيرة", genre="أطفال", age_min=3, age_max=6, page_count=24, series_name="سلسلة البطة", author="فيء موسى",
         summary="قصة مصورة عن بطة صغيرة تتعلم السباحة"),                                                                         # 9
    dict(title="البطة الصغيرة في المطر", genre="أطفال", age_min=3, age_max=6, page_count=20, series_name="سلسلة البطة",
         summary="قصة مصورة عن البطة في يوم ممطر"),                                                                               # 10
    dict(title="أرنوب يتعلم العد", genre="أطفال", age_min=2, age_max=5, page_count=20, summary="قصة عن أرنب يتعلم العد"),          # 11
    dict(title="مجموعة الصغار (4 كتب)", genre="أطفال", age_min=3, age_max=6, page_count=80, volumes=4, set_kind="collection",
         summary="قصص مصورة للصغار"),                                                                                             # 12
    dict(title="حكايات الجدة الطويلة", genre="أطفال", age_min=3, age_max=6, page_count=120, summary="حكايات مصورة قبل النوم"),     # 13
    dict(title="فروسية (جزءان) - غلاف لين، 2010", genre="تاريخ", author=LJ, volumes=2, set_kind="one_work", price=420.0,
         summary="كتاب عن تاريخ الفروسية والخيل العربية في الجزيرة"),                                                             # 14
    dict(title="فروسية (جزءان) - غلاف فاخر، 2010", genre="تاريخ", author=LJ, volumes=2, set_kind="one_work", price=770.0,
         summary="كتاب عن تاريخ الفروسية والخيل العربية في الجزيرة"),                                                             # 15
    dict(title="فروسية (جزءان) - غلاف فاخر بالعلبة، 2010", genre="تاريخ", author=LJ, volumes=2, set_kind="one_work", price=1300.0,
         summary="كتاب عن تاريخ الفروسية والخيل العربية في الجزيرة"),                                                             # 16
    dict(title="فروسية (جزءان) - غلاف لين، 2012", genre="تاريخ", author="مؤلف آخر", volumes=2, set_kind="one_work", price=300.0,
         summary="كتاب آخر عن الفروسية والخيل"),                                                                                  # 17
    dict(title="Al Furusiyya (2 volumes) - In Box", genre="تاريخ", author=LJ, language="en", volumes=2, set_kind="one_work", price=1400.0,
         summary="A history of Arabian horsemanship and the Arabian horse"),                                                     # 18
    dict(title="أصلي مع ماما", genre="أطفال", age_min=3, age_max=6, page_count=20, author="فيء موسى",
         summary="قصة مصورة عن طفلة تقلد أمها"),                                                                                  # 19
] + [dict(title=f"رواية عادية {n}", summary="رواية عن الحياة اليومية في المدينة") for n in range(60)]
books = []
for b, sp in zip([dict(x) for x in base], SPEC):
    b.update(quantity=3, active=1, author="كاتب " + str(b["book_id"]), original_year=None, summary="", hook="", keywords="", subjects="",
             subgenres=None, series_name=None, series_position=None, goodreads_url=None, language="ar", age_min=None, age_max=None,
             page_count=200, genre="رواية", publication_year=2015, setting=None, volumes=None, set_kind=None, price=40.0)
    b.update(sp)
    books.append(b)
assert len(books) == len(SPEC), (len(books), len(SPEC))
A.list_books = lambda *a, **k: books
A.topics._cache.update(key=None)
I = {k: books[k]["book_id"] for k in range(len(SPEC))}
_REV = {v: k for k, v in I.items()}
ix = lambda ids: [_REV.get(i, i) for i in ids]

def run(msgs, language="ar", show=2, **it):
    CUR.clear(); CUR.update(it)
    captured.clear(); SHOW["n"] = show
    m = [{"role": "user", "content": x} if isinstance(x, str) else x for x in msgs]
    r = A._run_chat_once(m, active=("synthetic", "no-network"), language=language)
    c = captured[0] if captured else {"facts": [], "guidance": ""}
    return r, [f["id"] for f in c["facts"]], c["guidance"], (r.get("_debug") or {}), [b["book_id"] for b in r.get("books") or []], c["facts"]

# Q39b (QAD 2026-11-14 9.2–9.3 class)
PF = {"genres": ["رواية"], "subgenres": ["فانتازيا"]}
_r, _f, _g, _d, _c, _fx = run(["أبي رواية فانتازيا مستقلة، مو جزء من سلسلة"], prefs=dict(PF, unsupported_requests=["مو جزء من سلسلة"]))
ok(I[0] in _f and I[1] in _f and _f.index(I[1]) < _f.index(I[0]) and _f.index(I[2]) < _f.index(I[0]),
   f"Q39b: «مو جزء من سلسلة» → «سيف الظلال» (its summary: «الجزء الثاني من ثلاثية») after the standalones ({ix(_f[:4])})")
ok(any(f["id"] == I[0] and "سلسلة" in str(f.get("series") or "") for f in _fx) and not any(f["id"] == I[2] and f.get("series") for f in _fx),
   "Q39b: its facts say it is part of a series by its summary; «سلسلة من الأحداث» is not")
_r, _f, _g, _d, _c, _fx = run(["something standalone in fantasy? not part of a series"], language="en",
                              prefs=dict(PF, unsupported_requests=["not part of a series"]))
ok(I[0] in _f and _f.index(I[1]) < _f.index(I[0]), f"Q39b fresh (English): the same ({ix(_f[:4])})")
_r, _f, _g, _d, _c, _fx = run(["أبي رواية فانتازيا"], prefs=PF)
ok(I[0] in _f and not any(f.get("series") for f in _fx if f["id"] == I[0]),
   f"Q39b negative: no standalone wish → the order and the facts as before ({ix(_f[:3])})")

# Q39c (QAD 2026-11-14 12.1–12.3)
PA = {"genres": ["أطفال"], "unsupported_requests": ["الحيوانات"], "audience": "children"}
_r, _f, _g, _d, _c, _fx = run(["ابني صار عمره ١٢ ويحب كل شي عن الحيوانات"], prefs=PA)
ok(_f[:1] == [I[4]] and I[3] in _f, f"Q39c: a boy of 12 → «كتاب الغابة» (9–11) first, then the books with no recorded age ({ix(_f[:4])})")
ok(_d.get("age_first1115") == [I[4]], f"Q39c: «كتاب الغابة» (within a year) is kept for him and put first ({_d.get('age_first1115')})")
ok(I[6] not in _f[:3], f"Q39c negative: «6+» stays out for 12 (QADFIX34) ({ix(_f[:4])})")
_r, _f, _g, _d, _c, _fx = run(["بنت أختي عمرها ١٠ تحب قصص الحيوانات، وش عندكم؟"], prefs=PA)
ok(set(_f[:2]) == {I[4], I[6]} and set(_f[2:4]) == {I[3], I[5]},
   f"Q39c fresh: a girl of 10 → the books whose recorded age covers her («كتاب الغابة» 9–11, «6+») before those with none ({ix(_f[:4])})")
_r, _f, _g, _d, _c, _fx = run(["ولدي عمره ٧ يحب الحيوانات"], prefs=PA)
ok(not _d.get("age_first1115"), f"Q39c negative: a boy of 7 is not on this rule ({ix(_f[:4])})")

# Q38a (QAD 2026-11-13 43.1)
PC = {"genres": ["أطفال"], "unsupported_requests": ["القطط"], "audience": "children"}
_r, _f, _g, _d, _c, _fx = run(["قصة مصورة لبنت عمرها ثلاث تحب القطط"], prefs=PC)
_SM = {I[9], I[10], I[11], I[19]}
_two = lambda ids: (len(ids) == 2 and set(ids) <= _SM and not {I[9], I[10]} <= set(ids) and not {I[9], I[19]} <= set(ids))
ok(_two(_f) and set(_c) == set(_f),
   f"Q38a: nothing on cats for 3 → two books for her age (one per series and author, no set, none over 64 pages) ({ix(_f)}, cards {ix(_c)})")
ok("ليست عن «القطط»" in _g and "ولا تعرض أي كتاب" not in _g and "يناسب عمر ٣ سنوات" in _g,
   "Q38a: the guidance says none is on cats for her age, then the two, said as on other subjects")
ok(I[7] not in _f and I[8] not in _f and I[12] not in _f and I[13] not in _f, "Q38a: never the 9–12 / 672-page cat books, the set, the 120-page book")
_r, _f, _g, _d, _c, _fx = run(["ولدي عمره أربع سنين، عندكم شي عن الديناصورات؟"], prefs=dict(PC, unsupported_requests=["الديناصورات"]))
ok(_two(_f) and "الديناصورات" in _g, f"Q38a fresh: a boy of 4 and dinosaurs → two such books ({ix(_f)})")
_sm = getattr(A, "small_other1115", lambda *a, **k: [])
_p = [b["book_id"] for _w, b in _sm([b for b in books if b["book_id"] in (I[9], I[19], I[11])], {}, 3)]
ok(len(_p) == 2 and not {I[9], I[19]} <= set(_p), f"Q38a: two authors when the stock allows ({ix(_p)})")
_p = [b["book_id"] for _w, b in _sm([b for b in books if b["book_id"] in (I[9], I[19])], {}, 3)]
ok(set(_p) == {I[9], I[19]}, f"Q38a: … the same author when nothing else fits ({ix(_p)})")
ok(_sm([b for b in books if b["book_id"] in (I[9], I[11])], {}, 6) == [], "Q38a negative: a child of six is not on this rule")
_r, _f, _g, _d, _c, _fx = run(["بنتي عمرها ثمان تحب القطط"], prefs=PC)
ok(I[7] in _f and not _d.get("small_other1115"), f"Q38a negative: a girl of 8 → the cat book for her age, no other subject ({ix(_f[:3])})")
_r, _f, _g, _d, _c, _fx = run(["بنتي عمرها ثلاث تحب قصص السباحة"], prefs=dict(PC, unsupported_requests=["السباحة"]))
ok(_f[:1] == [I[9]] and not _d.get("small_other1115"), f"Q38a negative: a book on her subject for her age exists → it ({ix(_f[:3])})")

# Q38b (QAD 2026-11-13 23.1 / 27.1, 11-14 3.1)
_r, _f, _g, _d, _c, _fx = run(["عندكم كتب عن الفروسية والخيل العربية؟"], prefs={"unsupported_requests": ["الفروسية"]}, show=3)
_ar = [i for i in _f if i in (I[14], I[15], I[16])]
_one = next((f for f in _fx if f["id"] in (I[14], I[15], I[16])), {})
ok(len(_ar) == 1 and len([e for e in _one.get("other_editions") or [] if e.get("id") in (I[14], I[15], I[16])]) == 2,
   f"Q38b: the three bindings of «فروسية» → one fact, the two others under its other_editions ({ix(_f)})")
ok({e.get("price_sar") for e in _one.get("other_editions") or []} >= ({420.0, 770.0, 1300.0} - {next((b["price"] for b in books if b["book_id"] == _one.get("id")), 0)})
   and "اذكر طبعاته وسعر كل طبعة" in _g, "Q38b: each edition's price is in facts and the guidance asks for the editions with their prices")
ok(len([i for i in _c if i in (I[14], I[15], I[16])]) == 1, f"Q38b: one card for the work ({ix(_c)})")
ok(I[17] in _f or I[18] in _f, f"Q38b negative: another author's «فروسية» / the English copy are not taken in as its editions ({ix(_f)})")
_r, _f, _g, _d, _c, _fx = run(["عندكم فروسية؟ بكم؟"], intent="about_book", mentioned_titles=["فروسية"], show=3)
ok(len([i for i in _f if i in (I[14], I[15], I[16])]) == 1 and len([i for i in _c if i in (I[14], I[15], I[16])]) == 1,
   f"Q38b fresh: asked by name → one fact, one card, the editions under it ({ix(_f)}, cards {ix(_c)})")
_r, _f, _g, _d, _c, _fx = run(["أبي رواية عادية عن الحياة اليومية"], prefs={"genres": ["رواية"]}, show=3)
ok(len(_c) >= 2 and "طبعاته" not in _g, f"Q38b negative: different books stay separate cards, no editions line ({ix(_c)})")

# ---------------------------------------------------------------- SETS (2): the Rewaa import
for t, n in (("ذهب مع الريح (جزءان)", 2), ("الأنوار والمراقب (7 أجزاء)", 7), ("ديفيد كوبرفيلد (1-3)", 3), ("الأخوة كارامازوف 4/1", 4),
             ("مقدمة ابن خلدون 1/3", 3), ("Al Furusiyya (2 volumes)", 2), ("Magic Tree House 1-4 Treasury Boxed Set", 4)):
    d = _clear(t)
    ok(bool(d) and d["volumes"] == n, f"SETS2: «{t}» is plainly a set of {n} ({d})")
for t in ("الأعمال القصصية الكاملة", "موسوعة الفروق اللغوية (ج1-ج10)", "ثلاثية غرناطة", "دوريت الصغيرة (1: الفقر، 2: الغنى)",
          "The Camel through the Ages - with Box", "Absolute Batman Vol. 1", "موساشي (الجزء 1 من 7)", "سلسلة أنا والعالم"):
    ok(_clear(t) is None, f"SETS2 negative: «{t}» is not marked by the import")
_csv = ("اسم المنتج,الباركود,سعر البيع,الكمية المتوفرة,المؤلف\n"
        "ذهب مع الريح (جزءان) تجربة,,120,2,مارجريت ميتشل\n"
        "Magic Tree House 1-4 Treasury Boxed Set Trial,,200,1,Mary Pope Osborne\n"
        "الأخوة كارامازوف 4/1 تجربة,,300,1,دوستويفسكي\n"
        "الأعمال الكاملة تجربة,,90,1,يوسف إدريس\n"
        "موسوعة الفروق اللغوية (ج1-ج10) تجربة,,500,1,العسكري\n"
        "رواية عادية جدًا تجربة,,40,3,كاتب\n")
_up = R.store_upload("rewaa.csv", _csv.encode("utf-8-sig"))
_plan = R.build_plan(_up["upload_id"], _up["suggested"], "report")
_ps = {x["title"]: x.get("set") for x in _plan["new"]}
ok(_ps.get("ذهب مع الريح (جزءان) تجربة", {}) and _ps["ذهب مع الريح (جزءان) تجربة"]["volumes"] == 2
   and not _ps.get("الأعمال الكاملة تجربة") and not _ps.get("رواية عادية جدًا تجربة"), f"SETS2: the dry-run plan shows the set it will mark ({_ps})")
_res = R.apply_plan(_up["upload_id"], _up["suggested"], "report")
_con = sqlite3.connect(models.DB_PATH)
_rows = {t: (v, k, s) for t, v, k, s in _con.execute("SELECT title, volumes, set_kind, volumes_source FROM books WHERE title LIKE '%تجربة' OR title LIKE '%Trial'")}
ok(_rows.get("ذهب مع الريح (جزءان) تجربة") == (2, "one_work", "title") and _rows.get("الأخوة كارامازوف 4/1 تجربة") == (4, "one_work", "title")
   and _rows.get("Magic Tree House 1-4 Treasury Boxed Set Trial") == (4, "collection", "title"),
   f"SETS2: the trial import wrote volumes / set_kind / volumes_source='title' for the plain titles ({_rows})")
ok(_rows.get("الأعمال الكاملة تجربة") == (None, None, None) and _rows.get("موسوعة الفروق اللغوية (ج1-ج10) تجربة") == (None, None, None)
   and _rows.get("رواية عادية جدًا تجربة") == (None, None, None), "SETS2 negative: the others are not marked (staff set them)")
_det = json.loads(_con.execute("SELECT details FROM import_runs ORDER BY run_id DESC LIMIT 1").fetchone()[0])
ok(_res.get("sets_marked") == 3 and sum(1 for x in _det["new"] if x.get("set")) == 3, f"SETS2: the run says how many sets it marked ({_res.get('sets_marked')})")
_con.close()

# ---------------------------------------------------------------- apply_library_edits: decided here
spec = importlib.util.spec_from_file_location("ale1115", os.path.join(ROOT, "scripts", "apply_library_edits.py"))
ALE = importlib.util.module_from_spec(spec); spec.loader.exec_module(ALE)
con = sqlite3.connect(models.DB_PATH)
b1, b2, b3 = [r[0] for r in con.execute("SELECT book_id FROM books WHERE active = 1 ORDER BY book_id LIMIT 3")]
con.execute("UPDATE books SET original_year = NULL, isbn = NULL WHERE book_id IN (?, ?, ?)", (b1, b2, b3))
con.commit()
_lab = IE.FIELD_AR.get("original_year", "original_year")
_labi = IE.FIELD_AR.get("isbn", "isbn")
md = f"""## 5. تعديلات نفّذها الموظفون في المكتبة (3)

### 5.1 ★ تعديل بيانات كتاب — «a» (book_id {b1})
- بواسطة: layan · 2026-11-12 16:13
- {_lab}: كان (فارغ) · صار 2009

### 5.2 ★ تعديل بيانات كتاب — «b» (book_id {b2})
- بواسطة: yara · 2026-11-12 16:20
- {_lab}: كان (فارغ) · صار 1999

### 5.3 ★ تعديل بيانات كتاب — «c» (book_id {b3})
- بواسطة: ibrahim · 2026-11-12 16:30
- {_labi}: كان (فارغ) · صار 9780000000002

## 6. ملفات
"""
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "inbox.md"); open(path, "w", encoding="utf-8").write(md)
corr = os.path.join(tmp, "library_corrections.json")
json.dump({"items": [{"book_id": b1, "field": "original_year", "expect": 2009, "value": None, "source": "test"},
                     {"book_id": b3, "field": "isbn", "expect": "9780000000001", "value": None, "source": "test"}]},
          open(corr, "w", encoding="utf-8"))
_split = getattr(ALE, "split_decided", None)
_load = getattr(ALE, "load_corrections", lambda p: {})
a_, c_, al_, cv_ = ALE.plan(ALE.parse(path), con)
if _split:
    a2, c2, dec = _split(a_, c_, _load(corr))
else:
    a2, c2, dec = a_, c_, []
ok([(x[0], x[1]) for x in dec] == [(b1, "original_year")], f"EDITS: the year decided here (library still on «expect») is «decided», not to apply ({[(x[0], x[1]) for x in dec]})")
ok(sorted((x[0], x[1]) for x in a2) == [(b2, "original_year"), (b3, "isbn")],
   "EDITS negative: a field with no correction, and a correction whose «expect» is not the library's value, are still applied")
out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "apply_library_edits.py"), path, "--corrections", corr],
                     capture_output=True, text=True, cwd=os.getcwd(), env=dict(os.environ))
txt = out.stdout + out.stderr
ok(out.returncode == 0 and re.search(rf"DECIDED HERE \(library_corrections\) {b1} ", txt) and not re.search(rf"APPLY {b1} ", txt)
   and re.search(rf"APPLY {b2} ", txt) and "decided here: 1" in txt and "(dry run: nothing written)" in txt,
   f"EDITS: the dry run prints «DECIDED HERE (library_corrections)» for it, APPLY for the other ({txt[:300]!r})")
ok(con.execute("SELECT original_year FROM books WHERE book_id = ?", (b1,)).fetchone()[0] is None, "EDITS: the dry run wrote nothing")
con.close()

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
