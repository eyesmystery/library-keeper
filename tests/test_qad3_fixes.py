"""Fixes after the daily chat test round of 2026-10-13 (QAD; Ibrahim «ابغى اصلاحات بعد جولة الاسئلة»).

One check per error of research/chat_tests_2026-10-13/report.md fixed in code: an age written in words, a
prize whose name holds «طلاب الثانوية», the start of a title after «اسمها … شي كذا», a foreign title in
another Arabic spelling, «أحدث كتاب» / «صدرت هالسنة», «مين فاز فيها سنة …», «كم رواية عندكم؟ ووش أقل قسم»,
the English edition of an author's books as a follow-up, a book's own prize before its author's, a parent
asking for himself, the answer to «تقصد … ولا …؟» by what the author is known for, and the guidance for
«رواية … بس مش كتاب تاريخ».
"""
import json, os, sys, time
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm, chat, chat_rules as R, author_flow, honors, app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

seen = {}
def fake_respond(conv, facts_json, guidance, active, *a):
    seen["facts"] = json.loads(facts_json); seen["guidance"] = guidance
    return {"reply": "x", "show_book_ids": [f["id"] for f in seen["facts"]][:4], "suggestions": [], "unanswered": ""}
chat.respond = fake_respond
def interp(prefs=None, intent="recommend", titles=(), author=None):
    chat.interpret = lambda conv, active, *a: {"intent": intent, "prefs": llm.clean_prefs(prefs or {}), "mentioned_titles": list(titles),
                                              "title_variants": [], "wants_new_options": False, "references": [],
                                              "author_request": author or {}}
def say(*msgs, ids=None):
    conv = [{"role": "user" if i % 2 == 0 else "assistant", "content": m} for i, m in enumerate(msgs)]
    for c in conv:
        if c["role"] == "assistant" and ids:
            c["book_ids"] = list(ids)
    return A.run_chat(conv, active=("fake", "m"))
books = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
avail = [b for b in books.values() if A.is_available(b)]

# ---------------------------------------------------------------- ages in words (case 14)
ok(R.ages_in("ولدي عمره عشر سنين ويحب الروايات المصورة") == [10], "«عمره عشر سنين» is 10")
ok(R.ages_in("قلت لك عشر سنين") == [10], "«عشر سنين» alone is 10")
ok(R.ages_in("بنتي ست سنوات") == [6] and R.kid_ages("بنتي ست سنوات") == [6], "«بنتي ست سنوات» is 6")
ok(R.ages_in("ابني اربعطعش سنه") == [14] and R.ages_in("عمرها سنتين") == [2], "«اربعطعش سنة» 14, «سنتين» 2")
ok(R.ages_in("my son is ten years old") == [10], "«ten years old» is 10")
ok(R.ages_in("عشر كتب") == [] and R.ages_in("عندي خمس روايات") == [], "«عشر كتب» / «خمس روايات» are counts, not ages")
ok(A.detect_audience(A.normalize("ولدي عمره عشر سنين ويحب الروايات المصورة")) == "children", "and the reader is a child")

# ---------------------------------------------------------------- a prize named «… طلاب الثانوية» (case 7)
_orig_other = honors.other_name_in
honors.other_name_in = lambda t: "جائزة غونكور لطلاب الثانوية" if "غونكور" in str(t) else None
q = "عندكم روايات فازت بغونكور طلاب الثانوية؟"
ok(A.detect_audience(A.normalize(q)) is None, "«غونكور طلاب الثانوية»: no teenage reader read from the prize's name")
ok(A.chat_audience([q], {"audience": "young_adult", "genres": ["رواية"]}, False) is None,
   "nor from the interpreter's «young_adult» for it")
ok(A.detect_audience(A.normalize("رواية لطالب ثانوي")) == "young_adult", "«لطالب ثانوي» without a prize is still a teenager")
honors.other_name_in = _orig_other

# ---------------------------------------------------------------- the start of a title (case 30)
ok(R.named_title("فيه رواية اسمها الساعة الخامسة شي كذا") == "الساعه الخامسه", "«اسمها الساعة الخامسة شي كذا» -> «الساعة الخامسة»")
ok(R.named_title("رواية اسمها مئة عام من العزلة او شي قريب") == "مئه عام من العزله", "«… او شي قريب» cut off")
ok(R.named_title("عندي ولد اسمه احمد") is None, "«ولدي اسمه أحمد» is not a title")
_t = next((b for b in avail if len(A.normalize(b["title"]).split()) >= 3), None)
if _t:
    _start = " ".join(A.normalize(_t["title"]).split()[:2])
    interp({"genres": ["رواية"]}, intent="clarify")
    out = say(f"فيه رواية اسمها {_start} شي كذا")
    ok(_t["book_id"] in [f["id"] for f in seen["facts"]], f"«اسمها {_start} شي كذا» finds «{_t['title']}»")

# ---------------------------------------------------------------- another spelling of a foreign title (case 29)
ok(R.title_skeleton("مرتفعات ويذرنغ") == R.title_skeleton("مرتفعات ويذرنج") == R.title_skeleton("مرتفعات وذرينغ"),
   "«ويذرنغ» = «ويذرنج» = «وذرينغ»")
ok(R.title_skeleton("بيت الطين") != R.title_skeleton("بنت الطين"), "«بيت» and «بنت» stay apart")

# ---------------------------------------------------------------- new releases (case 44)
ok(A.new_release_said("وش أحدث كتاب وصلكم؟") and A.new_release_said("what's the newest book you have"), "«أحدث كتاب وصلكم» asks for new releases")
y = time.localtime().tm_year
ok(A.years_said("اقصد الكتب اللي صدرت هالسنة") == (y, y), "«صدرت هالسنة» is this year")
ok(A.years_said("قريته هالسنة وعجبني") == (None, None), "«قريته هالسنة» is not a publication year")
ok(A._year4(2028) is None and A._year4(y) == y, "a year after next year (book 4350: 2028) is a slip, never «the newest»")

# ---------------------------------------------------------------- «مين فاز فيها سنة ٢٠٢٢؟» (case 35)
ok(A._WHO_WON.search(A.normalize("ومين فاز فيها سنة ٢٠٢٢؟")) and not A._WHO_WON.search(A.normalize("ابي رواية فازت بجائزة")),
   "«مين فاز» asks for the winner; «رواية فازت» does not")

# ---------------------------------------------------------------- the library in numbers (case 5)
ok(R.overview_asked("كم كتاب عندكم تقريبًا؟ ووش أقل قسم فيه كتب؟"), "«… ووش أقل قسم فيه كتب؟» as the visitor wrote it")
ok(R.parent_said("أنا معلمة وأبي كتاب عن إدارة الصف، لي أنا مو للطالبات") and R.parent_said("انا ام لطفلين")
   and not R.parent_said("انا ابي رواية"), "«أنا معلمة» / «أنا أم» is the grown-up reader; «أنا أبي رواية» is not")
ok(R.overview_asked("كم كتاب عندكم تقريبًا؟ ووش أقل قسم") and R.overview_asked("كم رواية عندكم؟")
   and R.overview_asked("كم كتاب تاريخ عندكم"), "«كم كتاب … ووش أقل قسم»، «كم رواية عندكم؟»، «كم كتاب تاريخ»")
ok(not R.overview_asked("كم كتاب عندكم لنجيب محفوظ؟") and not R.overview_asked("كم قصة عن الحيوانات عندكم"),
   "an author or a subject is not the shelves in numbers")
ok("وأقلها هو الأخير" in A.library_overview(books), "the overview names the smallest category")

# ---------------------------------------------------------------- «رواية … بس مش كتاب تاريخ» (case 50)
interp({"genres": ["رواية"]})
out = say("بدي رواية بس مش كتاب تاريخ")
ok("الرواية التاريخية رواية وليست كتاب تاريخ" in seen["guidance"] and "لا تعرض شيئًا منها" not in seen["guidance"],
   "the reply is told the books respect the wish and a historical novel is a novel")

# ---------------------------------------------------------------- the English edition of an author's books (case 26.2)
_by = {}
for b in avail:
    _by.setdefault(b.get("author"), []).append(b)
pair = next(((a, bs) for a, bs in _by.items() if a and any((x.get("language") or "ar") == "en" for x in bs)
             and any((x.get("language") or "ar") != "en" for x in bs)), None)
if pair:
    a, bs = pair
    areq = {"mode": "works", "name_as_written": a, "full_name": a, "aliases": []}
    interp({"authors": [a]}, author=dict(areq))
    say("وش عندكم لـ" + a + "؟")
    interp({"authors": [a], "original_languages": ["en"]}, author=dict(areq))
    out = say("وش عندكم لـ" + a + "؟", "x", "ابي بالانجليزي")
    langs = {(books[f["id"]].get("language") or "ar") for f in seen["facts"] if f["id"] in books}
    ok(langs == {"en"}, f"«ابي بالانجليزي» after {a}: his English copies only")

# ---------------------------------------------------------------- a book's own prize first (case 8)
class _P(dict):
    pass
_orig_m, _orig_n = honors.matches_award, honors.newest_award_year
honors.matches_award = lambda bid, *a, **k: (["فاز بجائزة هوغو 1966"] if bid == 1 else ["مؤلفه فاز بجائزة هوغو 2012"])
honors.newest_award_year = lambda bid, *a, **k: 1966 if bid == 1 else 2012
base = {"awards": ["hugo"], "genres": set(), "length": None, "original_languages": set(), "translated": None, "tokens": [], "authors": [], "adaptation": None}
s_own = A.score_book({"book_id": 1, "title": "x", "author": "y"}, dict(base))[0]
s_auth = A.score_book({"book_id": 2, "title": "x", "author": "y"}, dict(base))[0]
honors.matches_award, honors.newest_award_year = _orig_m, _orig_n
ok(s_own - 1.5 > s_auth, "a book that won (1966, English copy) still ranks before a book whose author won (2012)")

# ---------------------------------------------------------------- a parent asking for himself (cases 4, 13)
interp({})
out = say("ابي كتب عن التعامل مع عناد الأطفال، لي أنا كأب")
ok(not any(books[f["id"]].get("genre") == "رواية" for f in seen["facts"] if f["id"] in books), "no novel for the father asking for himself")

# ---------------------------------------------------------------- «تقصد … ولا …؟» answered by what he is known for (case 27)
ok(author_flow._picked_by_record("ابي رواية رعب", ["أ", "ب"]) is None, "a new request is not an answer to the question")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
