"""[QADKWFIX] Replies with the model down (Ibrahim 2026-11-14 on Q-QADKW-1: «موافق عالتوصية في اصلاحات الردود للتحسين حالة تعطل النموذج»):
(1) follow-ups about the books just shown answered from their own records (backend/kw_followup.py; SETS: a set's page count is never one
    book's length, a collection has no single year);
(2) guarded matching: common words («أنا»، «أيام»، «بعد») are no title words, a prize named after a person is no author, a look-alike name
    («الغزالة / الغزالي»، «سعود المطيري / المسيري») is not that author; the model-free reply says what it understood («فهمت: …»);
(3) the wider keyword reader (keyword_prefs.understand(..., wide=True) = app.fallback_understand): subgenres, «ما تبي / بلا» negations,
    mood / length / lightness words, English prizes and screen words; the model path keeps app.local_understand unchanged;
(4) scripts/kw_score.py — the standing score (its compare on hand-made rows).
Wording of the QAD rounds 2026-11-13 / 2026-11-14 and fresh rewordings, with negatives."""
import json, os, re, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import keyword_prefs as K
import kw_followup as KF
import book_sets
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

W = A.fallback_understand
def got(q, key):
    return W([q] if isinstance(q, str) else q).get(key)

# ---------------------------------------------------------------- (3) the wider reader
ok(got("بنتي ١٥ سنة تحب الفانتازيا بس ما تبي رومانسية أبد", "subgenres") == ["فانتازيا"]
   and "romantic" in got("بنتي ١٥ سنة تحب الفانتازيا بس ما تبي رومانسية أبد", "moods_exclude")
   and "romantic" not in got("بنتي ١٥ سنة تحب الفانتازيا بس ما تبي رومانسية أبد", "moods_include"),
   "3 (17.1): «تحب الفانتازيا بس ما تبي رومانسية أبد» → fantasy, romance refused (not wanted)")
ok("romantic" in got("ابي رواية رومانسية", "moods_include") and "romantic" in got("ما يبي شي رومانسي", "moods_exclude")
   and "romantic" in got("بلاش رومانسية", "moods_exclude"), "3: «رواية رومانسية» wanted; «ما يبي / بلاش رومانسي» refused")
ok(got("فانتازيا", "subgenres") == ["فانتازيا"] and got("فانتازيا", "genres") == ["رواية"], "3: «فانتازيا» alone → a fantasy novel")
ok(got("sci-fi pls", "subgenres") == ["خيال علمي"] and "pls" not in got("sci-fi pls", "title_keywords"), "3: 'sci-fi pls' → science fiction")
ok(got("رواية خيال علمي", "subgenres") == ["خيال علمي"] and got("قصة مرعبة", "subgenres") == ["رعب"]
   and got("رواية بوليسية فيها تحقيق", "subgenres") == ["بوليسية"] and got("I'm looking for a creepy haunted house novel", "subgenres") == ["رعب"],
   "3: science fiction / horror / detective / 'creepy haunted house' read as kinds")
ok(got("ودي برواية تاريخية، المهم أسلوبها أدبي رفيع", "subgenres") == ["تاريخية"]
   and "تاريخ" not in got("ودي برواية تاريخية، المهم أسلوبها أدبي رفيع", "genres") and "تاريخ" in got("كتاب تاريخ الأندلس", "genres"),
   "3 (11-14 1.1): «رواية تاريخية» is a kind of novel, not the history shelf; «كتاب تاريخ» still is")
ok(got("ولدي ١٣ سنة يبي رواية مغامرات، بس بدون مشاهد للكبار", "avoid_content") == ["sexual_content"]
   and got("ولدي ١٣ سنة يبي رواية مغامرات، بس بدون مشاهد للكبار", "subgenres") == ["مغامرات"],
   "3: «مغامرات … بدون مشاهد للكبار» → adventure, no adult scenes")
ok("violence" in got("قصة مرعبة تقعد في بالي أيام، بس بلا مشاهد دموية", "avoid_content")
   and "ايام" not in got("قصة مرعبة تقعد في بالي أيام، بس بلا مشاهد دموية", "title_keywords"),
   "3 (39.1): «بلا مشاهد دموية» refuses blood; «أيام» is no title word")
ok("violence" in got("رواية غموض ما فيها جرائم قتل ولا دم", "avoid_content"), "3: «ما فيها جرائم قتل ولا دم» refuses violence")
ok(got("اليوم نفسيتي زفت، بغيت كتاب يخليني أضحك من قلب", "moods_include") == ["funny"]
   and "قلب" not in got("اليوم نفسيتي زفت، بغيت كتاب يخليني أضحك من قلب", "title_keywords"),
   "3 (14.1): «يخليني أضحك من قلب» → funny («قلب» no title word)")
ok(got("قلبي مكسور وبدي كتاب يواسيني", "moods_include") == ["comforting"] and got("ابي شي يرفع المعنويات", "moods_include") == ["uplifting"],
   "3: «يواسيني» comforting, «يرفع المعنويات» uplifting")
ok(got("ابي شي يونّس وايد، ماله خص بالحزن", "moods_exclude") == ["sad"] and "sad" not in got("ابي شي يونّس وايد، ماله خص بالحزن", "moods_include"),
   "3: «ماله خص بالحزن» refuses sadness")
ok(got("ابي شي أخف وأقصر", "length") == "short" and got("ابي شي أخف وأقصر", "weight_include") == ["light"], "3: «أخف وأقصر» → short, light")
ok(got("ابي رواية تنقرى بجلسة وحدة", "length") == "short" and "quick_read" in got("ابي رواية تنقرى بجلسة وحدة", "use_cases"),
   "3: «تنقرى بجلسة وحدة» → short, a quick read")
ok(got("أيهم أقصر؟", "length") is None and got("Which one is the shortest?", "length") is None and got("ابي رواية أقصر", "length") == "short",
   "3: «أيهم أقصر؟» is an order among the books shown, not a wish for a short book; «رواية أقصر» is")
ok(got("أقصد مجموعات قصص قصيرة، مو روايات", "length") is None and "قصص قصيرة" in got("أقصد مجموعات قصص قصيرة، مو روايات", "genres"),
   "3: «قصص قصيرة» is the short-stories shelf, not «short»")
ok(got("any Pulitzer winners?", "awards") == ["pulitzer"] and got("a pulitzer winning novel please", "awards") == ["pulitzer"],
   "3: 'any Pulitzer winners?' → the Pulitzer")
ok(got("anything shortlisted for the International Booker?", "awards") == ["intl_booker"]
   and got("ميدالية نجيب محفوظ، وش عندكم من الروايات اللي أخذتها؟", "awards") == ["mahfouz"]
   and not ({"نجيب", "محفوظ", "ميداليه"} & set(got("ميدالية نجيب محفوظ، وش عندكم من الروايات اللي أخذتها؟", "title_keywords"))),
   "3 (25.1): the International Booker; «ميدالية نجيب محفوظ» is the prize (its words no title or author words)")
ok(got("a novel that was made into a movie", "adaptation") == "film" and got("any book that became a tv series?", "adaptation") == "series"
   and got("not part of a series", "adaptation") is None, "3: 'made into a movie' / 'tv series' → screen; 'part of a series' is not")
ok(not got("عندكم مئة عام من العزلة بالانجليزي؟", "original_languages") and not got("popular science book about the brain? english or arabic both fine", "original_languages")
   and not got("أبي كتاب عن الفروسية والخيل العربية", "original_languages") and got("رواية عربية", "original_languages") == ["ar"]
   and got("رواية روسية", "original_languages") == ["ru"] and got("an English novel", "original_languages") == ["en"],
   "3: «بالإنجليزي» / 'english or arabic' / «الخيل العربية» are no original language; «رواية عربية / روسية», 'an English novel' are")
_t = got("زين، وأنا جدتها ودي برواية لي عن الحرب العالمية", "title_keywords")
ok("وانا" not in _t and "جدتها" not in _t and {"الحرب", "العالميه"} <= set(_t), f"2 (2.2): «وأنا جدتها» are no title words ({_t})")
ok("ايام" not in got("ودي برواية خليجية عن أيام ما قبل النفط", "title_keywords") and "بعد" not in got("روايات سعودية طلعت بعد ٢٠٢٠", "title_keywords")
   and "النفط" in got("ودي برواية خليجية عن أيام ما قبل النفط", "title_keywords"), "2 (38.1 / 32.1): «أيام» / «بعد» no title words, «النفط» kept")
ok(W(["رواية فانتازيا", "لا، خلها خيال علمي"])["subgenres"] == ["خيال علمي"] and W(["رواية فانتازيا", "شي ثاني"])["subgenres"] == ["فانتازيا"],
   "3: a later kind replaces the earlier one; «شي ثاني» keeps it")
# the model path's reader is unchanged
_old = A.local_understand(["بنتي ١٥ سنة تحب الفانتازيا بس ما تبي رومانسية أبد"])
ok("subgenres" not in _old and "romantic" in _old["moods_include"] and "الفانتازيا" in _old["title_keywords"],
   "3: app.local_understand (the model path's helper) reads as before (no subgenre field, the old reading kept)")
ok(A.local_understand(["رواية غموض"])["moods_include"] == ["mysterious"] and A.local_understand(["وش أقصر واحد فيهم؟"])["length"] is None
   and "تاريخ" in A.local_understand(["رواية تاريخية"])["genres"], "3: local_understand: «غموض» a mood, «أقصر» no length, «تاريخية» the history shelf, as before")
ok(K.subgenre_families("رواية بوليسية") == {"mystery"} and K.subgenre_families("جريمة") == {"mystery"} and K.subgenre_families("غموض أدبي") == {"mystery"}
   and K.subgenre_families("الخيال العلمي") == {"scifi"} and K.subgenre_families("رواية تاريخية") == {"historical"} and K.subgenre_families("رحلات") == set(),
   "4: kinds compared by family («بوليسية» = «جريمة» = «غموض»)")

# ---------------------------------------------------------------- (1) the follow-up questions
for q, want in (("أيهم أقصر؟", ("order", "shortest")), ("وش أقصر واحد فيهم؟", ("order", "shortest")), ("أقصرهن؟", ("order", "shortest")),
                ("وش أقصر واحد له؟", ("order", "shortest")), ("which one is the shortest?", ("order", "shortest")),
                ("which of them is shorter?", ("order", "shortest")), ("أيهم أرخص؟", ("order", "cheapest")), ("وأغلاها؟", ("order", "dearest")),
                ("أقصر؟", ("both", "shortest")), ("أرخص؟", ("both", "cheapest")), ("أقصر من كذا؟", ("more", "shortest")),
                ("فيه شي أقصر؟", ("more", "shortest")), ("وأرخص من كذا؟", ("more", "cheapest")), ("anything shorter?", ("more", "shortest")),
                ("كم صفحة؟", ("facts", ["pages"])), ("كم سعرها؟", ("facts", ["price"])), ("متى صدر؟", ("facts", ["year"])),
                ("مين المؤلف؟", ("facts", ["author"])), ("متوفر؟", ("facts", ["available"])), ("is it long?", ("facts", ["pages"])),
                ("how much is it?", ("facts", ["price"])), ("who wrote it?", ("facts", ["author"])), ("كم سعرها وكم صفحاتها؟", ("facts", ["pages", "price"])),
                ("حصار الغزالة واو عن إيش؟ وكم صفحة؟", ("facts", ["about", "pages"]))):
    ok(KF.classify(q) == want, f"1: «{q}» → {want} ({KF.classify(q)})")
for q in ("ابي رواية", "وش أقصر رواية عندكم أصلاً؟", "رواية رعب", "شي ثاني", "عندكم دوستويفسكي؟"):
    ok(KF.classify(q) is None, f"1: «{q}» is not a question about the books shown")
_set = {"title": "فروسية (جزءان)", "page_count": 900, "price": 420.0, "volumes": 2, "set_kind": "one_work", "book_id": 1}
_col = {"title": "مجموعة روايات", "page_count": 1200, "price": 145.7, "volumes": 6, "set_kind": "collection", "book_id": 2, "original_year": 1950}
_one = {"title": "رواية", "page_count": 120, "price": 30.0, "book_id": 3, "original_year": 1967, "publication_year": 2017, "author": "كاتب"}
book_sets.chat_view([_set, _col, _one])
_l = KF.fact_line(_set, "pages")
ok("في جزأين" in _l and "ما عندي عدد صفحات" in _l and "٩٠٠" not in _l, f"1 SETS: a set's pages are never one book's length ({_l})")
ok("مجموعة" in KF.fact_line(_col, "year", first_year=A.first_published, edition_year=A.edition_year) and "١٩٥٠" not in
   KF.fact_line(_col, "year", first_year=A.first_published, edition_year=A.edition_year), "1 SETS: a collection has no single first year")
_ol, _ord = KF.order_line([_set, _one, _col], "shortest")
ok(_ord[0] is _one and "فما أقارن صفحاتها" in _ol and "٩٠٠" not in _ol, f"1 SETS: sets are not ranked by pages ({_ol})")
ok(KF.fmt_price(48.39) == "٤٨٫٣٩ ريال" and KF.fmt_price(30) == "٣٠ ريال" and KF.fmt_price(5.5, en=True) == "5.50 SAR", "1: prices said as the record has them")
ok(KF.fact_line(_one, "year", first_year=A.first_published, edition_year=A.edition_year) == "«رواية» صدر أول مرة سنة ١٩٦٧ (طبعتنا سنة ٢٠١٧)",
   "1: «متى صدر؟» → the first year and our edition's")

# ---------------------------------------------------------------- whole path on synthetic books (the model off)
base = [dict(b) for b in A.list_books(only_active=False)][:60]
A.get_profile_cards = lambda: {}
A.embeddings.embed = lambda *a, **k: None
A.embeddings.search = lambda *a, **k: {}
A.embeddings.similar_to = lambda *a, **k: []
A.embeddings.similar_to_book = lambda *a, **k: []
A.llm.complete_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected live-model call"))
SPEC = [
    dict(title="رواية طويلة", page_count=480, price=60.0, original_year=1990),                                     # 0
    dict(title="رواية متوسطة", page_count=260, price=45.5, original_year=2005),                                    # 1
    dict(title="رواية قصيرة جدا", page_count=110, price=22.0, original_year=1961, publication_year=2019),         # 2
    dict(title="الأنوار والمراقب (جزءان)", page_count=2120, price=150.0, volumes=2, set_kind="one_work"),            # 3 set
    dict(title="رواية أقصر منها", page_count=90, price=18.0),                                                       # 4
    dict(title="رواية أرخص منها", page_count=300, price=9.5),                                                       # 5
    dict(title="أنا والعالم", genre="أطفال", page_count=24, summary="كتاب مصور للصغار"),                            # 6
    dict(title="أيام الطفولة", page_count=200, summary="سيرة"),                                                      # 7
    dict(title="الحرب العالمية في رواية", page_count=350, summary="رواية عن الحرب العالمية الثانية في أوروبا"),        # 8
    dict(title="عالم التنانين", subgenres='["فانتازيا"]', page_count=300, summary="ممالك وسحر"),                        # 9
    dict(title="حب في مملكة السحر", subgenres='["فانتازيا", "رومانسية"]', page_count=280, summary="قصة حب وسحر"),        # 10
    dict(title="الجريمة والعقاب", author="فيودور دوستويفسكي", page_count=852, price=129.25),                          # 11
    dict(title="الطنطورية", author="رضوى عاشور", page_count=463, price=48.39, original_year=2010, publication_year=2022),  # 12
    dict(title="بعد الغروب", page_count=180, publication_year=2015),                                               # 13
    dict(title="رواية جديدة", page_count=220, publication_year=2023, original_year=2023),                            # 14
    dict(title="صرصار الليل على الموقد", author="تشارلز ديكنز", page_count=143),                                    # 15
    dict(title="الآمال الكبرى", author="تشارلز ديكنز", page_count=620),                                             # 16
    dict(title="أغنية عيد الميلاد", author="تشارلز ديكنز", page_count=120),                                         # 17
    dict(title="الثلاثية", author="نجيب محفوظ", page_count=900),                                                     # 18
    dict(title="اللص والكلاب", author="نجيب محفوظ", page_count=129),                                                 # 19
] + [dict(title=f"رواية عادية {n}", summary="رواية عن الحياة اليومية") for n in range(30)]
books = []
for b, sp in zip([dict(x) for x in base], SPEC):
    b.update(quantity=3, active=1, author="كاتب " + str(b["book_id"]), original_year=None, summary="", hook="", keywords="", subjects="",
             subgenres=None, series_name=None, series_position=None, language="ar", age_min=None, age_max=None, original_language=None,
             page_count=200, price=40.0, genre="رواية", publication_year=2010, setting=None, volumes=None, set_kind=None)
    b.update(sp)
    books.append(b)
assert len(books) == len(SPEC), (len(books), len(SPEC))
A.list_books = lambda *a, **k: [dict(x) for x in books]
I = {k: books[k]["book_id"] for k in range(len(SPEC))}
_REV = {v: k for k, v in I.items()}
ix = lambda ids: [_REV.get(i, i) for i in ids]

def chat(*turns, language="ar"):
    m = [{"role": "user", "content": x} if isinstance(x, str) else x for x in turns]
    r = A._run_chat_once(m, active=None, language=language)
    return r, [b["book_id"] for b in r.get("books") or []], (r.get("_debug") or {})

def shown(*ks, text="هذي الكتب"):
    return {"role": "assistant", "content": text, "book_ids": [I[k] for k in ks]}

_r, _b, _d = chat("ابي رواية", shown(0, 1, 2), "أيهم أقصر؟")
ok(_r["understanding"] == "keywords" and _r["reply"].startswith("الأقصر بين الكتب اللي عرضتها «رواية قصيرة جدا» (١١٠ صفحة)")
   and ix(_b) == [2, 1, 0], f"1 (4.2): «أيهم أقصر؟» → the shown books from the shortest ({_r['reply'][:90]}; {ix(_b)})")
_r, _b, _d = chat("ابي رواية", shown(0, 1, 2), "وش أقصر واحد فيهم؟")
ok(ix(_b)[:1] == [2] and _d.get("why") == "follow-up order", "1: «وش أقصر واحد فيهم؟» → the same")
_r, _b, _d = chat("ابي رواية", shown(0, 3, 1), "أيهم أقصر؟")
ok(ix(_b)[:2] == [1, 0] and "٢١٢٠" not in _r["reply"] and "في جزأين فما أقارن صفحاتها" in _r["reply"],
   f"1 SETS: a set shown is never ranked by its page count ({_r['reply'][:160]})")
_r, _b, _d = chat("ابي رواية", shown(3), "كم صفحة؟")
ok("في جزأين" in _r["reply"] and "٢١٢٠" not in _r["reply"] and "ما عندي عدد صفحات" in _r["reply"], f"1 SETS: «كم صفحة؟» of a set ({_r['reply']})")
_r, _b, _d = chat("ابي رواية", shown(12), "كم سعرها وكم صفحاتها؟")
ok("٤٨٫٣٩ ريال" in _r["reply"] and "٤٦٣ صفحة" in _r["reply"] and ix(_b) == [12], f"1 (11-14 20.2): price and pages from the record ({_r['reply']})")
_r, _b, _d = chat("ابي رواية", shown(12), "متى صدرت؟")
ok("٢٠١٠" in _r["reply"] and "٢٠٢٢" in _r["reply"], f"1: «متى صدرت؟» → 2010 (our edition 2022) ({_r['reply']})")
_r, _b, _d = chat("ابي رواية", shown(12), "مين المؤلف؟")
ok("رضوى عاشور" in _r["reply"], f"1: «مين المؤلف؟» ({_r['reply']})")
_r, _b, _d = chat("ابي رواية", shown(12), "متوفر؟")
ok("متوفر عندنا الآن" in _r["reply"], f"1: «متوفر؟» ({_r['reply']})")
_r, _b, _d = chat("ابي رواية", shown(11, 0), "طيب كم سعر كل وحدة؟")
ok("١٢٩٫٢٥ ريال" in _r["reply"] and "٦٠ ريال" in _r["reply"] and set(ix(_b)) == {11, 0}, f"1 (10.2): «كم سعر كل وحدة؟» → each ({_r['reply']})")
_r, _b, _d = chat("ابي رواية", shown(11, 0), "وكم صفحة الأولى؟")
ok(ix(_b) == [11] and "٨٥٢" in _r["reply"], f"1 (11.2): «الأولى» is the first shown ({_r['reply']})")
_r, _b, _d = chat("ابي رواية", shown(11, 12), "الطنطورية كم صفحة؟")
ok(ix(_b) == [12] and "٤٦٣" in _r["reply"], f"1: a shown title named → that book only ({_r['reply']})")
_r, _b, _d = chat("ابي رواية", shown(0, 1, 2), "أقصر من كذا؟")
ok(_b and all(next(x for x in books if x["book_id"] == i)["page_count"] < 110 for i in _b) and not set(ix(_b)) & {0, 1, 2}
   and "أقصر من «رواية قصيرة جدا» (١١٠ صفحة)" in _r["reply"], f"1 (19.2): «أقصر من كذا؟» → new books under 110 pages ({ix(_b)}; {_r['reply'][:120]})")
_r, _b, _d = chat("ابي رواية", shown(0, 1, 2), "أقصر؟")
ok(_r["reply"].startswith("الأقصر بين الكتب اللي عرضتها «رواية قصيرة جدا»") and _b and not set(ix(_b)) & {0, 1, 2},
   f"1: a bare «أقصر؟» → the order line and shorter books ({_r['reply'][:120]}; {ix(_b)})")
_r, _b, _d = chat("ابي رواية", shown(0, 1, 2), "فيه شي أرخص؟")
ok(_b and all(next(x for x in books if x["book_id"] == i)["price"] < 22.0 for i in _b), f"1 (49.2): «فيه شي أرخص؟» → under 22 SAR ({ix(_b)})")
_r, _b, _d = chat("ابي شي لتشارلز ديكنز", shown(15, 16, 17), "أقصر من كذا؟")
ok(not _b and "لنفس المؤلف" in _r["reply"], f"1: the books shown are one author's → shorter books of his only, none here ({_r['reply']})")
_r, _b, _d = chat("ابي رواية", shown(0, 1, 2), "which one is the shortest?", language="en")
ok(_r["reply"].startswith("The shortest of the books I showed is «رواية قصيرة جدا» (110 pages)"), f"1 en: ({_r['reply'][:90]})")
_r, _b, _d = chat("ابي رواية", shown(0), "is it long?", language="en")
ok("about 480 pages" in _r["reply"], f"1 en: 'is it long?' ({_r['reply']})")
_r, _b, _d = chat("ابي رواية", shown(0, 1), "كم سعر رواية الحرب العالمية في رواية الجديدة؟")
ok(_d.get("why") not in ("follow-up facts", "follow-up order"), "1 negative: a question naming a book not shown is not about the shown ones")
_r, _b, _d = chat("ابي رواية", shown(0, 1), "رواية فانتازيا")
ok(_d.get("why") not in ("follow-up facts", "follow-up order", "follow-up more"), "1 negative: a new wish after books is a new search")

# ---------------------------------------------------------------- (2) guards and «فهمت»
_r, _b, _d = chat("أدور قصة لحفيدتي عمرها ثمان سنين", shown(6), "زين، وأنا جدتها ودي برواية لي عن الحرب العالمية")
ok(I[6] not in _b and I[8] in _b[:2] and "فهمت" in _r["reply"], f"2 (2.2): the grandmother's own WWII novel, no «أنا …» picture book ({ix(_b)}; {_r['reply'][:60]})")
_r, _b, _d = chat("ودي برواية عن أيام زمان")
# (QADKWFIX2: the common words are kept apart as "title_common" by design; the novels then tie and come in a random order, so
# «أيام الطفولة» may come first by chance — the check is that «أيام» is no title word; it failed once in about 70 runs on 2026-11-15)
ok(I[7] not in _b[:1] or "ايام" not in ((_d.get("understood") or {}).get("title_keywords") or []),
   f"2 (38.1): «أيام» alone picks no title ({ix(_b)})")
_r, _b, _d = chat("روايات طلعت بعد ٢٠٢٠")
ok(_b and I[13] not in _b and all((next(x for x in books if x["book_id"] == i).get("original_year") or
                                    next(x for x in books if x["book_id"] == i)["publication_year"]) >= 2021 for i in _b)
   and "صادرة من ٢٠٢١" in _r["reply"], f"2 (32.1): «بعد ٢٠٢٠» is the publication years, not «بعد الغروب» ({ix(_b)}; {_r['reply'][:60]})")
_r, _b, _d = chat("فانتازيا")
ok(ix(_b)[:2] and set(ix(_b)[:2]) <= {9, 10} and _r["reply"].startswith("فهمت: رواية، فانتازيا."), f"3 (35-type): «فانتازيا» → the fantasy books, said ({ix(_b)}; {_r['reply'][:50]})")
_r, _b, _d = chat("رواية فانتازيا بس ما تبي رومانسية أبد")
ok(I[9] in _b and I[10] not in _b and "بدون رومانسية" in _r["reply"], f"3 (17.1): romance refused → not the fantasy romance ({ix(_b)}; {_r['reply'][:70]})")
_r, _b, _d = chat("sci-fi pls", language="en")
ok(_r["reply"].startswith("Understood: novel, science fiction.") and "خيال" not in _r["reply"], f"2 en: the line in English ({_r['reply'][:60]})")
_r, _b, _d = chat("دوستويفسكي الجريمة والعقاب، تقريبًا كم صفحة النسخة اللي عندكم؟")
ok(ix(_b) == [11] and "٨٥٢" in _r["reply"], f"2 (10.1): a title of ours in a pages question → its record, not the author's other books ({_r['reply']})")
_r, _b, _d = chat("متوفرة عندكم الطنطورية؟")
ok(ix(_b) == [12] and "متوفر عندنا الآن" in _r["reply"], f"2: «متوفرة عندكم الطنطورية؟» → that book ({_r['reply']})")
_r, _b, _d = chat("ابغى رواية قصيرة لتشارلز ديكنز")
ok(ix(_b)[:2] == [17, 15] and I[16] not in _b and "القصيرة" in _r["reply"], f"2 (11.1): his short books, the shortest first ({ix(_b)})")
_r, _b, _d = chat("عندكم كتاب «حكايات الجن في نجد» لسعود المطيري؟")
ok(not _b and "«حكايات الجن في نجد»" in _r["reply"] and _d.get("why") != "author", f"2 (23.1): a title asked by name and not found is said ({_r['reply']})")
_r, _b, _d = chat("ميدالية نجيب محفوظ، وش عندكم من الروايات اللي أخذتها؟")
ok(_d.get("why") != "author" and "هذي كتب «نجيب محفوظ»" not in _r["reply"], f"2 (25.1): the Mahfouz medal is not Mahfouz's books ({_r['reply'][:80]})")
_r, _b, _d = chat("عندكم روايات نجيب محفوظ؟")
ok(_d.get("why") == "author" and {I[18], I[19]} <= set(_b), "2 negative: «روايات نجيب محفوظ» is still his books")

# found on the model-off replays of 2026-11-13 / 2026-11-14
ok(got("هو عمره ١٢ مو صغير، أبي شي يناسب سنه", "length") is None and got("أخوي الصغير بأول ثانوي", "length") is None
   and got("ودي أبدأ مشروع صغير", "length") is None and got("ابي كتاب صغير", "length") == "short",
   "3 (11-14 12.2 / 37.1 / 46.1): «مو صغير / أخوي الصغير / مشروع صغير» are no length; «كتاب صغير» is short")
ok(not got("أبي كتاب عن الفروسية والخيل العربية", "original_languages") and got("رواية فرنسية", "original_languages") == ["fr"],
   "3 (27.1): «الفروسية» is not «و + روسية»")
ok("شعر" not in got("ودّي أتعلم الخط الديواني، فيه كتاب يعلمني؟", "genres"), "3 (6.1): «الخط الديواني» is calligraphy, not poetry")
ok(got("طفلتي سنتين ونص، أبي كتاب ورقه كرتون سميك ما ينشق", "adaptation") is None and got("قصص صارت كرتون", "adaptation") == "animated",
   "3 (11-14 36.1): «ورقه كرتون سميك» is board pages, not a cartoon")
ok(KF.classify("وأيهم أقصر؟") == ("order", "shortest") and KF.classify("كم كتاب فيها؟") == ("facts", ["volumes"]),
   "1: «وأيهم أقصر؟» an order; «كم كتاب فيها؟» the record's count")
ok("مجموعة من" in KF.fact_line(_col, "volumes") and "كتاب واحد" in KF.fact_line(_one, "volumes"), "1 SETS: «كم كتاب فيها؟» → the set's label")
_r, _b, _d = chat("أدور قصة لحفيدتي عمرها ثمان سنين", shown(6), "زين، وأنا جدتها ودي برواية لي عن الحرب العالمية", shown(8),
                  "أقصد الحرب العالمية الثانية في أوروبا")
ok(not [i for i in _b if next(x for x in books if x["book_id"] == i)["genre"] == "أطفال"] and "للأطفال" not in _r["reply"],
   f"2 (2.3): the grandmother is still the reader on the next message ({ix(_b)}; {_r['reply'][:50]})")
_r, _b, _d = chat("شلونكم، أبي كتاب يعلمني الطبخ الكويتي")
ok("كاتب من" not in _r["reply"], "2 (11-14 38.1): «الطبخ الكويتي» is no Kuwaiti author")
_r, _b, _d = chat("ابي رواية", shown(0, 1, 2), "طيب بالإنجليزي؟")
ok("بالإنجليزية" in _r["reply"], f"3: «طيب بالإنجليزي؟» → the copy's language said ({_r['reply'][:70]})")
_r, _b, _d = chat("عندكم شي لتشارلز ديكنز؟", shown(16, 15, 17), "شي قصير له")
ok(ix(_b)[:2] == [17, 15], f"2: «شي قصير له» → his short books (the author named before) ({ix(_b)})")
_ab2 = {b["book_id"]: dict(b) for b in books}
ok([b["book_id"] for b in A._fb_titles_named("كم سعر الأنوار والمراقب؟", _ab2, one_word=True)] == [I[3]],
   "2 (27.2): «الأنوار والمراقب» names «الأنوار والمراقب (جزءان)»")

# the look-alike names (the author index replaced for these checks)
_keep = (A.authors._index, A.authors.lookup, getattr(A.authors, "lookup_id", None), A.authors.book_ids)
_recs = {99: {"author_id": 99, "name_ar": "أبو حامد الغزالي", "name_latin": None, "aliases": []},
         98: {"author_id": 98, "name_ar": "عبد الوهاب المسيري", "name_latin": None, "aliases": []},
         97: {"author_id": 97, "name_ar": "فيودور دوستويفسكي", "name_latin": None, "aliases": ["دوستويفسكي"]}}
A.authors._index = lambda conn: {A.authors.key("الغزالي"): 99, A.authors.key("المسيري"): 98, A.authors.key("دوستويفسكي"): 97}
A.authors.lookup = lambda *a, **k: None
A.authors.lookup_id = lambda i: _recs.get(i)
A.authors.book_ids = lambda *a, **k: set()
_ab = {b["book_id"]: dict(b) for b in books}
_ab[I[0]]["title"] = "حصار الغزالة واو"
A._fb_title_cache.update(sig=None)
ok(A.fallback_author("حصار الغزالة واو عن إيش؟ وكم صفحة؟", _ab) is None, "2 (3.2): «الغزالة» in a title of ours is not al-Ghazali")
ok(A.fallback_author("عندكم شي عن الغزالة؟", _ab) is None, "2: «الغزالة» / «الغزالي» differ in the last letter only: another word")
ok(A.fallback_author("لسعود المطيري", _ab) is None, "2 (23.1): «سعود المطيري» is not «عبد الوهاب المسيري»")
ok((A.fallback_author("كتب المسيري", _ab) or ("",))[0] == "عبد الوهاب المسيري", "2 negative: «المسيري» itself is his")
ok((A.fallback_author("ابي شي لدوستوفيسكي", _ab) or ("",))[0] == "فيودور دوستويفسكي", "2 negative: «دوستوفيسكي» (a slip) is still Dostoevsky")
A.authors._index, A.authors.lookup, A.authors.lookup_id, A.authors.book_ids = _keep
A._fb_title_cache.update(sig=None)
ok(A._fb_name_close("تشارلز دكنز", {"name_ar": "تشارلز ديكنز", "aliases": []}) and not A._fb_name_close("سعود المطيري", _recs[98]),
   "2: a full name one letter off is the author; another given name is not")
ok(A._fb_award_lead(" ميداليه نجيب محفوظ وش عندكم ", A.authors.key("نجيب محفوظ")) and not A._fb_award_lead(" روايات نجيب محفوظ ", A.authors.key("نجيب محفوظ")),
   "2: a name right after «ميدالية / جائزة» is a prize")

# ---------------------------------------------------------------- (4) the standing score's compare
sys.path.insert(0, os.path.join(os.path.dirname(os.getcwd()), "scripts"))
try:
    import kw_score as S
    ok(S.compare({"subgenres": ["رواية بوليسية"]}, {"subgenres": ["جريمة"]}, "subgenres", K) == "ok"
       and S.compare({"subgenres": ["فانتازيا"]}, {"subgenres": []}, "subgenres", K) == "missed"
       and S.compare({"subgenres": []}, {"subgenres": ["رعب"]}, "subgenres", K) == "extra"
       and S.compare({"genres": ["رواية", "يافعين"]}, {"genres": ["يافعين"]}, "genres", K) == "ok"
       and S.compare({"audience": "adult"}, {"audience": None}, "audience", K) is None,
       "4: kw_score compares as report.md (families for kinds, «رواية» dropped beside a precise genre, adult audience not counted)")
    _rows = [{"round": "x", "n": 1, "k": 1, "lang": "ar", "r": {f: None for f in S.ALL} | {"genres": "ok", "length": "missed"}},
             {"round": "x", "n": 2, "k": 1, "lang": "ar", "r": {f: None for f in S.ALL} | {"genres": "ok"}},
             {"round": "x", "n": 3, "k": 2, "lang": "en", "r": {f: None for f in S.ALL} | {"genres": "missed"}}]
    _s = S.summary(_rows)
    ok(_s["with_wish"] == 3 and _s["complete"] == 1 and _s["partly_pct"] == 67 and _s["nothing_pct"] == 33
       and S.line("x", _s).startswith("KW score x: complete 33% (1/3 turns the model read; shared fields)"),
       f"4: complete / partly / nothing per turn, one line for the QAD report ({S.line('x', _s)[:80]})")
except ImportError as e:
    ok(False, f"4: scripts/kw_score.py imports ({e})")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
