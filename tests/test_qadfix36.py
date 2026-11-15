"""[QADFIX36] Ibrahim's answers (2026-11-13) on QADFIX35's questions 1 and 3 and on INBOX1112 item 29:
(1) «ج» — a reader of 12–13 on the children's path also gets the young readers' shelf books of the kind asked (QAD 2026-11-12 case 8.2,
«عندكم شي ثاني لها؟» for a niece of 12 → «جيم الزر وسائق القطار» (6+) as the only book);
(2) «ب» — an English reply names an Arabic copy by its English title AND its own Arabic title in «» so the card check finds it
(case 22.1, "do u have books by Agatha Cristy" → two books about her named in English, no card);
(3) «نعم» — a book asked for and missing whose outside record names a place: our books about that same place come first
(library inbox 2026-11-11 item 29, «اللؤلؤة التي كسرت محارتها» → «حجر الصبر» in 1 run of 3).
Each check uses the round / inbox wording and a fresh one, with negatives."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()

# ---------------------------------------------------------------- word readers
ok("مغامر" in A.kind_keys36(["مغامرة"]) and "مغامر" in A.kind_keys36(["قصص المغامرات"]),
   "1: «مغامرة» and «قصص المغامرات» are the same kind («مغامر»)")
ok(not A.kind_keys36(["قصص"]) and not A.kind_keys36(["روايات للأطفال"]), "«قصص» / «روايات للأطفال» name no kind")
_rec29 = ("تسرد هذه الرواية للكاتبة الأمريكية، ذات الأصول الأفغانية، نادية هاشمي تفاصيل قصة حزينة… تكاد تردد الجبال الأفغانية صداها."
          " هي صورة لأفغانستان في كل مجدها، ومرآة تعكس صراعات النساء الأفغانيات")
ok(A.book_place36([_rec29]) == ["afghanistan"], "29: the Goodreads record of «اللؤلؤة التي كسرت محارتها» names Afghanistan")
ok(A.book_place36(["A novel of three generations of Afghan women in Kabul"]) == ["afghanistan"]
   and A.book_place36(["رواية تدور في طهران عن أسرة إيرانية"]) == ["iran"], "fresh: an English record (Kabul), an Arabic one (Tehran)")
ok(A.book_place36(["رواية للكاتبة الإيرانية عن الحب"]) == [] and A.book_place36(["A love story by the Iranian author"]) == []
   and A.book_place36(["رواية عن الحب والفقد"]) == [], "the author's nationality after a writer word, or no place: none")
ok(A._english_reply({"reply": "We have “Mallowan's Memoirs” («مذكرات مالوان»); our copy is in Arabic."})
   and not A._english_reply({"reply": "عندنا «مذكرات مالوان» بالعربي"}) and not A._english_reply({"reply": "«مذكرات مالوان»"}),
   "22.1: the Arabic titles in «» beside their English names leave an English reply English")
ok("The Little Prince («الأمير الصغير»)" in src and "its card under the reply shows the Arabic title" not in src,
   "22.1: the English guidance asks for the copy's own Arabic title in «» after its English name")

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
REPLY = {"text": "ok", "ids": None}
def respond(conv, facts_json, guidance, *a, **k):
    facts = json.loads(facts_json); captured.append({"facts": facts, "guidance": guidance})
    return {"reply": REPLY["text"], "show_book_ids": REPLY["ids"] if REPLY["ids"] is not None else [f["id"] for f in facts][:2],
            "suggestions": [], "unanswered": ""}
A.chat.respond = respond
SPEC = [
    dict(title="جيم الزر وسائق القطار", genre="أطفال", age_min=6, subgenres='["مغامرة", "فانتازيا"]', summary="مغامرة جيم وصديقه سائق القطار"),  # 0
    dict(title="قصة الباحثين عن الكنز", genre="أطفال", age_min=9, age_max=11, subgenres='["مغامرة"]', summary="إخوة يبحثون عن كنز"),       # 1
    dict(title="رينكو والكنز", genre="يافعين", subgenres='["مغامرات", "أدب يافعين"]', summary="رواية لليافعين عن يتيم يبحث عن كنز أبيه"),  # 2
    dict(title="في أعماق البحر", genre="يافعين", age_min=9, age_max=12, keywords='["مغامرة"]', summary="مغامرة جديدة في فندق على البحر"),  # 3
    dict(title="رسائل الحب الأولى", genre="يافعين", subgenres='["رومانسي"]', summary="قصة حب مدرسية"),                                  # 4
    dict(title="رحلة إلى الجبل الأخير", genre="يافعين", age_min=15, subgenres='["مغامرات"]', summary="مغامرة في الجبال"),               # 5
    dict(title="لغز البيت المهجور", genre="يافعين", subgenres='["لغز"]', summary="أصدقاء يحلون ألغاز البيت"),                         # 6 (no age)
    dict(title="لغز الحديقة الصغيرة", genre="أطفال", age_min=4, age_max=7, subgenres='["لغز"]', summary="لغز لطيف للصغار"),            # 7
    dict(title="حجر الصبر", summary="في غرفة ضيقة بأفغانستان تسهر شابة على زوجها الغائب عن الوعي"),                                    # 8
    dict(title="أرض ورماد", summary="يقصف الجيش السوفييتي قرية أفغانية فلا ينجو منها غير عجوز وحفيده"),                                # 9
    dict(title="تلك الأشياء المظلمة", summary="رواية نفسية عن أثر الماضي في العائلة"),                                                 # 10
    dict(title="فتاة من الشرق", summary="حكاية أجيال عن العائلة والتهجير والانتماء"),                                                  # 11
    dict(title="سووشون", summary="رواية عن شيراز وأسرة إيرانية أثناء الاحتلال"),                                                         # 12
    dict(title="مذكرات مالوان الكاملة: حياتي مع أجاثا كريستي", genre="سيرة", summary="مذكرات عالم الآثار زوج أجاثا كريستي"),           # 13
    dict(title="أجاثا كريستي واستخدام السموم", genre="علوم", summary="السموم في روايات أجاثا كريستي"),                                  # 14
] + [dict(title=f"رواية عادية {n}", summary="رواية عن الحياة اليومية") for n in range(6)]                                               # 15–20
SPEC += [dict(title="مختارات من ديوان شمس تبريز", genre="شعر", summary="غزليات من ديوان شمس تبريز، شاعر إيران الكبير"),   # 21
         dict(title="الطريق إلى أصفهان", summary="رحلة طبيب بين بلاطات فارس حتى أصفهان")]                                     # 22
books = []
for b, sp in zip([dict(x) for x in base], SPEC):
    b.update(quantity=3, active=1, author="كاتب " + str(b["book_id"]), original_year=None, summary="", hook="", keywords="", subjects="",
             subgenres=None, series_name=None, series_position=None, goodreads_url=None, language="ar", age_min=None, age_max=None,
             page_count=200, genre="رواية", publication_year=2015, setting=None)
    b.update(sp)
    books.append(b)
books[8]["author"] = books[9]["author"] = "عتيق رحيمي"
A.list_books = lambda *a, **k: books
A.topics._cache.update(key=None)
I = {k: books[k]["book_id"] for k in range(len(SPEC))}

def run(msgs, reply="ok", ids=None, language="ar", **it):
    CUR.clear(); CUR.update(it)
    captured.clear(); REPLY.update(text=reply, ids=ids)
    m = [{"role": "user", "content": x} if isinstance(x, str) else x for x in msgs]
    r = A._run_chat_once(m, active=("synthetic", "no-network"), language=language)
    c = captured[0] if captured else {"facts": [], "guidance": ""}
    return r, [f["id"] for f in c["facts"]], c["guidance"], [b["book_id"] for b in r.get("books") or []]

# 1 (8.2, Ibrahim «ج»)
Q81 = "بنت أخوي عمرها ١٢ تحب قصص المغامرات والبحث عن الكنوز"
_a81 = {"role": "assistant", "content": "أنصحك بـ«قصة الباحثين عن الكنز»", "book_ids": [I[1]]}
P8 = {"genres": ["رواية"], "subgenres": ["مغامرة"], "unsupported_requests": ["البحث عن الكنوز"], "audience": "children"}
_r, _f, _g, _c = run([Q81, _a81, "عندكم شي ثاني لها؟"], prefs=P8, wants_new_options=True)
ok(I[2] in _f and I[3] in _f, f"8.2: «عندكم شي ثاني لها؟» (12) brings the young readers' shelf adventures ({_f})")
ok(I[0] not in _f, "8.2: the «6+» children's book is no longer the answer")
ok(I[4] not in _f and I[5] not in _f, "8.2: not a romance of that shelf, not a book for 15+")
ok(set(_c) & {I[2], I[3]} and "رف اليافعين" in _g, f"8.2: their cards are shown and the guidance says where they stand ({_c})")
_r, _f, _g, _c = run([Q81], prefs=dict(P8, unsupported_requests=["الكنوز"]))
ok(I[2] in _f and I[3] in _f, f"8.1 (first message): the shelf's adventures already there ({_f})")
_r, _f, _g, _c = run(["بنت خالتي عمرها ١٢ سنة وتحب قصص الألغاز، وش يناسبها؟"], prefs={"subgenres": ["لغز"], "audience": "children"})
ok(I[6] in _f and I[7] not in _f and I[6] in _c, f"fresh: a cousin of 12 who likes riddles → «لغز البيت المهجور», not the 4–7 one ({_f})")
_r, _f, _g, _c = run(["ولدي عمره ١١ يحب قصص المغامرات"], prefs={"subgenres": ["مغامرة"], "audience": "children"})
ok(I[2] not in _f and "رف اليافعين" not in _g, "a child of 11: the children's shelf only, as before")
_r, _f, _g, _c = run(["بنت أخوي عمرها ١٢، ابي قصص مغامرات من رف الأطفال"], prefs={"subgenres": ["مغامرة"], "audience": "children"})
ok(I[2] not in _f and I[3] not in _f, "the children's shelf named: no young readers' shelf books")

# 2 (22.1, Ibrahim «ب»)
_kaa, _no = A.known_absent_author, A.authors.names_of
A.known_absent_author = lambda req, ab: {"name_ar": "أجاثا كريستي", "author_id": 1}
A.authors.names_of = lambda rec: ["أجاثا كريستي", "Agatha Christie"]
AR22 = {"name_as_written": "Agatha Cristy", "full_name": "أجاثا كريستي", "aliases": ["Agatha Christie"], "mode": "works"}
R22 = ("Agatha Christie's own books aren't available here, but we have two books about her. “Mallowan's Memoirs” («مذكرات مالوان») by"
       " Max Mallowan; our copy is in Arabic. “Agatha Christie and the Use of Poisons” («أجاثا كريستي واستخدام السموم»); our copy is in Arabic.")
_r, _f, _g, _c = run(["do u have books by Agatha Cristy"], reply=R22, ids=[I[13], I[14]], language="en", author_request=AR22)
ok(I[13] in _c and I[14] in _c, f"22.1: both books about her get their cards when the reply gives their Arabic titles ({_c})")
ok("The Little Prince («الأمير الصغير»)" in _g, "22.1: the guidance of that turn asks for the Arabic title in «»")
R22b = ("Her novels aren't in stock, but “Mallowan's Memoirs” («مذكرات مالوان») tells their life together; our copy is in Arabic.")
_r, _f, _g, _c = run(["anything about agatha christie's life?"], reply=R22b, ids=[I[13]], language="en", author_request=dict(AR22, name_as_written="agatha christie"))
ok(_c == [I[13]], f"fresh: two words that begin the Arabic title («مذكرات مالوان») name that copy ({_c})")
_r, _f, _g, _c = run(["do u have books by Agatha Cristy"], reply="Her own books aren't available here. “Mallowan's Memoirs” tells their life.",
                     ids=[I[13]], language="en", author_request=AR22)
ok(_c == [], "an English title alone still names no Arabic copy (the model is asked for the «» title)")
_r, _f, _g, _c = run(["do u have books by Agatha Cristy"], reply="Her own books aren't available here; we have «أجاثا كريستي» only as a name.",
                     ids=[I[14]], language="en", author_request=AR22)
ok(_c == [], "her name in «» alone is not the poisons book")
A.known_absent_author, A.authors.names_of = _kaa, _no

# 3 (inbox item 29, Ibrahim «نعم»)
T29 = "اللؤلؤة التي كسرت محارتها"
OUT = {}
A.outside_books.lookup = lambda t, *a, **k: dict(OUT[t]) if t in OUT else None
def _rec(title, desc, genres=("Fiction", "Historical Fiction")):
    OUT[title] = {"status": "found", "title": title, "author": "Someone", "genres": list(genres), "subjects": [], "description": desc,
                  "sources": ["https://www.goodreads.com/book/show/1"]}
_rec(T29, _rec29)
REF = lambda t, th=("العائلة", "الأجيال"): [{"title": t, "relation": "same_book", "known": True, "author": "", "themes": list(th),
                                            "moods": [], "format": "novel"}]
_r, _f, _g, _c = run([T29], intent="about_book", mentioned_titles=[T29], references=REF(T29), prefs={"title_keywords": [T29]})
ok(set(_f[:2]) == {I[8], I[9]}, f"29: the novels about Afghanistan come first among the close books ({_f[:4]})")
ok("عن المكان نفسه" in _g and "«حجر الصبر»" in _g and "عن أفغانستان" in _g, "29: the guidance names them and says to offer them first")
ok(set(_c) == {I[8], I[9]}, f"29: their cards ({_c})")
_r, _f, _g, _c = run(["عندكم رواية اللؤلؤة التي كسرت محارتها؟"], intent="about_book", mentioned_titles=[T29], references=REF(T29),
                     prefs={"genres": ["رواية"], "title_keywords": [T29]})
ok(set(_f[:2]) == {I[8], I[9]}, f"29 (second wording of the inbox replay): the same ({_f[:4]})")
T3f = "الحديقة الفارسية"
_rec(T3f, "رواية تدور في طهران عن أسرة إيرانية تعيش سنوات الثورة")
_r, _f, _g, _c = run([f"عندكم {T3f}؟"], intent="about_book", mentioned_titles=[T3f], references=REF(T3f), prefs={"title_keywords": [T3f]})
ok(_f[:1] == [I[12]] and "عن إيران" in _g, f"fresh: a missing novel set in Tehran → «سووشون» (Shiraz) first ({_f[:3]})")
T3e = "The Kabul Beauty School"
_rec(T3e, "A memoir-like novel of women in Kabul, Afghanistan, after the war")
_r, _f, _g, _c = run([f"do you have {T3e}?"], intent="about_book", mentioned_titles=[T3e], references=REF(T3e, ("women",)),
                     prefs={"title_keywords": [T3e]}, language="en")
ok(set(_f[:2]) == {I[8], I[9]} and "about the same place" in _g, f"fresh (English): Kabul → the Afghan novels first ({_f[:3]})")
T3v = "نساء من كابول"
_r, _f, _g, _c = run([f"أبحث عن رواية اسمها «{T3v}»"], intent="about_book", mentioned_titles=[T3v],
                     references=[{"title": T3v, "relation": "same_book", "known": False, "author": "", "themes": [], "moods": []}],
                     prefs={"title_keywords": [T3v]})
ok(set(_f[:2]) == {I[8], I[9]}, f"fresh: no outside record, the place in the visitor's own words («كابول») ({_f[:3]})")
T3p = "قصة حب إيرانية تحت مقص الرقيب"
_rec(T3p, "A love story in Tehran under the censor, in modern Iran", ("Fiction", "Iran", "Romance"))
_r, _f, _g, _c = run([f"عندكم رواية «{T3p}»؟"], intent="about_book", mentioned_titles=[T3p], references=REF(T3p, ("الحب",)),
                     prefs={"genres": ["رواية"], "title_keywords": [T3p]})
ok(_f[:1] == [I[12]] and I[22] in _f[:3] and I[21] not in _f, f"replay 2965: «عندكم رواية …؟» about Iran → the Iranian novels, not a diwan ({_f[:4]})")
T3n = "ظلال الحنين"
_rec(T3n, "رواية للكاتبة الإيرانية عن الحب والفقد والذاكرة")
_r, _f, _g, _c = run([T3n], intent="about_book", mentioned_titles=[T3n], references=REF(T3n), prefs={"title_keywords": [T3n]})
ok("عن المكان نفسه" not in _g and I[12] not in _f[:1], "only the author's nationality named: no place, nothing put first")
ok("place36[ref[\"title\"]] = (_lb36," in src, "3: the place books are kept for the guidance")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
