"""[QADFIX5] Fixes for the QAD round 2026-10-17 (Ibrahim: «اصلحها كلها وابغى تفاصيل عن الاصلاحات بترقيم»).
Pure checks (no model, no live database)."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat_rules as R, author_flow, topics
from keyword_prefs import normalize
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# #1 bare years
ok(A.years_said("ابي رواية عربية طلعت ٢٠٢٤ أو ٢٠٢٥") == (2024, 2025), "#1 «طلعت ٢٠٢٤ أو ٢٠٢٥» -> 2024–2025")
ok(A.years_said("رواية صدرت سنة 2023") == (2023, 2023), "#1 «صدرت سنة 2023» -> 2023")
ok(A.years_said("يعني ما عندكم ولا رواية عربية جديدة؟ ٢٠٢٥ مثلا") == (2025, None), "#1 «جديدة؟ ٢٠٢٥ مثلا» -> from 2025")
ok(A.years_said("رواية 1984 طلعت فيلم") == (None, None), "#1 «رواية 1984» is a title, not a year")
ok(A.years_said("عندكم 1984؟") == (None, None), "#1 a bare number alone is no year wish")
ok(A.years_said("قبل ١٩٠٠") == (None, 1899) and A.years_said("بين 2010 و2015") == (2010, 2015), "#1 earlier forms unchanged")
# #2 year phrase is not a subject
ok(any(A.years_said("ما قبل ١٩٠٠")), "#2 «ما قبل ١٩٠٠» is read as a year wish (dropped from the subjects)")
# #3 English edition / Booker
ok(A.wanted_edition("ابغى رواية انقليزية فازت بالبوكر") == "en", "#3 «رواية انقليزية» -> a copy in English")
import llm
_ai = llm.clean_prefs({"awards": ["ipaf", "booker"]})
_ai["awards"] = ["ipaf", "booker"]
pr, *_ = A.prefs_from_ai(_ai, normalize("ابغى رواية انقليزية فازت بالبوكر"))
ok(pr["awards"] == ["booker"], "#3 Booker said with «انقليزية»: not the Arabic Booker as well")
# #5 named book for a child
show, note = A.named_age_note({"format": "novel", "confidence": {}}, "children", (), {"title": "العالم المفقود"}, 12)
ok(show and note and "رف الكبار" in note, "#5 a classic off the children's shelf, no flag: card shown with a note")
show, note = A.named_age_note({"format": "novel", "confidence": {("sensitive_content", "violence"): (3, True)}, "severity": {"violence": 2}}, "children", (), {"title": "x"}, 12)
ok((not show) and note and "لا يناسب" in note, "#5 a flagged book: no card, the model says why")
ok(A.named_age_note({"format": "children", "confidence": {}}, "children", (), {"title": "x"}, 6) == (True, None), "#5 a children's book: nothing to add")
# #9 parent reader
ok(R.parent_said("ابي دليل عملي للتعامل مع المراهقين") and R.parent_said("كتاب عن تربية الأبناء"), "#9 a guide for dealing with teenagers: the parent reads it")
ok(not R.parent_said("رواية للمراهقين"), "#9 «رواية للمراهقين» stays a book for teenagers")
# #8 gore
ok(A._GORE_NOT.search(normalize("a cozy mystery, nothing gory")) and A._GORE_NOT.search(normalize("رواية غموض بدون دم")), "#8 «nothing gory / بدون دم» is content to avoid")
ok(not A._GORE_NOT.search(normalize("رواية عن حرب دموية")), "#8 a bloody war asked for is not refused")
# #11 space
ok(topics.content_words("الفضاء")[0] & {"كون", "الكون"}, "#11 «الفضاء» also finds books about «الكون»")
# #12 full-name author we lack
q = author_flow.near_question("يوسف زيدان", ["جرجي زيدان", "رشا زيدان"])
ok(q.startswith("ما عندنا كتب لـيوسف زيدان الآن، ") and author_flow.NEAR_RE.search(q).group("ar") == "يوسف زيدان", "#12 «ما عندنا كتب لـيوسف زيدان الآن» and the answer is still read back")
ok(author_flow.near_question("زيدان", ["جرجي زيدان"]).startswith("ما عندنا مؤلف باسم زيدان، "), "#12 a bare family name keeps the old wording")
# #14 every author link
ok(callable(A.book_author_map) and isinstance(A.book_author_map(), dict), "#14 book_author_map reads every author link")
# #19 English reply without Arabic film titles
import re
ok(re.sub(r"\s*«[^»]*[؀-ۿ][^»]*»", "", "film «أغنياء آسيويون مجانين» (2018)") == "film (2018)", "#19 Arabic film title dropped in English")
# Ibrahim 2026-10-17: «انقليزي وانجليزي كلها نفس الشيء» — both spellings mean a copy in English
for _q in ("ابغى رواية انجليزية", "ابغى رواية انقليزية", "عادي لو انقليزي", "عادي لو انجليزي", "عندكم نسخة انقليزية؟",
           "عندكم نسخة انجليزية؟", "قصة انقليزية سهلة", "قصة انجليزية سهلة", "روايات انقليزي", "روايات انجليزي"):
    ok(A.wanted_edition(_q) == "en", f"#3 «{_q}» -> a copy in English")
for _a, _b in (("روايه انقليزيه", "روايه انجليزيه"), ("ولو انقليزي", "ولو انجليزي"), ("طبعه انقليزيه", "طبعه انجليزيه")):
    ok(_a in A._EDITION["en"] and _b in A._EDITION["en"], f"#3 both spellings listed: «{_a}» / «{_b}»")
ok(A.wanted_edition("رواية انقليزية مترجمة") is None and A.wanted_edition("رواية انجليزية مترجمة") is None
   and A.wanted_edition("ابي نسخة انقليزية مو مترجمة") == "en", "#3 «مترجمة» keeps «رواية انجليزية/انقليزية» the original language")
ok(all(A._LIVELY.search(normalize(x)) for x in ("تاريخ؟ بس مو ممل", "ابي تاريخ بس بأسلوب ممتع زي الحكايات"))
   and not A._LIVELY.search(normalize("كتب تاريخ الدولة العثمانية")), "#7 lively-history wishes recognised")
# #7 history that is not boring: history books and historical novels too
_src = open(A.__file__, encoding="utf-8").read()
ok('| {"تاريخ", "رواية"}' in _src and '["رواية تاريخية"]' in _src and "الروايات التاريخية" not in "x",
   "#7 «تاريخ بس مو ممل» -> history + novels, historical novels ranked first (soft subgenre)")
ok(any("روايات تاريخية" in part[:900] for part in _src.split('if it.get("lively_history"):')[1:]), "#7 the guidance asks for historical novels too")
ok("_hn[i:i + 1]" in _src, "#7 history books and historical novels are interleaved; other novels left out")
# AGY-20261017-01: a test script never opens the real project's database (a folder with .git); run_all's copy is fine
import tempfile
ok(models._guard_live_db() is None, "guard: the throwaway copy of run_all is allowed")
_proj = tempfile.mkdtemp(); os.makedirs(os.path.join(_proj, ".git")); os.makedirs(os.path.join(_proj, "data"))
_old = models.DB_PATH; models.DB_PATH = os.path.join(_proj, "data", "library.db")
try:
    models._guard_live_db(); _blocked = False
except RuntimeError:
    _blocked = True
models.DB_PATH = _old
ok(_blocked, "guard: a test script on a database inside a git project folder is refused")
print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
