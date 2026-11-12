"""[QADFIX35] The errors of the QAD round 2026-11-12 (Ibrahim's standing word 2026-11-06: QAD fixes go ahead, doubtful ones are listed):
grave 28.1–28.3 («عندكم كتب فازت بجائزة غونكور الفرنسية؟ ابي أقدمها» → the oldest by the year of the prize, over every winner in stock),
12.2 («طيب وأنا جده، ابي رواية لي عن الريف والفلاحين» → the grandfather himself is the reader); medium 3.1–3.2 (the whole Zayed list,
the seventh winner in facts, no «ما عندي تفاصيل السابع»), 7.1 (parents of a middle-schooler reading it themselves), 26.1 («نساء» inside
an out-of-stock title is no wish for women authors), 47.1 («خط الرقعة» is Arabic calligraphy); minor 8.2 (the children's path for a
reader of 12), 20.1 («الروائي الكولومبي ماركس، صاحب العزلة»), 23.1 («شرق المتوسط» only the head of a longer title), 24.1 (the same work
in another form when the copy asked is out of stock). Each check uses the round's wording and a fresh rewording, with negatives."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import chat_rules as R
import topics as T
import author_flow as AF
import honors
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()

# ---------------------------------------------------------------- word readers
Q281 = "عندكم كتب فازت بجائزة غونكور الفرنسية؟ ابي أقدمها"
Q282 = "يعني ما عندكم ولا وحدة فازت بالغونكور؟"
Q283 = "متأكد إنها أقدم وحدة؟"
ok(R.oldest_asked35(Q281) and R.oldest_asked35(Q283), "28.1 / 28.3: «ابي أقدمها» and «أقدم وحدة» ask for the oldest")
ok(R.oldest_asked35("وش أول رواية فازت بالبوكر عندكم؟") and R.oldest_asked35("which is the oldest Pulitzer winner you have?")
   and R.oldest_asked35("الأقدم فوزًا لو سمحت"), "fresh: «أول رواية فازت», \"oldest winner\", «الأقدم فوزًا»")
ok(not R.oldest_asked35("وش أحدث فائزة بالغونكور؟") and not R.oldest_asked35("أبي رواية فازت بجائزة") and not R.oldest_asked35("كتاب أول مرة أقرأ"),
   "«أحدث فائزة» / a plain prize wish ask for no oldest")
ok(R.oldest_prize_kept35([Q281, Q282]), "28.2: «يعني ما عندكم ولا وحدة فازت؟» right after «أقدمها» keeps the oldest")
ok(not R.oldest_prize_kept35([Q281, "كم سعرها؟"]) and not R.oldest_prize_kept35([Q281, "وأرخص وحدة فازت؟"])
   and not R.oldest_prize_kept35(["عندكم كتب فازت بالغونكور؟", Q282]), "a price question, another order, or no oldest before: not kept")

Q121 = "حفيدي بالرابع ابتدائي سألني ليش القمر يتغير شكله، عندكم كتاب يفهمه؟"
Q122 = "طيب وأنا جده، ابي رواية لي عن الريف والفلاحين"
ok(R.self_said(Q122) and R.kin_self_said35(Q122), "12.2: «وأنا جده، ابي رواية لي …» is the visitor himself")
ok(R.self_said("زين وأنا خالته، ودي برواية لي عن البحر") and R.self_said("وأنا أمها، أبي كتاب لنفسي عن الطبخ"),
   "fresh: «وأنا خالته … لي», «وأنا أمها … لنفسي»")
ok(not R.self_said("وأنا جده، ابي له رواية عن الفضاء") and not R.kin_self_said35("وأنا جده، رشحي لي شي يناسبه")
   and not R.self_said("وأنا جده، ابي قصة لحفيدي"), "«… له» / «يناسبه» / «لحفيدي»: still the child")
ok(A.said_for_adult([Q121, Q122]), "12.2: said_for_adult reads the switch to the grandfather")
ok(not A.said_for_adult([Q121, "طيب وأنا جده، وش يناسبه بعد؟"]), "«وش يناسبه بعد؟» stays with the grandson")
ok(len(T.content_words("حياة الفلاحين")) == 1 and len(T.content_words("حياة الصيادين")) == 1, "12.2: «حياة الفلاحين» = «الفلاحين» (the frame)")
ok(len(T.content_words("حياة النبي")) == 2 and len(T.content_words("حياة البحر")) == 2 and len(T.content_words("الفلاحين والريف")) == 2,
   "«حياة النبي» / «حياة البحر» keep both words")

Q71 = "زوجتي تقول ولدنا اللي بالمتوسط صاير يرد علينا بقلة أدب، نبي كتاب نقراه احنا الاثنين"
ok(R.parent_said(Q71) and R.parent_reader([Q71]), "7.1: «ولدنا … نبي كتاب نقراه احنا الاثنين» — the parents read it")
ok(R.parent_said("ابننا بالثانوي صار عصبي مرة، ودنا بكتاب نقراه انا وزوجتي") and R.parent_said("our son is 13 and rude lately, my wife and I want to read something"),
   "fresh: «نقراه انا وزوجتي», \"my wife and I want to read\"")
ok(not R.parent_said("نبي كتاب نقراه مع ولدنا قبل النوم") and not R.parent_said("ولدنا يحب الفضاء، نبي له كتاب"),
   "reading it with the child / a book for him: no parent reader")

ok(not A.women_wish("ابغى كتاب نساء جسورات من طهران", ["نساء جسورات من طهران"]) and A.women_wish("ابغى كتاب نساء جسورات من طهران"),
   "26.1: «نساء» inside the title named is no wish for women authors (it was)")
ok(not A.women_wish("عندكم رواية «امرأة من طهران»؟", ["امرأة من طهران"]), "fresh: «امرأة من طهران» as a title")
ok(A.women_wish("ابي روايات لكاتبات إيرانيات", ["نساء جسورات من طهران"]), "a wish for women writers still counts")
ok("women_wish(t, _ttl35) for t in user_messages[-3:]" in src and src.count("women_wish(t, _ttl35)") == 3, "26.1: every women check gets the titles")

ok(T.content_words("خط الرقعة") == T.content_words("الخط العربي") and T.content_words("الخط الديواني") == T.content_words("الخط العربي"),
   "47.1: «خط الرقعة» / fresh «الخط الديواني» are looked for as «الخط العربي»")
ok(T.content_words("خط الاستواء") != T.content_words("الخط العربي") and T.content_words("خط الزمن") != T.content_words("الخط العربي"),
   "«خط الاستواء» / «خط الزمن» are not calligraphy")

_recs = {"غابرييل غارسيا ماركيز": {"author_id": 1, "origin": "كولومبيا", "known_for": ["مئة عام من العزلة", "الحب في زمن الكوليرا"]},
         "كارل ماركس": {"author_id": 2, "origin": "بدون جنسية", "known_for": ["رأس المال", "بيان الحزب الشيوعي"]},
         "كارل مارسي": {"author_id": 3, "origin": "الولايات المتحدة", "known_for": ["موسيقى العقل"]}}
import authors as _authors
_lk = _authors.lookup
_authors.lookup = lambda n: _recs.get(n)
_opts = list(_recs)
ok(AF._picked_by_record(AF.key("وش عندكم للروائي الكولومبي ماركس؟ صاحب العزلة"), _opts) == "غابرييل غارسيا ماركيز",
   "20.1: «الروائي الكولومبي … صاحب العزلة» picks Márquez")
ok(AF._picked_by_record(AF.key("عندكم شي للمفكر الألماني ماركس صاحب رأس المال؟"), _opts) == "كارل ماركس", "fresh: «المفكر … صاحب رأس المال» picks Marx")
ok(AF._picked_by_record(AF.key("عندكم كتب ماركس؟"), _opts) is None and AF._picked_by_record(AF.key("ابي رواية رعب"), _opts) is None,
   "no clue / a new request: still asked")
_authors.lookup = _lk

_rep3 = ("عندنا ٧ كتب فازت بجائزة الشيخ زايد. أقدر أذكر لك منها: «أ» (٢٠٢٥)، «ب» (٢٠٢٤). ما عندي تفاصيل السابع الآن، وموظف المكتبة يقدر يساعدك."
         " تفضّل الرواية أو الفكر؟")
_AB = {11: {"book_id": 11, "title": "الفن الهندي"}}
_d3 = A.list_all_completed34(_rep3, [(11, 2007)], _AB)
ok("ما عندي تفاصيل" not in _d3 and "أقدر أذكر لك منها" not in _d3 and "وهي:" in _d3 and "«الفن الهندي» (٢٠٠٧)" in _d3
   and _d3.rstrip().endswith("تفضّل الرواية أو الفكر؟"), f"3.1: the «no details of the seventh» sentence goes once the list is completed ({_d3})")
_e3 = A.list_all_completed34("We have 7 winners. I can name some of them: «A» (2025), «B» (2024). I don't have details on the seventh one right now."
                             " Which do you prefer?", [(11, 2007)], {11: {"book_id": 11, "title": "Indian Art"}}, "en")
ok("don't have details" not in _e3 and "They are:" in _e3 and "«Indian Art» (2007)" in _e3, f"fresh (English): likewise ({_e3})")
_n3 = A.list_all_completed34("عندنا ٣: «أ» و«ب». تحب أذكر لك أكثر عن الأول؟", [(11, 2007)], _AB)
ok("«أ» و«ب»" in _n3 and "وعندنا كذلك: «الفن الهندي»" in _n3, "a reply without those sentences only gets the missing one")

ok("_out34 = [x for x in scored if kid_shelf32" in src and 'pprefs.get("audience") in ("young_adult", "children")' in src,
   "8.2: the children's path for a reader of 12–17 leaves out a children's book whose recorded age does not cover him")
ok('"ما لقيت الآن كتابًا ثانيًا يناسب طلبك." if shown else "ما لقيت الآن كتابًا يناسب طلبك."' in src,
   "28.1: «كتابًا ثانيًا» only when a book was shown before")

# 24.1: the same work in another form
_TB = {1: {"book_id": 1, "title": "اللص والكلاب", "author": "نجيب محفوظ", "quantity": 0, "active": 1, "language": "ar"},
       2: {"book_id": 2, "title": "اللص والكلاب (رواية مصورة)", "author": "نجيب محفوظ", "quantity": 5, "active": 1, "language": "ar"},
       3: {"book_id": 3, "title": "زقاق المدق", "author": "نجيب محفوظ", "quantity": 0, "active": 1, "language": "ar"},
       4: {"book_id": 4, "title": "زقاق المدق: رواية مصورة", "author": "نجيب محفوظ", "quantity": 2, "active": 1, "language": "ar"},
       5: {"book_id": 5, "title": "الخيميائي", "author": "باولو كويلو", "quantity": 3, "active": 1, "language": "ar"},
       6: {"book_id": 6, "title": "الخيميائي (طبعة مصورة)", "author": "باولو كويلو", "quantity": 3, "active": 1, "language": "ar"}}
ok([b["book_id"] for b in A.match_titles(["اللص والكلاب"], _TB)] == [1, 2], "24.1: «اللص والكلاب» out of stock brings its in-stock graphic novel")
ok([b["book_id"] for b in A.match_titles(["زقاق المدق"], _TB)] == [3, 4], "fresh: «زقاق المدق: رواية مصورة» likewise")
ok([b["book_id"] for b in A.match_titles(["الخيميائي"], _TB)] == [5], "a copy in stock: no other form added")
ok("and author_flow.display(b.get(\"author\")) in _ea35)]" in src, "24.1: QADFIX27 #8 keeps the same author's other form of the work asked")
ok(A.wanted_edition("أبيها بالعربي مو بالإنجليزي") == "ar" and A.wanted_edition("not in English, in Arabic please", "en") == "ar",
   "24.2 (replay): «بالعربي مو بالإنجليزي» is Arabic (was English)")
ok(A.wanted_edition("مو بالعربي، بالانجليزي") == "en" and A.wanted_edition("no English copies please", "en") is None
   and A.wanted_edition("بالإنجليزي لو سمحت") == "en" and A.wanted_edition("ابيها بالعربي") == "ar", "a refused language is never the wish; plain wishes as before")
_conv20 = [{"role": "user", "content": "وش عندكم للروائي الكولومبي ماركس؟ صاحب العزلة"},
           {"role": "assistant", "content": "عندنا لغابرييل غارسيا ماركيز «مئة عام من العزلة»."}, {"role": "user", "content": "ماركيز طبعا"}]
ok(AF._spoken_of("غابرييل غارسيا ماركيز", "ماركيز", _conv20[:-1], None) and not AF._spoken_of("كارل ماركس", "ماركيز", _conv20[:-1], None)
   and "_sp35 = [o for o in options[:5] if _spoken_of(o, written, conversation[:-1], None)]" in open(AF.__file__, encoding="utf-8").read(),
   "20.2 (replay): «ماركيز طبعا» after the reply named him in full is that author, not the question again")

# ---------------------------------------------------------------- whole path on synthetic books
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
REPLY = {"text": "ok"}
def respond(conv, facts_json, guidance, *a, **k):
    facts = json.loads(facts_json); captured.append({"facts": facts, "guidance": guidance})
    return {"reply": REPLY["text"], "show_book_ids": [f["id"] for f in facts][:2], "suggestions": [], "unanswered": ""}
A.chat.respond = respond
ids = [b["book_id"] for b in base]
SPEC = [
    dict(title="الحياة أمامك", summary="رواية عن صبي في حي بلفيل", publication_year=2021),                       # 0 goncourt 1975
    dict(title="العاشق", summary="رواية عن فتاة في الهند الصينية", publication_year=2017),                        # 1 goncourt 1984
    dict(title="الوصية الفرنسية", summary="رواية عن الذاكرة", original_year=1995, publication_year=2012),          # 2 goncourt 1995
    dict(title="حياة متعجلة", summary="رواية قصيرة", original_year=2022, publication_year=2025),                   # 3 goncourt 2022
    dict(title="منطق الطير", genre="شعر", summary="ديوان صوفي قديم", original_year=1177, publication_year=2000),   # 4 classic, no prize
    dict(title="الأرض", summary="رواية عن قرية مصرية وفلاحيها في الريف ويدافع الفلاحين عن أرضهم", publication_year=2010),  # 5 peasants
    dict(title="جسر إلى الغابة", genre="أطفال", age_min=8, age_max=12, summary="قصة أطفال في الريف عن الصداقة"),     # 6 children's countryside
    dict(title="سلسلة الكون: القمر", genre="أطفال", age_min=6, age_max=10, summary="كتاب مصور للأطفال عن القمر وأطواره"),  # 7
    dict(title="200 خطوة نحو التربية برفق", genre="تطوير ذاتي", summary="دليل ميسر للوالدين للتعامل مع الأبناء بالرفق"),     # 8
    dict(title="دليل الآباء لوسائل التواصل الاجتماعي", genre="تطوير ذاتي", summary="منصات التواصل وأثرها على الأبناء والتواصل"),  # 9
    dict(title="مهارات التواصل الذكي", genre="تطوير ذاتي", summary="التواصل مع الآخرين وفن الحوار"),               # 10
    dict(title="سيكولوجية وسائل التواصل", genre="علم نفس", summary="التواصل الاجتماعي والنفس"),                    # 11
] + [dict(title=f"فائز زايد {n}", genre="فكر", summary="كتاب فكري", publication_year=2010) for n in range(7)] + [   # 12–18 zayed
    dict(title=f"كتاب آخر لمؤلف فائز {n}", genre="فكر", summary="كتاب فكري") for n in range(4)] + [                # 19–22
    dict(title="شرق المتوسط وتكوين التيار الراديكالي العالمي في الفترة من 1860 وحتى 1914", genre="تاريخ", summary="دراسة تاريخية"),  # 23
    dict(title="رواية عادية", summary="رواية"), dict(title="تاريخ المدن", genre="تاريخ"), dict(title="ديوان", genre="شعر")]
books = []
for b, sp in zip([dict(x) for x in base], SPEC):
    b.update(quantity=3, active=1, author="كاتب " + str(b["book_id"]), original_year=None, summary="", hook="", keywords="", subjects="",
             subgenres=None, series_name=None, series_position=None, goodreads_url=None, language="ar", age_min=None, age_max=None,
             page_count=200, genre="رواية", publication_year=2015)
    b.update(sp)
    books.append(b)
for n in range(4):   # the other books of the Zayed winners' authors
    books[19 + n]["author"] = books[12 + n]["author"]
A.list_books = lambda *a, **k: books
A.topics._cache.update(key=None)
conn = models.get_connection()
honors.ensure_table(conn)
conn.execute("DELETE FROM book_awards")
for i, y in ((0, 1975), (1, 1984), (2, 1995), (3, 2022)):
    conn.execute("INSERT INTO book_awards (book_id, award_key, result, year, source, created_at) VALUES (?, 'goncourt', 'winner', ?, 'test', 'now')",
                 (ids[i], y))
for n in range(7):
    conn.execute("INSERT INTO book_awards (book_id, award_key, result, year, source, created_at) VALUES (?, 'zayed', 'winner', ?, 'test', 'now')",
                 (ids[12 + n], 2025 - 3 * n))
conn.commit(); conn.close()
honors.clear_cache()
I = {k: ids[k] for k in range(len(SPEC))}

def run(msgs, reply="ok", **it):
    CUR.clear(); CUR.update(it)
    captured.clear(); REPLY["text"] = reply
    m = [{"role": "user", "content": x} if isinstance(x, str) else x for x in msgs]
    r = A._run_chat_once(m, active=("synthetic", "no-network"))
    c = captured[0] if captured else {"facts": [], "guidance": ""}
    return r, [f["id"] for f in c["facts"]], c["guidance"]

# 28 (grave)
_r, _f, _g = run([Q281], prefs={"awards": ["goncourt"]}, order_by="oldest")
ok(_f[:3] == [I[0], I[1], I[2]], f"28.1: the winners by the year of the prize, oldest first ({_f[:4]})")
ok(I[4] not in _f[:4], "28.1: the oldest classic of the stock («منطق الطير», no prize) is not the answer")
ok("الأقدم فوزًا هو «الحياة أمامك»" in _g and "فاز سنة ١٩٧٥" in _g and "حسب سنة صدور العمل أول مرة" not in _g,
   "28.1: the guidance names the oldest winner by its prize year, not the first-publication order")
_a1 = {"role": "assistant", "content": "ما لقيت الآن كتابًا يناسب طلبك.", "book_ids": []}
_r, _f, _g = run([Q281, _a1, Q282], prefs={"awards": ["goncourt"]})
ok(_f[:1] == [I[0]] and "الأقدم فوزًا هو «الحياة أمامك»" in _g, f"28.2: «يعني ما عندكم ولا وحدة؟» keeps the oldest ({_f[:3]})")
_a2 = {"role": "assistant", "content": "من أقدمها «العاشق» (١٩٨٤)", "book_ids": [I[1], I[2]]}
_r, _f, _g = run([Q281, _a1, Q282, _a2, Q283], prefs={"awards": ["goncourt"]}, intent="compare", order_by="oldest", about_shown=True)
ok("الأقدم فوزًا هو «الحياة أمامك»" in _g and "سنوات الفوز من سجل الجائزة" in _g and I[0] in _f,
   "28.3: «متأكد إنها أقدم وحدة؟» is answered from the prize years, the oldest winner in facts")
_r, _f, _g = run(["أبي أول رواية فازت بالغونكور من اللي عندكم"], prefs={"awards": ["goncourt"]})
ok(_f[:1] == [I[0]], f"fresh: «أول رواية فازت بالغونكور» → the 1975 winner ({_f[:3]})")
_r, _f, _g = run(["وش أحدث رواية فازت بالغونكور عندكم؟"], prefs={"awards": ["goncourt"]}, order_by="newest")
ok(_f[:1] == [I[3]] and "الأحدث فوزًا هو «حياة متعجلة»" in _g and "الأقدم" not in _g, "the newest (QADFIX33) is kept")
_r, _f, _g = run(["وش أقدم كتاب عندكم كتابةً؟"], prefs={}, order_by="oldest")
ok(_f[:1] == [I[4]] and "حسب سنة صدور العمل أول مرة" in _g, "no prize: the oldest is still by first publication (QADFIX9)")
_r, _f, _g = run(["عندكم كتب فازت بالغونكور؟ سمّها كلها من الأقدم"], prefs={"awards": ["goncourt"]})
ok("من الأقدم فوزًا: «الحياة أمامك» (١٩٧٥)، «العاشق» (١٩٨٤)" in _g, "the whole list asked with the oldest: listed oldest first")

# 12.2 (grave)
_a12 = {"role": "assistant", "content": "أنصحك بكتاب «سلسلة الكون: القمر»", "book_ids": [I[7]]}
_r, _f, _g = run([Q121, _a12, Q122], prefs={"genres": ["رواية"], "unsupported_requests": ["الريف", "حياة الفلاحين"], "audience": "adult"},
                 reader_age=9)
ok(_f[:1] == [I[5]] and I[6] not in _f[:2], f"12.2: the grandfather's novel about peasants first, not the children's book ({_f[:3]})")
ok(_r.get("audience") != "children" and "كتبًا للكبار" in _g, f"12.2: the reader is a grown-up now ({_r.get('audience')}/{_r.get('audience_age')})")
_r, _f, _g = run([Q121, _a12, "وأنا جده، ودي برواية لي عن حياة الفلاحين"], prefs={"genres": ["رواية"], "unsupported_requests": ["حياة الفلاحين"],
                 "audience": "adult"}, reader_age=9)
ok(_f[:1] == [I[5]] and _r.get("audience") != "children", f"fresh: «وأنا جده، ودي برواية لي عن حياة الفلاحين» ({_f[:3]})")
_r, _f, _g = run([Q121, _a12, "طيب وأنا جده، ابي له قصة عن الريف"], prefs={"unsupported_requests": ["الريف"], "audience": "children"}, reader_age=9)
ok("كتبًا للكبار" not in _g, "«ابي له قصة»: still for the grandson")

# 3.1–3.2 (medium)
Q31 = "كم كتاب عندكم فايز بجائزة الشيخ زايد؟ ابيها كلها بالاسم"
_r, _f, _g = run([Q31], prefs={"awards": ["zayed"]})
ok(all(I[12 + n] in _f for n in range(7)), f"3.1: all seven winners are in facts ({_f})")
_a3 = {"role": "assistant", "content": "عندنا ٧ … وعندنا كذلك: «فائز زايد 6» (٢٠٠٧).", "book_ids": [I[12], I[13]]}
_r, _f, _g = run([Q31, _a3, "طيب وش السابع اللي قلت ما عندك تفاصيله؟"], prefs={"awards": ["zayed"]}, intent="about_book")
ok(I[18] in _f and "ولا تقل عن أي منها إنه خطأ" in _g, f"3.2: the seventh is in facts and none is called a mistake ({_f})")
_r, _f, _g = run(["كم كتاب فاز بجائزة زايد عندكم؟"], prefs={"awards": ["zayed"]})
ok("ولا تقل عن أي منها إنه خطأ" not in _g, "no whole list before: no such note")

# 7.1 (medium)
_r, _f, _g = run([Q71], prefs={"use_cases": ["practical_learning"], "unsupported_requests": ["تربية المراهقين", "التواصل مع الأبناء"], "audience": "adult"})
ok(_f[:1] == [I[8]], f"7.1: the general parenting book first, not the social-media guide ({_f[:4]})")
_r, _f, _g = run(["ولدنا ١٣ سنة مدمن جوال، نبي كتاب نقراه احنا الاثنين عن وسائل التواصل"],
                 prefs={"unsupported_requests": ["وسائل التواصل"], "audience": "adult"})
ok(I[9] in _f[:2], f"screens named by the parents: the social-media guide stays up front ({_f[:4]})")
# QADFIX35b: score_books shuffles equal scores; the parenting books brought in must not depend on where a book with no score fell
import random as _rnd
_bad35b = []
for _seed in range(16):
    _rnd.seed(_seed)
    _r, _f, _g = run(["ولدنا ١٣ سنة مدمن جوال، نبي كتاب نقراه احنا الاثنين عن وسائل التواصل"],
                     prefs={"unsupported_requests": ["وسائل التواصل"], "audience": "adult"})
    if not (I[9] in _f[:2] and I[8] in _f[:2]):
        _bad35b.append((_seed, _f[:4]))
ok(not _bad35b, f"QADFIX35b: the same two parenting books up front whatever the order of equal scores (16 seeds; bad: {_bad35b})")

# 23.1 (minor)
_r, _f, _g = run(["ودي أعرف إذا «شرق المتوسط» متوفرة"], prefs={}, mentioned_titles=["شرق المتوسط"], intent="about_book")
ok("عنوانه يبدأ بهذه الكلمات فقط" in _g and "ليس في قائمتنا" in _g, "23.1: a longer title that only begins with the words asked is said as such")
_r, _f, _g = run(["عندكم «الأرض»؟"], prefs={}, mentioned_titles=["الأرض"], intent="about_book")
ok("يبدأ بهذه الكلمات" not in _g, "a title we have exactly: no such note")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
