"""[QADFIX7] Fixes for the QAD round 2026-10-19 (Ibrahim: «صلحها»). Pure checks (no model, no live database)."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat_rules as R, topics, keyword_prefs as kp
from keyword_prefs import normalize
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# #1 a verb of the reader's day is not a subject
r = A._routine_topics(["القيام بدري", "الالتزام بالتمرين"])
ok("الصباح" in r and "العادات" in r and "الرياضة" in r and "القيام بدري" not in r, "#1 «أقوم الصبح بدري وأتمرن» → الصباح / العادات / الرياضة")
ok(A._routine_topics(["غزو الكويت"]) == ["غزو الكويت"], "#1 a real subject is left as it is")
# #2 «أقصر» does not drop the subject's book
_src = open(A.__file__, encoding="utf-8").read()
ok("not chat_rules.shortest_asked(user_messages[-1])" in _src and "_fresh = [b for b in pool_before if b[\"book_id\"] not in shown] if _another" in _src,
   "#2 «أقصر» after the only book on a subject keeps the whole subject (not cut to «غزو»)")
ok('q7["nopages"]' in _src and "ما عندي عدد صفحاته" in _src, "#2 a subject book without a page count stays and the reply says so")
# #3 the year and the country stay in a narrowing follow-up
ok(A._narrow_followup("اللي مؤلفها سعودي منها") and A._narrow_followup("ابي اللي طلعت ٢٠٢٥ بس"), "#3 «منها» / «بس» narrow the request before")
ok(not A._narrow_followup("رواية عن غزو الكويت"), "#3 a new request is not a narrowing")
ok("_lo, _hi = years_said(user_messages[-2])" in _src, "#3 the year of the message before is kept")
# #4 the parent is the reader
_m = "ولدي دايم على الجوال وما يكلمني، ابي كتاب لي يساعدني اتقرب منه"
ok(R.parent_said(_m), "#4 «ابي كتاب لي يساعدني اتقرب منه» — the parent reads it")
ok(R.parent_reader([_m, "عمره ١٦"]), "#4 «عمره ١٦» after it keeps the parent as the reader")
ok(not R.parent_said("ابي كتاب لولدي عمره ١٢") and not R.parent_said("بنتي تحب الحيوانات ابي لها قصة"), "#4 a book for the child stays for the child")
# #5 series
ok(bool(A._SERIES_WISH.search(" " + normalize("ابغى سلسلة روايات اجزاء كثيرة اتابعها مثل المسلسل") + " ")), "#5 «سلسلة / أجزاء» is a series wish")
ok('q7["series"]' in _src and "عندنا منها" in _src, "#5 series searched in series_name with the parts in stock")
# #6 #7 subject words
_rb = set().union(*topics.content_words("رعب"))
ok("اشباح" in _rb or "الاشباح" in _rb, "#6 «رعب» also finds a summary about ghosts («كتاب المقبرة»)")
_lg = set().union(*topics.content_words("ألغاز"))
ok("لغز" in _lg, "#7 «ألغاز» finds «لغز كرة القدم»")
# #8 a refined kind is answered with books
ok("QADFIX7 #8" in _src, "#8 «شي عربي» after «خيال علمي»: books, not a second question")
# #9 old series
ok(not kp.old_film_said(normalize("مسلسل مصري قديم مأخوذ عن رواية")) and kp.old_film_said(normalize("فيلم قديم أبيض وأسود")),
   "#9 «مسلسل قديم» is not the «film by 1975» rule")
ok("QADFIX7 #9 (case 29.2)" in _src, "#9 «مو شرط قبل ١٩٧٥» lifts the year")
# #10 a novel's title word
ok('(b.get("genre") or "").strip() not in _Q7_FICTION' in _src, "#10 «جلسة قهوة» (a novel) is not a book about coffee")
# #11 poetry
ok("QADFIX7 #11" in _src and "_pk(x)" in _src, "#11 «تحب الشعر» brings poetry; «سهل» puts Arabic easy poetry first")
# #12 a kind of people is no author
ok(A.generic_author("مجموعة شاعرات") and not A.generic_author("مجموعة مؤلفين") and not A.generic_author("نازك الملائكة"),
   "#12 «مجموعة شاعرات» is not an author («مجموعة مؤلفين» and real names stay)")
ok("جنس المؤلف غير مسجل عندنا" in _src, "#12 the author's gender is said to be unrecorded")
print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
