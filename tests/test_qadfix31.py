"""[QADFIX31] The errors of the QAD round 2026-11-08 (Ibrahim's standing word of 2026-11-06: QAD fixes go ahead): grave 1.3 «وأغلى كتاب
أطفال؟» (a «و» in front of the superlative), 6.1–6.2 «الأديبات» on the IPAF shortlist; medium 5.1 a teenager who refuses violence (the
QADFIX30b rule after the subject search), 44.2 «للطفل ولا للكبار؟» asked again for a 10-year-old, 46.1 a scientist's life; minor 4.1, 13.1,
36.1, 49.1. Each check uses the round's own wording and a fresh rewording."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import chat_rules as R
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()
src_r = open(R.__file__, encoding="utf-8").read()

# --- 1 (case 1.3, grave): «و / ف / ب» in front of a superlative
ok(R.dearest_asked("وأغلى كتاب أطفال؟") and R.dearest_asked("طيب والأغلى؟"), "1.3: «وأغلى كتاب أطفال؟»، «طيب والأغلى؟» are the most expensive")
ok(R.dearest_asked("فأغلى رواية عندكم كم؟") and R.dearest_asked("والأعلى سعرًا في الشعر؟"), "fresh: «فأغلى …»، «والأعلى سعرًا»")
ok(R.price_wish(["طيب والأرخص رواية عندكم؟"])[1] and R.shortest_asked("وأقصر وحدة فيهم؟") and R.longest_asked("والأطول؟") == "longest",
   "the same for «والأرخص»، «وأقصر»، «والأطول»")
ok(R.absolute_asked("وأغلى كتاب عندكم على الإطلاق؟") and not R.dearest_asked("وغالي علي") and not R.dearest_asked("هذا أغلى من 100 ريال؟"),
   "absolute with «و»; «غالي» and «أغلى من 100» are no superlative")
ok("[وفب]?(?:ال)?(?:اغلي|اغلا)" in src_r and "[وفب]?(?:ال)?اقصر" in src_r, "the prefix is in the patterns")

# --- 2 (cases 6.1–6.2, grave): «الأديبات» on the shortlist
ok(A.women_wish("عدّ لي الأديبات العربيات اللي ترشحن لقصيرة جائزة الرواية العربية العالمية، ولكل وحدة كتاب عندكم"),
   "6.1: «الأديبات» are women writers")
ok(A.women_wish("عندكم روايات لأديبة سعودية؟") and A.women_wish("أبي شي للمبدعات العربيات") and A.women_wish("مجموعات لقاصات خليجيات"),
   "fresh: «لأديبة»، «للمبدعات»، «لقاصات»")
ok(not A.women_wish("الروايات اللي وصلن القائمة القصيرة") and not A.women_wish("شي ثاني مو لنفس الكاتبة") and not A.women_wish("أبي رواية تاريخية"),
   "«وصلن» after novels, «نفس الكاتبة», no women: no women wish")
ok("_short31 = bool(_SHORTLIST_RX.search(" in src and '(_wac31.get("won_ids") or []) + (_wac31.get("short_listed_ids") or [])' in src,
   "6.1–6.2: the women's shortlisted books (the winners too) come first in facts, carried to «سمّهن»")
ok("الزائر يسأل عن القائمة القصيرة: عدد كتب الكاتبات اللي وصلتها" in src and "لا تقل إنك ما تقدر تعدّهن أو تسمّيهن" in src,
   "the guidance gives the number and the names")

# --- 3 (case 5.1, medium): the QADFIX30b rule after every path
ok("_out31 = {x[2][\"book_id\"] for x in scored if _ya_pred30b(x[2])}" in src, "5.1: adult books with no content record left out again after the subject search")
ok("and not (_ya_pred30b and f[\"id\"] in all_books and _ya_pred30b(all_books[f[\"id\"]])" in src, "… and in the final card check")
ok(R.reader_person(["my daughter is 9 and loves animals", "something with pictures maybe"]) == ("f", 9)
   and R.reader_person(["my son is nine", "x"]) == ("m", 9) and R.reader_person(["بنتي ٨ سنين"]) == ("f", 8),
   "replay fresh 204: «my daughter is 9» is her age (no toddlers' word books next turn)")
ok("_keep_par |= {x[2][\"book_id\"] for x in _ya_fit31} | {x[2][\"book_id\"] for x in ya_added30}" in src and "scored = _fit31 + [x for x in scored if x[2][\"book_id\"] not in _fid31]" in src,
   "the shelf books that fit come first and survive the subject-only filter")
_sc = [(9, [], {"book_id": 1, "s": 1, "k": 0}), (8, [], {"book_id": 2, "s": 1, "k": 0}), (5, [], {"book_id": 3, "s": 0, "k": 0})]
_k, _o = A.drop_unknown_adult30b(_sc, lambda b: b["s"], lambda b: b["k"], lambda b: False)
ok(_o == {3}, "QADFIX30b rule itself unchanged")

# --- 4 (cases 44.2–44.3, medium): no «للطفل ولا للكبار؟» for a refinement about the same child
ok('it["audience_carried31"] = _age26' in src and "(?:شي|شيء|اشياء\"\n                         r\"|something|anything) " in src,
   "44.2: «something with pictures, like comics» after books for the boy of 10 stays for him")
ok("«كتب تاريخ؟» after a child is still asked: Ibrahim 2026-10-08" in src, "Ibrahim's rule kept: a new kind of book is still asked")

# --- 5 (case 46.1, medium): a scientist's life
ok('unsupported = list(dict.fromkeys(unsupported + ["سيرة علمية", "العلم"]))' in src and 'it["scientist_bio31"] = True' in src,
   "46.1: «سيرة … لعالِم» also searches a scientist's life («عصر العلم»)")

# --- minor
ok("or \" \".join(normalize(u).split()) in (\"النوم\", \"نوم\"))]" in src and A._BEDTIME.search("ابي شي يريح راسي قبل النوم")
   and not A._SLEEP_PROBLEM.search(" ابي شي يريح راسي قبل النوم "), "13.1: «قبل النوم» for a grown-up is no subject (a sleep problem still is)")
ok('unsupported = list(dict.fromkeys(unsupported + ["نبطي"]))' in src, "36.1: «الشعر الشعبي» finds «نبطي» titles")
ok("جائزة نوبل للآداب تُمنح للكاتب عن مجمل أعماله" in src, "49.1: a Nobel question about a novel is corrected kindly from our records")
ok("_rk31 = _rk31.replace(" in src, "4.1: a title found only inside another card's longer title is not named")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
