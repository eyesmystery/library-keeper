"""[QADFIX10] Q-MGR-11 / AGY-20261020-01 (Ibrahim 2026-10-20 22:5x «بالنسبة للاصلاحات اصلحها الان»). Pure checks (no model, no live database)."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

_src = open(A.__file__, encoding="utf-8").read()
# 39.3 the subject carries across follow-ups
ok(A.follow_up_only("أقصر") and A.follow_up_only("شي ثاني") and A.follow_up_only("أرخص؟"), "39.3 «أقصر»، «شي ثاني»، «أرخص» are follow-ups only")
ok(not A.follow_up_only("رواية عن الهجرة والغربة"), "39.1 «رواية عن الهجرة والغربة» names a subject")
ok(not A.follow_up_only("أبي كتاب عن تاريخ الأندلس وشي قصير لو سمحت يكون"), "a long message is not a follow-up")
ok("_q10_src = next((t for t in reversed(user_messages[:-1]) if not follow_up_only(t)), None)" in _src,
   "39.3 the subject comes from the last message that said one")
# 11.2 the compared book
ok(A.compared_title("شي أخف وأقصر من سر الزعفرانة", "سر الزعفرانة"), "11.2 «أخف وأقصر من سر الزعفرانة» compares with it")
ok(A.compared_title("ابي شي غير الخيميائي", "الخيميائي"), "«شي غير الخيميائي» compares with it")
ok(not A.compared_title("عندكم سر الزعفرانة؟", "سر الزعفرانة"), "«عندكم سر الزعفرانة؟» asks for the book itself")
ok(A.compared_title("ابي روايات مثل سر الزعفرانة", "سر الزعفرانة"), "«مثل …» names the book for likeness: not offered itself (QADFIX11, QAD 2026-10-21 case 11.1)")
ok('if f["available"] and f["id"] not in _cmp_ids' in _src and "للمقارنة فقط" in _src, "11.2 the compared book is never a card; the reply is told so")
# 23.2 a much older / younger reader
ok('_age_jump and pprefs.get("audience") == "children" and age_fit(all_books[i], _age_now) != 1' in _src, "23.2 the sister's books for 5 do not come back for her 11-year-old brother")
ok(A.age_fit({"age_min": 5, "age_max": None}, 11) != 1, "23.2 «صديق ياسمين» (5+) is not «for» an 11-year-old")
ok(A.age_fit({"age_min": 9, "age_max": 14}, 11) == 1, "a 9–14 book is for an 11-year-old")
ok("if order_by and topic_phrases and not topic_strict and not _fits_topic(t[\"book_id\"]):".replace('\\"','"') in _src,
   "39.3 «أقصر» on a subject: no same-author book off the subject beside it")
ok("ids = [i for i in ids if i not in _cmp_ids]" in _src, "11.2 the compared book is not added back as «named in the reply»")
print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
