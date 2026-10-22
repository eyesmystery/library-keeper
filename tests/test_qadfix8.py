"""[QADFIX8] The QADFIX7 replay follow-ups (cases 8.2, 39.2, 47.2/47.3, 49.1; Ibrahim 2026-10-19 22:4x «اصلحها كلها»).
Pure checks (no model, no live database)."""
import os, re, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
from keyword_prefs import normalize
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

_src = open(A.__file__, encoding="utf-8").read()
# 47.2: a book on harmful parents is not a parenting book
_harm = lambda t: bool(A._PARENT_HARM.search(normalize(t)))
ok(_harm("أسر مسممة") and _harm("الآباء المؤذون") and _harm("أبي الذي أكره سموم"), "47 «أسر مسممة» / «الآباء المؤذون» are books on harmful parents")
ok(not _harm("دليل الآباء لوسائل التواصل الاجتماعى") and not _harm("200 خطوة نحو التربية برفق") and not _harm("حين يتغير الوالدان يتغير كل شيء"),
   "47 real parenting books are not caught")
ok("and not _harm(b)" in _src and "_nonpar" in _src, "47 harmful-parents books and novels/memoirs get no «كتاب للوالدين» boost")
ok("12 < _age < 20" in _src and "(5 if _teen and _small else 0)" in _src, "47 «عمره ١٦»: books on babies and small children after the others")
ok('(3 if b["book_id"] in shown else 0)' in _src, "47 parenting books shown already come after the unseen ones")
# 8.2: a new series wish is not «the other parts of the book shown»
_new = lambda m: bool(A._SERIES_WISH.search(" " + normalize(m) + " ")) and bool(re.search(
    r"(?:^| )(?:ابي|ابغى|ابغي|ابغا|اريد|ودي|بدي|رشح|رشحي|رشحلي|عطني|اعطني|اقترح|اقترحي|want|recommend|suggest)(?: |$)", normalize(m)))
ok(_new("ابي سلسلة لها أجزاء مؤكدة"), "8.2 «ابي سلسلة لها أجزاء مؤكدة» is a new series wish")
ok(not _new("عندكم كل أجزاء مقبرة الكتب المنسية؟") and not _new("هل له أجزاء ثانية؟"), "8.3 «عندكم كل أجزاء …» / «هل له أجزاء» stay questions about a series")
ok('if _q7_new_series and part_wanted == "all":' in _src and 'it["intent"] = "recommend"   # QADFIX8 (case 8.2)' in _src,
   "8.2 the series found are shown as cards")
# 39.2: «أقصر» with a subject book without page count is subject-strict
ok('(_topic_now or _form_only or _more_only or q7.get("nopages"))' in _src, "39.2 no shorter book off the subject beside «السندباد الأعمى»")
# 49.1: the author's gender from the author record's own words
ok(A.note_gender("كاتبة من أعمالها كتاب «سوار قصيدة».") == "f", "49 «كاتبة …» → a woman")
ok(A.note_gender("كتابها نص قصير يتأمل فكرة …") == "f", "49 «كتابها …» → a woman")
ok(A.note_gender("شاعر صدرت له عدة دواوين") == "m", "49 «شاعر …» → a man")
ok(A.note_gender("") is None and A.note_gender("علاج نفسي عملي للقراء") is None, "49 no word for it → not known")
ok('q7["women"]' in _src and "مؤلفته امرأة حسب سجل المؤلفة عندنا" in _src and 'not q7.get("women")' in _src,
   "49 women's books first, no fillers beside them, the reply says the author records say so")
ok("they are still the answer (the «new options» pool had left them out)" in _src, "49.2 «لا اقصد شاعرة امرأة»: the women's books shown stay the answer")
ok("QADFIX8 (model replay, case 47.2)" in _src, "47.2 «عمره ١٦» after the father's request: parenting books, not a question about mood")
print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
