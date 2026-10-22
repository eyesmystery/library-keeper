"""[QADFIX6] Fixes for the QAD round 2026-10-18 (Ibrahim: «حلها»). Pure checks (no model, no live database)."""
import os, sys, time
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat_rules as R, honors, topics, second_look
from keyword_prefs import normalize
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# #1 Nobel with «فاز»: the author's own win counts; a shortlist stays the book's own result
_fake = {"book": {}, "author": {7: [{"award_key": "nobel", "result": "winner", "year": 2022, "award_name": None, "work_title": None}]},
         "adapt": {}, "book_authors": {1: [7]}}
honors._cache.update(at=time.time(), data=_fake)
ok(honors.matches_award(1, ["nobel"], results=["winner"]), "#1 «كاتبة فازت بنوبل»: the author's Nobel counts with results=['winner']")
ok(not honors.matches_award(1, ["nobel"], results=["shortlist", "winner"]), "#1 «القائمة القصيرة»: the author's prize does not count")
ok(honors.matches_award(1, ["nobel"]), "#1 no result said: unchanged")
honors._cache.update(at=0.0, data=None)
# #2 a book in stock is never called «غير متوفر»
_b = {2440: {"book_id": 2440, "title": "الظلام", "active": 1, "quantity": 3, "language": "ar"},
      9: {"book_id": 9, "title": "الجزيرة", "active": 1, "quantity": 0, "language": "ar"},
      1197: {"book_id": 1197, "title": "مهارات التواصل الذكي", "active": 1, "quantity": 2, "language": "ar"},
      2134: {"book_id": 2134, "title": "دليل الآباء", "active": 1, "quantity": 3, "language": "ar"}}
r, c = R.fix_false_unavailable("المتوفر «أدب رخيص». و«الجزيرة» و«الظلام» مو موجودتين عندي الآن، وما أقدر أؤكد توفرهما؛ تبغى شي ثاني؟", _b)
ok(c and "ما عندنا نسخ من «الجزيرة»" in r and "متوفرة من «الظلام»" in r and "مو موجودتين" not in r and r.endswith("تبغى شي ثاني؟"),
   "#2 «الظلام» in stock is said to be in stock, «الجزيرة» (none left) is not")
r, c = R.fix_false_unavailable("ما لقيت كتابًا واضحًا، والكتابان المذكوران في الرد السابق غير متوفرين بالعربية. من الموجود «س».", _b, [1197, 2134])
ok(c and "غير متوفرين" not in r and r.startswith("ما لقيت كتابًا واضحًا.") and "«س»" in r, "#2 «الكتابان المذكوران في الرد السابق غير متوفرين» about shown books in stock is dropped")
r, c = R.fix_false_unavailable("«الظلام» غير متوفرة بالإنجليزي، لكنها عندنا بالعربية.", _b)
ok(not c, "#2 «not in English» about an Arabic copy stays (true)")
ok(not R.fix_false_unavailable("«الجزيرة» غير متوفرة الآن.", _b)[1], "#2 a book really out of stock stays «غير متوفرة»")
ok("فلا تقل عن أي منها إنه غير متوفر" in second_look.note("x", {"rewrite": "y", "clarify": "z"}, "r", "ar", ["الظلام"]),
   "#2 the second look is told which books are in stock")
# #3 the parent stays the reader in the second look
_src = open(A.__file__, encoding="utf-8").read()
ok("chat_rules.parent_reader(_users) and not chat_rules.parent_said(used[\"rewrite\"])" in _src, "#3 «بنتي ١٤» after a parent's wish: the second reading keeps the parent")
ok(R.parent_reader(["ابي كتاب يساعدني أتفاهم مع بنتي المراهقة", "بنتي ١٤، وابي شي عملي"]), "#3 parent_reader holds for «بنتي ١٤»")
# #4 copies of the same work in other titles / languages
ok(A._LANGS_ASKED.search(normalize("كم نسخة عندكم من مزرعة الحيوان؟ وبأي لغات؟")) and not A._LANGS_ASKED.search(normalize("رواية عن مزرعة")),
   "#4 «بأي لغات» asks for the copies in every language")
# #5 negation and figurative wording are not subjects
ok(A._ESCAPE.search(normalize("متضايق من الشغل وابغى شي يفصلني عن الدنيا")) and not A._ESCAPE.search(normalize("كتاب عن الدنيا والدين")),
   "#5 «يفصلني عن الدنيا» is a wish to get away, «عن الدنيا والدين» a subject")
_m = [m.group(1) for m in A._NEG_LEAD.finditer(normalize("ابي كتاب تطوير ذات بس مو كله نصايح مكرره"))]
ok(_m and "مكرره" in _m[0], "#5 «مو كله نصايح مكررة» is read as what is not wanted")
ok(A._hz("نصائح") == A._hz("نصايح"), "#5 «نصائح» (the reading) = «نصايح» (the visitor's spelling)")
ok('prefs["genres"] = (set(prefs["genres"]) - {"تاريخ"}) | {"رواية"}' in open(A.__file__, encoding="utf-8").read(),
   "#12 «رواية تاريخية» is a novel, not history books")
# #6 video games
_cat = {i: {"title": f"كتاب {i}", "summary": f"نبذة رقم {i}"} for i in range(2, 60)}
_cat[1] = {"title": "Ready Player One", "keywords": '["Gaming", "Virtual-Reality"]', "summary": "x"}
topics._cache.update(key=None, at=0.0, index=None)
_idx = topics.index(_cat)
ok(topics.match(["الالكترونيه"], 1, _idx)[0] and topics.match(["ماينكرافت"], 1, _idx)[0], "#6 «الألعاب الإلكترونية / ماينكرافت» find a gaming book")
# #10 part of a title + author
ok(R.named_title("فيه رواية لبول أوستر اسمها «حدائق» شي، نسيت باقي العنوان") == "حدائق", "#10 «اسمها «حدائق» شي، نسيت باقي العنوان» -> «حدائق»")
ok("_his = {i: b for i, b in all_books.items()" in _src, "#10 with an author named, his titles in stock or not are searched")
# #11 «شي عربي» after a kind of book
ok(R.refine_only("شي عربي") and not R.refine_only("شي عن الحرب"), "#11 «شي عربي» only adds the language")
# #8 / #9 / #12 source checks
ok("ids = [i for i in ids if _named_in_reply(i)]" in _src, "#8 a clarifying question that names a book shows its card")
ok('it["no_age_fit"] = reader_age' in _src, "#9 no book for the child's age in that language: said, not hidden")
ok("_gk = [x for x in _base if x[2].get(\"genre\") in prefs[\"genres\"]]" in _src, "#12 «أقصر» keeps the kind of book (novels)")
print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
