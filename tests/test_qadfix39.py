"""[QADFIX39] Fixes for the QAD round 2026-11-14 (Ibrahim's standing word 2026-11-06: fixes go ahead, doubtful ones are listed):
(1) grave 2.1–2.2 newest Hugo «Hyperion 1990» while «كتاب المقبرة» (Hugo 2009) stands on the young readers' shelf: the newest / oldest of a
    prize runs over every winner in stock (a novel on the young readers' shelf counts for novels, said with its shelf), one count;
(2) 7.2 «رواية تاريخية عن مصر أيام الرهبان والكنيسة القديمة» → «ما لقيت»: monks / monastery / church / Copts are one subject, and the
    subject named now orders the books of the missing book's place;
(3) 9.2–9.3 "something standalone?" → non-fantasy: a standalone is no subject (no series first), and the English keyword "Fantasy" is a kind;
(4) 12.1–12.3 a boy of 12 «الحيوانات» → «كلماتي الأولى» / colouring books, then «بيت العقرب» («يُعامل كحيوان»): small children's forms out,
    a likeness «كـ» in a summary is not the word, the young readers' shelf with nothing on the subject is said;
(5) 26.1 «أبا الخيل» (a family's genealogy) offered as a horse book: «أبا / آل + name» is no subject; horses are one subject;
(6) 41.2 a spy novel asked, «غير رأفت الهجان» → chase novels that are no spy novels: the kind asked comes before a carried subject;
(7) 46.2 «خطوات تأسيس شركة ناشئة» → the subject «تاسيس، خطوات» → «ما لقيت»: steps are no subject, the company founded is.
Each check uses the round wording and a fresh one, with negatives."""
import json, os, re, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import honors
import topics
from keyword_prefs import normalize
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

_alone = getattr(A, "standalone_wish39", lambda s: False)
_todd = getattr(A, "toddler_title39", lambda b: False)

# ---------------------------------------------------------------- word readers
for s in ("رواية مستقلة", "مستقلة", "غير سلسلة", "مو جزء من سلسلة", "جزء واحد", "something standalone", "stand-alone", "not part of a series",
          "no huge series"):
    ok(_alone(s), f"3: «{s}» is a standalone wish")
for s in ("الدولة المستقلة", "سلسلة جورج", "series of unfortunate events", "الاستقلال", "رواية تاريخية"):
    ok(not _alone(s), f"3: «{s}» is not a standalone wish")
_g = topics.content_words("الرهبان")
ok(len(_g) == 1 and {"دير", "كنيسه", "قبطي", "اقباط"} <= _g[0], "2: «الرهبان» finds a monastery, a church, the Copts")
ok(len(topics.content_words("الخيل")) == 1 and {"حصان", "horse", "فروسيه"} <= topics.content_words("الخيل")[0],
   "5: «الخيل» finds a horse, «فروسية», 'horse'")
ok("روسيه" not in topics.stems("فروسية") and "روسيه" not in topics.content_words("الخيل")[0],
   "5: «فروسية» keeps its «ف» (never «روسية», Russian)")
ok("خيل" not in topics._tokens("أبا الخيل .. النسب- التسمية- الفروع") and "خيل" in topics._tokens("تاريخ الخيل العربية")
   and "سعود" not in topics._tokens("تاريخ آل سعود") and "تاريخ" in topics._tokens("تاريخ آل سعود"),
   "5: «أبا الخيل» / «آل سعود» are a family's name, not a subject; «الخيل» alone is")
ok("حيوان" in topics.stems("كحيوان") and "حيوان" not in topics._tokens("يكبر وهو يعامل كحيوان", kaf=False)
   if "kaf" in topics._tokens.__code__.co_varnames else False,
   "4: «يُعامل كحيوان» in a summary is a likeness, not «حيوان» (titles keep the old reading)")
ok(topics.content_words("خطوات") == [] and A.head_object30("تأسيس الشركات الناشئة") == normalize("الشركات الناشئة"),
   "7: «خطوات» is no subject; in «تأسيس الشركات الناشئة» the company is")
for t in ("كلماتي الأولى - الحيوانات", "سلسة كتابي الأول والأحجيات... الحيوانات", "كتاب تلوين حروف وحيوانات من الألف إلى الياء",
          "كتاب ألعاب الدمى... العد مع الحيوانات"):
    ok(_todd({"title": t}), f"4: «{t}» is a small child's form")
for t in ("هدهد سليمان عليه السلام", "كتاب الغابة", "لون الماء", "الحروف المتقاطعة في الرواية"):
    ok(not _todd({"title": t}), f"4: «{t}» is not")
_fan = {"subgenres": None, "keywords": '["Fantasy", "Books", "Magic"]', "genre": "رواية"}
ok(A.matching_subgenres(_fan, {"subgenres": ["فانتازيا"]}) and not A.matching_subgenres(_fan, {"subgenres": ["رومانسية"]})
   and not A.matching_subgenres({"subgenres": '["رومانسية"]', "keywords": '["Fantasy"]'}, {"subgenres": ["فانتازيا"]})
   and A.matching_subgenres({"subgenres": None, "keywords": '["Sci-Fi"]'}, {"subgenres": ["خيال علمي"]}),
   "3: an English genre keyword is the kind when no subgenre is recorded (a recorded one wins)")

# ---------------------------------------------------------------- whole path on synthetic books
base = [dict(b) for b in A.list_books(only_active=False)][:100]
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
    return {"reply": "ok", "show_book_ids": [f["id"] for f in facts][:2], "suggestions": [], "unanswered": ""}
A.chat.respond = respond
SPY = '["جاسوسية"]'
SPEC = [
    dict(title="Hyperion", language="en", summary="A science fiction novel of pilgrims"),                                         # 0 hugo 1990
    dict(title="نيورومانسر", summary="رواية عن قرصان شبكات"),                                                                     # 1 hugo 1985
    dict(title="كتاب المقبرة", genre="يافعين", age_min=10, summary="طفل تربيه الأشباح في مقبرة"),                               # 2 hugo 2009
    dict(title="الحراس", genre="قصص مصورة", summary="أبطال مقنعون"),                                                              # 3 hugo 1988
    dict(title="دليل كمبريدج للخيال العلمي", genre="فكر", summary="دراسات نقدية"),                                                 # 4 hugo 2005
    dict(title="Foundation", language="en", summary="An empire falls"),                                                            # 5 hugo 1943
    dict(title="خالتي صفية والدير", subjects='["ثأر في صعيد مصر ودير قبطي"]', summary="حب ينقلب انتقامًا في صعيد مصر"),            # 6
    dict(title="البشموري", subgenres='["رواية تاريخية"]', summary="ثورة فلاحي الدلتا الأقباط في مصر وموقف الكنيسة من القتال"),     # 7
    dict(title="ليالي القاهرة", subgenres='["رواية تاريخية"]', summary="رواية عن القاهرة في مصر أيام محمد علي"),                   # 8
    dict(title="نهار مصري", subgenres='["رواية تاريخية"]', summary="رواية عن أسرة في مصر"),                                       # 9
    dict(title="The Starless Sea", language="en", keywords='["Fantasy", "Books", "Magic"]', summary="A secret library under the earth"),  # 10
    dict(title="A Game of Thrones", language="en", keywords='["Fantasy", "Politics"]', series_name="A Song of Ice and Fire", series_position=1,
         summary="Noble houses at war"),                                                                                            # 11
    dict(title="A Clash of Kings", language="en", keywords='["Fantasy", "War"]', series_name="A Song of Ice and Fire", series_position=2,
         summary="Kings at war"),                                                                                                   # 12
    dict(title="Never Let Me Go", language="en", keywords='["Dystopia", "Literary"]', summary="Students at a boarding school"),      # 13
    dict(title="Ready Player One", language="en", keywords='["Science Fiction", "Games"]', summary="A virtual treasure hunt"),       # 14
    dict(title="كلماتي الأولى - الحيوانات", genre="أطفال", page_count=None, summary="صور حيوانات وأسماؤها"),                       # 15
    dict(title="كتاب تلوين حروف وحيوانات من الألف إلى الياء", genre="أطفال", page_count=None, summary="تلوين الحيوانات"),          # 16
    dict(title="هدهد سليمان (سلسلة قصص الحيوان في القرآن)", genre="أطفال", page_count=None, summary="قصة الهدهد والحيوانات"),        # 17
    dict(title="بيت العقرب", genre="يافعين", age_min=12, keywords='["استنساخ"]', summary="فتى مستنسخ يكبر وهو يعامل كحيوان ثم يهرب"),  # 18
    dict(title="كتاب الغابة", genre="أطفال", age_min=9, age_max=12, keywords='["حيوانات", "غابة"]', summary="طفل تربيه الذئاب"),    # 19
    dict(title="أبا الخيل .. النسب- التسمية- الفروع", genre="تاريخ", keywords='["أنساب", "عائلة"]',
         summary="كتاب في نسب أسرة أبا الخيل وفروعها"),                                                                            # 20
    dict(title="The Arabian Horse: Origin, Development and History", language="en", genre="تاريخ", summary="عن أصل الحصان العربي وتاريخه"),  # 21
    dict(title="فروسية (جزءان)", genre="تاريخ", summary="دراسات عن الخيل في الحرب والصيد والأدب"),                                 # 22
    dict(title="رأفت الهجان", subgenres=SPY, summary="جاسوس مصري في إسرائيل"),                                                     # 23
    dict(title="المغامرة", subgenres=SPY, summary="مهندس مصري عالق بين المخابرات الأمريكية والسوفيتية"),                           # 24
    dict(title="قنبلة للاستخدام الشخصي", subgenres=SPY, summary="عميلة مخابرات في مهمة"),                                         # 25
    dict(title="رجل تتعقبه الغربان", keywords='["مطاردة", "صحراء"]', summary="رجل في مطاردة طويلة في الصحراء"),                    # 26
    dict(title="ذات مرة في الطريق المظلم", keywords='["مطاردة", "ليل"]', summary="مطاردة ليلية على طريق مهجور"),                   # 27
    dict(title="The Lean Startup", language="en", genre="أعمال", keywords='["Entrepreneurship"]', summary="منهج لبناء الشركات الناشئة"),  # 28
    dict(title="Zero to One", language="en", genre="أعمال", keywords='["Startups"]', summary="ملاحظات عن الشركات الناشئة والابتكار"),  # 29
    dict(title="قوة الحمقى", genre="أعمال", summary="رواد الأعمال الاجتماعيون وأسواقهم"),                                          # 30
    dict(title="أنساب قبائل نجد", genre="تاريخ", keywords='["أنساب", "قبائل"]', summary="أنساب العوائل والقبائل في نجد"),          # 31
] + [dict(title=f"رواية عادية {n}", summary="رواية عن الحياة اليومية في المدينة") for n in range(60)]
books = []
for b, sp in zip([dict(x) for x in base], SPEC):
    b.update(quantity=3, active=1, author="كاتب " + str(b["book_id"]), original_year=None, summary="", hook="", keywords="", subjects="",
             subgenres=None, series_name=None, series_position=None, goodreads_url=None, language="ar", age_min=None, age_max=None,
             page_count=200, genre="رواية", publication_year=2015, setting=None, volumes=None, set_kind=None)
    b.update(sp)
    books.append(b)
assert len(books) == len(SPEC), (len(books), len(SPEC))
A.list_books = lambda *a, **k: books
A.topics._cache.update(key=None)
I = {k: books[k]["book_id"] for k in range(len(SPEC))}
_REV = {v: k for k, v in I.items()}
ix = lambda ids: [_REV.get(i, i) for i in ids]
conn = models.get_connection()
honors.ensure_table(conn)
conn.execute("DELETE FROM book_awards")
for k, y in ((0, 1990), (1, 1985), (2, 2009), (3, 1988), (4, 2005), (5, 1943)):
    conn.execute("INSERT INTO book_awards (book_id, award_key, result, year, source, created_at) VALUES (?, 'hugo', 'winner', ?, 'test', 'now')",
                 (I[k], y))
conn.commit(); conn.close()
honors.clear_cache()

def run(msgs, language="ar", register=None, **it):
    CUR.clear(); CUR.update(it)
    captured.clear()
    m = [{"role": "user", "content": x} if isinstance(x, str) else x for x in msgs]
    r = A._run_chat_once(m, active=("synthetic", "no-network"), language=language, register_msgs=register)
    c = captured[0] if captured else {"facts": [], "guidance": ""}
    return r, [f["id"] for f in c["facts"]], c["guidance"], (r.get("_debug") or {}), [b["book_id"] for b in r.get("books") or []]

def first_count(g):
    m = re.search(r"المتوفر عندنا الآن لطلب الجائزة: (\S+) كتابًا فاز هو نفسه", g)
    return m.group(1) if m else None

# 1 (2.1–2.2, grave)
Q21, Q22 = "من الروايات اللي فازت بجائزة هوغو عندكم، أيها آخر واحدة فازت؟", "متأكدة؟ ما عندكم رواية فازت فيها بعد التسعين؟"
PH = {"genres": ["رواية"], "awards": ["hugo"]}
_r, _f, _g, _d, _c = run([Q21], prefs=PH)
ok(_f[:1] == [I[2]], f"2.1: the newest Hugo winner is «كتاب المقبرة» (2009, young readers' shelf), first in facts ({ix(_f[:4])})")
ok("الأحدث فوزًا هو «كتاب المقبرة»" in _g and "رف اليافعين" in _g, "2.1: the guidance names it, with its shelf")
ok(I[2] in _c, f"2.1: it is a card ({ix(_c)})")
ok(I[3] not in _f[:1] and I[4] not in _f[:1], "2.1: a comic or a study of the genre is not «the newest novel»")
_n1 = first_count(_g)
_l1 = re.search(r"فازت هي نفسها ضمن طلبه (\S+)،", _g)
_a21 = {"role": "assistant", "content": "الأحدث فوزًا هو Hyperion ١٩٩٠", "book_ids": [I[0], I[1]]}
_r, _f, _g, _d, _c = run([Q21, _a21, Q22], prefs=PH, wants_new_options=True)
ok(_f[:1] == [I[2]] and "الأحدث فوزًا هو «كتاب المقبرة»" in _g, f"2.2: «متأكدة؟ … بعد التسعين؟» → «كتاب المقبرة» ({ix(_f[:3])})")
_l2 = re.search(r"فازت هي نفسها ضمن طلبه (\S+)،", _g)
ok(_l1 and _l2 and _l1.group(1) == _l2.group(1) and first_count(_g) in (None, _l2.group(1)) and _n1 in (None, _l1.group(1)),
   f"2.1 → 2.2: one count of winners in both turns ({_n1} {_l1 and _l1.group(1)} / {first_count(_g)} {_l2 and _l2.group(1)})")
_r, _f, _g, _d, _c = run(["وش أحدث رواية فازت بالهوغو؟"], prefs=PH)
ok(_f[:1] == [I[2]], f"fresh: «وش أحدث رواية فازت بالهوغو؟» → the same ({ix(_f[:3])})")
_r, _f, _g, _d, _c = run(["وش أقدم رواية فازت بالهوغو عندكم؟"], prefs=PH)
ok(_f[:1] == [I[5]], f"negative: the oldest → «Foundation» (1943), as before ({ix(_f[:3])})")
_r, _f, _g, _d, _c = run(["عندكم روايات فازت بالهوغو؟"], prefs=PH)
ok(I[2] not in _f, f"negative: no order asked → the novels' shelf as before ({ix(_f[:5])})")
_r, _f, _g, _d, _c = run(["وش أحدث كتاب فاز بالهوغو عندكم؟"], prefs={"awards": ["hugo"]})
ok(_f[:1] == [I[2]] and "رف اليافعين عندنا: قل ذلك" not in _g, f"negative: no kind asked → the newest of all, no shelf note needed ({ix(_f[:3])})")

# 2 (7.2, medium)
P72 = {"genres": ["رواية"], "subgenres": ["تاريخية"], "unsupported_requests": ["مصر", "الرهبان", "الكنيسة القديمة"]}
_r, _f, _g, _d, _c = run(["طيب أبي رواية تاريخية عن مصر أيام الرهبان والكنيسة القديمة، قريبة من جوها"], prefs=P72)
ok({I[6], I[7]} == set(_f[:2]), f"7.2: «البشموري» (Copts, the church) and «خالتي صفية والدير» (a Coptic monastery) first ({ix(_f[:5])})")
_r, _f, _g, _d, _c = run(["عندكم رواية عن الرهبان والأديرة في مصر؟"], prefs={"genres": ["رواية"], "unsupported_requests": ["الرهبان", "الأديرة"]})
ok({I[6], I[7]} <= set(_f[:3]) and I[9] not in _f[:2], f"fresh: «الرهبان والأديرة» → the monastery / Coptic novels ({ix(_f[:4])})")
_r, _f, _g, _d, _c = run(["أبي رواية تاريخية عن مصر"], prefs={"genres": ["رواية"], "subgenres": ["تاريخية"], "unsupported_requests": ["مصر"]})
ok(I[8] in _f[:4] and I[9] in _f[:4], f"negative: Egypt alone → the Egypt novels as before ({ix(_f[:4])})")

# 3 (9.2–9.3, medium)
Q91 = "i'm an adult looking for a fantasy novel, english copy please"
_a91 = {"role": "assistant", "content": "A Game of Thrones …", "book_ids": [I[11], I[12]]}
P92 = {"genres": ["رواية"], "subgenres": ["فانتازيا"], "unsupported_requests": ["رواية مستقلة"], "audience": "adult"}
_r, _f, _g, _d, _c = run([Q91, _a91, "something standalone? i don't want to start a huge series"], language="en", prefs=P92, wants_new_options=True)
ok(_f[:1] == [I[10]] and I[13] not in _f[:2] and I[14] not in _f[:2], f"9.2: standalone fantasy → «The Starless Sea» first ({ix(_f[:4])})")
ok((_d.get("reference") or {}).get("topic_phrases") == [] and "standalone" in _g, "9.2: «رواية مستقلة» is no subject; the guidance says standalone")
_r, _f, _g, _d, _c = run([Q91, _a91, "something standalone?", {"role": "assistant", "content": "Never Let Me Go", "book_ids": [I[13]]},
                          "it has to be fantasy though"], language="en", prefs=P92, wants_new_options=True)
ok(_f[:1] == [I[10]] and I[13] not in _c, f"9.3: «it has to be fantasy» → «The Starless Sea», no Never Let Me Go card ({ix(_f[:4])} {ix(_c)})")
_r, _f, _g, _d, _c = run(["أبي رواية فانتازيا بالإنجليزي، بس مو جزء من سلسلة"],
                         prefs={"genres": ["رواية"], "subgenres": ["فانتازيا"], "unsupported_requests": ["مو جزء من سلسلة"]})
ok(I[10] in _f and (I[11] not in _f or _f.index(I[10]) < _f.index(I[11])), f"fresh (Arabic): not a series → «The Starless Sea» before GoT ({ix(_f[:4])})")
_r, _f, _g, _d, _c = run([Q91], language="en", prefs={"genres": ["رواية"], "subgenres": ["فانتازيا"], "audience": "adult"})
ok({I[10], I[11]} <= set(_f) and I[13] not in _f, f"negative: no standalone said → the series may come too, never a non-fantasy ({ix(_f[:4])})")

# 4 (12.1–12.3, medium)
Q121 = "ابني صار عمره ١٢ ويحب كل شي عن الحيوانات، وش يقرا؟"
P121 = {"unsupported_requests": ["الحيوانات"], "audience": "children"}
_r, _f, _g, _d, _c = run([Q121], prefs=P121)
ok(I[15] not in _f and I[16] not in _f and bool({I[17], I[19]} & set(_f)), f"12.1: no «كلماتي الأولى» / colouring book for a boy of 12 ({ix(_f[:5])})")
_a121 = {"role": "assistant", "content": "…", "book_ids": [I[17]]}
_r, _f, _g, _d, _c = run([Q121, _a121, "هو عمره ١٢ مو صغير، أبي شي يناسب سنه"], prefs=P121, wants_new_options=True)
ok(I[15] not in _f and I[16] not in _f, f"12.2: «مو صغير» → still no small child's form ({ix(_f[:5])})")
_r, _f, _g, _d, _c = run([Q121, _a121, "من رف اليافعين، وش عندكم عن الحيوانات؟"],
                         prefs={"genres": ["يافعين"], "unsupported_requests": ["الحيوانات"], "audience": "young_adult"}, wants_new_options=True)
ok("ما في رف اليافعين عندنا كتاب عن «الحيوانات»" in _g, "12.3: the young readers' shelf has nothing on animals: said first")
ok(not topics.match(["الحيوانات"], I[18], topics.index({b["book_id"]: b for b in books}))[0],
   "12.3: «بيت العقرب» («يُعامل كحيوان») is not a book on animals")
_r, _f, _g, _d, _c = run(["بنت أختي عمرها ٤ تحب الحيوانات"], prefs=P121)
ok(I[15] in _f, f"negative: a child of 4 may still get «كلماتي الأولى - الحيوانات» ({ix(_f[:5])})")

# 5 (26.1, medium)
_r, _f, _g, _d, _c = run(["كتاب عن تاريخ الخيل العربية وأنسابها"], prefs={"genres": ["تاريخ"], "unsupported_requests": ["الخيل العربية", "أنساب الخيل"]})
ok(I[20] not in _f[:3] and bool({I[21], I[22]} & set(_f[:2])), f"26.1: horse books first, never «أبا الخيل» (a family) ({ix(_f[:4])})")
_r, _f, _g, _d, _c = run(["أبي كتاب عن الحصان العربي"], prefs={"unsupported_requests": ["الحصان العربي"]})
ok(I[21] in _f[:2], f"fresh: «الحصان العربي» → «The Arabian Horse» ({ix(_f[:3])})")
_r, _f, _g, _d, _c = run(["أبي كتاب عن أنساب العوائل في نجد"], prefs={"genres": ["تاريخ"], "unsupported_requests": ["أنساب العوائل"]})
ok(I[31] in _f[:2], f"negative: genealogies of families still found ({ix(_f[:3])})")

# 6 (41.2, medium)
_a411 = {"role": "assistant", "content": "«رأفت الهجان»", "book_ids": [I[23]]}
P412 = {"genres": ["رواية"], "subgenres": ["جاسوسية"], "unsupported_requests": ["المطاردة"]}   # (the synthetic authors carry no country)
_r, _f, _g, _d, _c = run(["ودي بروايه جاسوسيه فيها مطارده وحماس", _a411, "أبي شي عربي غير رأفت الهجان"], prefs=P412, wants_new_options=True)
ok(set(_f[:2]) == {I[24], I[25]}, f"41.2: the spy novels first, the chase novels that are no spy novels after ({ix(_f[:4])})")
ok("يأتي أولًا" in _g or I[26] not in _f, "41.2: the guidance says the kind asked comes first")
_Q411 = "ودي بروايه جاسوسيه فيها مطارده وحماس"
_r, _f, _g, _d, _c = run([_Q411, _a411, "أبي رواية جاسوسية عربية فيها مطاردة وحماس، غير رأفت الهجان."], prefs=P412, wants_new_options=True,
                         register=[_Q411, "أبي شي عربي غير رأفت الهجان"])
ok(set(_f[:2]) == {I[24], I[25]}, f"replay 41.2: the second look's rewrite («…فيها مطاردة») is not the visitor's word: the spy novels first ({ix(_f[:4])})")
_r, _f, _g, _d, _c = run(["أبي رواية فيها مطاردة"], prefs={"genres": ["رواية"], "unsupported_requests": ["مطاردة"]})
ok(bool({I[26], I[27]} & set(_f[:2])), f"negative: a chase asked now with no kind → the chase novels ({ix(_f[:3])})")

# 7 (46.2, medium)
_a461 = {"role": "assistant", "content": "«قوة الحمقى»", "book_ids": [I[30]]}
_r, _f, _g, _d, _c = run(["ودي أبدأ مشروع صغير، فيه كتاب يفيدني بالبداية؟", _a461, "أبي شي عملي عن خطوات تأسيس شركة ناشئة"],
                         prefs={"genres": ["أعمال"], "use_cases": ["practical_learning"], "unsupported_requests": ["تأسيس الشركات الناشئة", "خطوات"]},
                         wants_new_options=True)
ok({I[28], I[29]} <= set(_f[:3]) and "ولا تعرض أي كتاب" not in _g, f"46.2: «The Lean Startup» / «Zero to One», no «show no book» ({ix(_f[:4])})")
ok("خطوات" not in str((_d.get("reference") or {}).get("topic_phrases")) and "تأسيس الشركات الناشئة، خطوات" not in _g,
   "46.2: «خطوات» is no subject, and the request is not said unanswered")
_r, _f, _g, _d, _c = run(["كتاب عن إنشاء الشركات الناشئة"], prefs={"unsupported_requests": ["إنشاء الشركات الناشئة"]})
ok({I[28], I[29]} <= set(_f[:3]), f"fresh: «إنشاء الشركات الناشئة» → the same ({ix(_f[:3])})")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
