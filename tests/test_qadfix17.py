"""[QADFIX17] The minor findings of the QAD round of 2026-10-24 and of the QADFIX16 replay (Ibrahim 2026-10-24: «ممتاز موافق»).
Checks without the model or the live database: the masculine address, one-word family names in another spelling, prizes given
only to novels, two parts of a series, a dropped book not named in the reply, and the interpreter / responder rules."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm as L, chat as C, app as A, chat_rules as R, author_flow as AF
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# --- 11: the visitor is addressed in the masculine unless she is a woman
t = R.masculine_address("ما عندي معلومة عن حجم الخط؛ تقدرين تسألين موظف المكتبة. أبشري، وش تفضّلين؟ وأنا متأكدة.")
ok("تقدر تسأل" in t and "أبشر،" in t and "تفضّل؟" in t, "45.2: «تقدرين تسألين / أبشري / تفضّلين» become «تقدر تسأل / أبشر / تفضّل»")
ok("متأكدة" in t, "Manar still speaks of herself in the feminine («متأكدة»)")
ok(R.masculine_address("وتحبين") == "وتحب", "a joined و is kept")
ok("visitor_female" in C.INTERPRET_SCHEMA["required"], "the interpreter reads whether the visitor is a woman")

# --- 7: one-word family names written another way
ok(AF._family_spelling("تشيكوف", ["أنطون تشيخوف"]), "27.1: «تشيكوف» is a spelling of «تشيخوف»")
ok(AF._family_spelling("Gibran", ["جبران خليل جبران", "Kahlil Gibran"]), "33.2: «Gibran» is the family name of Kahlil Gibran")
ok(not AF._family_spelling("سعد", ["إدوارد سعيد"]), "a short different name is not taken for another")
ok(not AF._family_spelling("الطيب", ["نجيب محفوظ"]), "an unrelated name is not a spelling")

# --- 6: prizes given only to novels
ok({"goncourt", "booker", "ipaf"} <= A.NOVEL_PRIZES, "Goncourt, Booker and the Arabic Booker are novel prizes")
ok(not A._novel_prize_ok({"book_id": -1}, {"awards": ["goncourt"]}, {"شعر"}), "the exception is only for a novels wish")

# --- 1, 2, 8, 9, 10: rules given to the model
ok("التربية" in C.INTERPRET_SYSTEM and "avoid_topics carries what was rejected earlier" in C.INTERPRET_SYSTEM,
   "41.3: a subject rejected earlier is kept in later requests")
ok("«عربي أو سعودي»" in C.INTERPRET_SYSTEM, "41.3: «عربي أو سعودي» is any Arab author")
ok("لا تقل «ما لقيت»" in C.RESPOND_SYSTEM_AR and "Never say you found nothing" in C.RESPOND_SYSTEM_EN,
   "11.1: no «I found nothing about it» while showing a book about it")
ok("بالجزء الأصغر رقمًا" in C.RESPOND_SYSTEM_AR and "lower-numbered part first" in C.RESPOND_SYSTEM_EN, "19.1: the earlier part first in the text")
_src = open(os.path.join(os.path.dirname(A.__file__), "app.py"), encoding="utf-8").read()
ok("وتذكر المصدر (مثل: «حسب Goodreads»)" not in _src and "إلا إذا سأل الزائر من وين المعلومة" in _src,
   "7.1 / 28.1: the outside source is not named unless asked")
ok("لا تقل إنك ما تقدر تحدد" in _src and "Never answer that you cannot say" in _src, "44.1: a comparison gets a plain answer")

# --- run_chat: series order and a dropped book's sentence
con = models.get_connection()
avail = [b for b in (dict(r) for r in A.list_books(only_active=False)) if A.is_available(b)]
ids = [b["book_id"] for b in avail[:4]]
con.execute("UPDATE books SET title='سلسلة فحص الجزء الرابع', author='كاتب السلسلة', genre='رواية', series_name='سلسلة الفحص', series_position=4, quantity=2 WHERE book_id=?", (ids[0],))
con.execute("UPDATE books SET title='سلسلة فحص الجزء الأول', author='كاتب السلسلة', genre='رواية', series_name='سلسلة الفحص', series_position=1, quantity=2 WHERE book_id=?", (ids[1],))
con.execute("UPDATE books SET title='رواية إنجليزية للفحص', author='كاتب آخر', genre='رواية', language='en', quantity=0 WHERE book_id=?", (ids[2],))
con.commit(); con.close()
for fn in ("clear_cache", "invalidate_cache"):
    if hasattr(A, fn):
        getattr(A, fn)()
_raw = {"intent": "recommend", "preferences": {"genres": ["رواية"]}, "mentioned_titles": [], "title_variants": [], "wants_new_options": False,
        "reference_books": [], "author_request": {}, "avoid_topics": [], "order_by": "none", "reader_is_visitor": False,
        "reply_language": "ar", "whole_series": [], "first_publication": False, "publication_years": {"from": 0, "to": 0},
        "reader_age": 0, "physical_wishes": [], "author_countries": [], "about_shown": False, "copy_language": "any",
        "about_raising_child": False, "avoid_author_countries": [], "visitor_female": False}
_orig, _interp, _resp = L.complete_json, C.interpret, C.respond
try:
    L.complete_json = lambda *a, **k: json.loads(json.dumps(_raw))
    it = _interp([{"role": "user", "content": "x"}], active=("fake", "fake"))
finally:
    L.complete_json = _orig
C.interpret = lambda conv, active, *a, **k: json.loads(json.dumps(it, default=list))
C.respond = lambda conv, facts, guidance, active, *a, **k: {
    "reply": "«سلسلة فحص الجزء الرابع» مشوقة. و«سلسلة فحص الجزء الأول» بداية السلسلة. أقترح «رواية إنجليزية للفحص» بعد. وش تفضّلين؟",
    "show_book_ids": [ids[0], ids[1], ids[2]], "suggestions": [], "unanswered": "", "excluded_book_ids": []}
out = A.run_chat([{"role": "user", "content": "رواية من سلسلة"}], active=("fake", "m"))
got = [b["book_id"] for b in out["books"]]
ok(got[:2] == [ids[1], ids[0]], f"19.1: the first part's card comes before the fourth (got {got})")
ok("رواية إنجليزية للفحص" not in out["reply"] and "سلسلة فحص الجزء الأول" in out["reply"],
   "24.3: the sentence offering a book the checks dropped goes; the rest stays")
ok("تفضّل؟" in out["reply"], "the reply's «تفضّلين» becomes «تفضّل» for a visitor not read as a woman")

C.interpret, C.respond = _interp, _resp
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
