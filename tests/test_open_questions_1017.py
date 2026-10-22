"""[MGR] Open questions of 2026-10-17 (Ibrahim): sales -> the library's picks of the month, not other shops' lists;
who designed a book / its cover -> outside the library's field, not logged; a series' full list of parts from
backend/series_parts.json (sourced) for «كم جزء…» / «الجزء الثاني…». Pure checks, no model."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import no_data, series_parts
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# ---------------------------------------------------------------- design questions
for q in ("مين مصمم غلافه؟", "من صمم غلاف الطلياني؟", "مين المصممة اللي رسمت الغلاف؟", "مين رسام الغلاف", "Who designed the cover?",
          "وش اسم مصمم الغلاف"):
    ok(no_data.design_asked(q), f"«{q}» -> outside the library's field")
for q in ("كتب عن تصميم الغلاف", "ابغى كتاب عن التصميم", "رواية غلافها أزرق", "كتب تصميم جرافيك", "عندكم كتب رسم؟"):
    ok(not no_data.design_asked(q), f"«{q}» stays a reading request")
r, chips = no_data.design_answer()
ok("مو من اختصاص المكتبة" in r and "ما أعرف" in r and chips, "the design answer says it plainly and offers the book itself")

# ---------------------------------------------------------------- sales -> picks
r, chips = no_data.picks_answer(["أ", "ب"])
ok("اختياراتنا هذا الشهر" in r and "«أ»، «ب»" in r and "مبيع" in r, "a sales question is answered with «اختياراتنا هذا الشهر»")
src = open(os.path.join(os.path.dirname(no_data.__file__), "app.py"), encoding="utf-8").read()
i_d, i_p, i_n = src.index("no_data.design_asked(user_messages[-1])"), src.index("_month_picks(all_books):"), src.index("elif no_data.asked(user_messages[-1]):\n        _reply, _chips = no_data.answer")
ok(i_d < i_p < i_n, "design first, then picks, then the old «no sales data» answer when no picks are live")
seg = src[i_d:i_p]
ok("open_questions.record" not in seg, "a design question is not logged as unanswered")

# ---------------------------------------------------------------- series parts
data = json.load(open(series_parts.PATH, encoding="utf-8"))
ok(all(s.get("source", "").startswith("http") for s in data["series"]), "every series entry names its source (rule 12)")
kk = series_parts.for_book({"title": "اسم الريح", "series_name": "سجلات قاتل الملك"})
ok(kk and series_parts.count(kk) == 2 and kk["parts"][1]["title_en"] == "The Wise Man's Fear", "Kingkiller: 2 published parts, the second named")
ks = series_parts.for_book({"title": "عالم بلا نهاية", "series_name": "كينغسبريدج"})
ok(ks and series_parts.count(ks) == 5, "Kingsbridge: 5 books")
ok(series_parts.for_book({"title": "عمود نار", "series_name": ""}) is ks, "a part found by its Arabic title even without a series name")
ok(series_parts.for_book({"title": "رواية أخرى", "series_name": ""}) is None, "another book: no entry")
avail = lambda b: (b.get("quantity") or 0) > 0
ours = [{"book_id": 1, "title": "أعمدة الأرض", "series_position": 1, "quantity": 3},
        {"book_id": 2, "title": "عالم بلا نهاية", "series_position": 2, "quantity": 3},
        {"book_id": 3, "title": "عمود نار", "series_position": 3, "quantity": 0}]
line = series_parts.describe(ks, ours, avail)
ok("«أعمدة الأرض» (1989، متوفر عندنا)" in line and "«عمود نار» (2017، غير متوفر الآن)" in line
   and "«The Armour of Light» (2023، غير موجود عندنا)" in line and "تمهيد: «The Evening and the Morning»" in line,
   "the list says which parts we stock, which ran out and which we never had")

print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
