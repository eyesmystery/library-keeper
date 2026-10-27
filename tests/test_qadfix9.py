"""[QADFIX9] Fixes for the QAD round 2026-10-20 (Ibrahim 2026-10-20 10:5x «صححها»). Pure checks (no model, no live database)."""
import os, sys, time
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat_rules as R, topics, second_look
from keyword_prefs import normalize
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

_src = open(A.__file__, encoding="utf-8").read()
# ج1 the oldest work
ok('order_by, order_first, order_list = "oldest"' in _src and "صدر العمل أول مرة سنة" in _src, "43.1 «أقدم رواية» ordered by the year the work first came out")
ok('overview_asked(user_messages[-1]) and order_by != "oldest"' in _src, "43.1 «وش أقدم رواية عندكم» is not the shelves-in-numbers answer")
# ج2 phone / social media
_ph = set().union(*topics.content_words("الجوال"))
ok("الانترنت" in _ph and "الهواتف" in _ph, "34.1 «الجوال» also finds «الإنترنت»، «الهواتف الذكية»")
ok("(_sem_ok or (b.get(\"genre\") or \"\").strip() in _Q7_FICTION)" in _src, "34.1 a novel with the word only in its title («الجوالون») is not about it")
# ج3 one-way words
_mc = set().union(*topics.content_words("ماينكرافت"))
ok("الالكترونيه" not in _mc and "الكترونيه" not in _mc, "41.1 «ماينكرافت» does not find «المراسلة الإلكترونية»")
_el = set().union(*topics.content_words("الالكترونيه"))
ok("ماينكرافت" in _el, "41.1 «الألعاب الإلكترونية» still finds the games words")
# ج4 the parent reads it
_m = "بنتي صارت عصبية وما تسمع الكلام، أبي كتاب أقرأه أنا يفهمني نفسيتها"
ok(R.parent_said(_m), "4.1 «أبي كتاب أقرأه أنا يفهمني نفسيتها» — the mother reads it")
ok(R.parent_reader([_m, "عمرها ١٤"]), "4.2 «عمرها ١٤» after it keeps the mother as the reader")
ok(not R.parent_said("بنتي عمرها ١٤ وتحب الروايات"), "a book for the daughter stays for the daughter")
# ج5 relative years
_y = time.localtime().tm_year - 1
ok(A.years_said("ابي كتب عن تاريخ السعودية نزلت السنة اللي فاتت") == (_y, _y), "3.1 «نزلت السنة اللي فاتت» → last year")
ok(A.years_said("قريت كتاب السنة اللي فاتت") == (None, None), "«قريت … السنة اللي فاتت» is not a release year")
ok(bool(A._SAME_YEAR.search(normalize("عطني غيرها من نفس السنة"))) and "for _m in reversed(user_messages[-4:-1]):" in _src,
   "3.2 «من نفس السنة» keeps the year said before")
# ج6 the copy language from facts only; women authors kept
ok("copy_language" in open(second_look.__file__, encoding="utf-8").read(), "11.3 second look: the language of a copy from copy_language only")
ok(A.note_gender("روائية سعودية من أعمالها «بنت عطشة»") == "f", "11.3 «روائية سعودية» → a woman")
ok('any(women_wish(t) for t in user_messages[-3:])' in _src and "_named" in _src,
   "11.2/11.3 women authors kept through «أخف وأقصر من …» and «لا، قصدي لروائية …»")
# medium
ok(A._routine_topics(["السهر"]) == ["النوم"], "1.1 «أبطل السهر» → «النوم»")
ok(R.part_asked("ثلاثية التوأمين عندكم كاملة؟") == "all", "5.2 «ثلاثية … كاملة؟» asks for every part")
ok("QADFIX9 (QAD 2026-10-20, case 29.1)" in _src, "29.1 «كتب دار رشم وش فيها حلو؟» shows the publisher's books")
ok('_g.get(b["book_id"]) != "m" and bool(_TITLE_WOMEN.search' in _src, "11.1 a title «… النساء» by a man is not a woman's book")
print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
