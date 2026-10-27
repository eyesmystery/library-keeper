"""[QADFIX19] The QAD round of 2026-10-25 (Ibrahim: «نفذ الاصلاحات»): a country is not a prize name (4.1), a work in stock under
its translated title (43.1), the named work of «على منوال …» in stock (11.1), a field narrower than its shelf kept as the subject
(19.1), refused kinds of story (38), the studio of an adaptation is not recorded (35.1), Arab authors read in English (44)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm as L, chat as C, app as A, honors as H
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# --- the interpreter's new readings
ok("subgenres_exclude" in L.PREFS_SCHEMA["required"], "38: the interpreter reads refused kinds of story")
ok(L.clean_prefs({"subgenres_exclude": ["واقعية سحرية"]})["subgenres_exclude"] == ["واقعية سحرية"], "38: refused kinds are kept")
ok("«فلسفة» first in unsupported_requests" in L.PREFS_RULES, "19.1: «فلسفة» stays the subject beside the shelf «فكر»")
ok("adaptation_company" in C.INTERPRET_SCHEMA["required"], "35.1: the studio named is read")
ok("original_languages stays empty (an\n  Arab author may write in English)" in C.INTERPRET_SYSTEM, "44: Arab authors in English are a nationality, not a language")

# --- 38: a refused kind keeps a book out
b = {"book_id": -7, "subgenres": json.dumps(["واقعية سحرية"]), "genre": "رواية"}
ok(A.matching_subgenres(b, {"subgenres_exclude": ["واقعية سحرية"]}, key="subgenres_exclude"), "38: «واقعية سحرية» matches the refused kind")
_src = open(os.path.join(os.path.dirname(A.__file__), "app.py"), encoding="utf-8").read()
ok('honors.other_name_in(user_messages[-1]) if prefs.get("awards") else None' in _src, "4.1: a prize name only when a prize is wished")

# --- run_chat checks on a throwaway copy
con = models.get_connection()
H.ensure_table(con)
avail = [b for b in (dict(r) for r in A.list_books(only_active=False)) if A.is_available(b)]
b1, b2, b3, b4, b5 = [b["book_id"] for b in avail[:5]]
cur = con.execute("INSERT INTO authors (name_ar, name_latin, origin) VALUES ('مؤلفة فحص الترجمة', 'Test Translator Author', 'المملكة المتحدة')")
aid = cur.lastrowid
con.execute("UPDATE books SET title='Pride Test Novel', author='Test Translator Author', genre='رواية', language='en', original_language='en', original_year=1813, quantity=2 WHERE book_id=?", (b1,))
con.execute("UPDATE books SET title='كبرياء فحص', author='مؤلفة فحص الترجمة', genre='رواية', language='ar', original_language='en', original_year=1813, quantity=3 WHERE book_id=?", (b2,))
for bid in (b1, b2):
    con.execute("DELETE FROM book_authors WHERE book_id=?", (bid,))
    con.execute("INSERT INTO book_authors (book_id, author_id, role) VALUES (?, ?, 'author')", (bid, aid))
con.execute("UPDATE books SET title='حي فحص يقظان', author='كاتب فحص المنوال', genre='رواية', language='ar', quantity=2 WHERE book_id=?", (b3,))
con.execute("INSERT INTO book_awards (book_id, award_key, award_name, result, year, source) VALUES (?, 'other', 'جائزة المغرب للكتاب', 'winner', 1988, 'test')", (b4,))
con.execute("UPDATE books SET title='رواية فحص مقتبسة', author='كاتب فحص الفيلم', genre='رواية', language='ar', quantity=2 WHERE book_id=?", (b5,))
con.execute("INSERT INTO book_adaptations (book_id, kind, title, year, source) VALUES (?, 'animated', 'فيلم فحص', 1990, 'test')", (b5,))
con.commit(); con.close()
for fn in ("clear_cache", "invalidate_cache"):
    if hasattr(A, fn):
        getattr(A, fn)()
H.clear_cache()
if hasattr(A, "_BOOK_AUTHOR_MAP"):
    A._BOOK_AUTHOR_MAP = None

_raw = {"intent": "recommend", "preferences": {"genres": ["رواية"]}, "mentioned_titles": [], "title_variants": [], "wants_new_options": False,
        "reference_books": [], "author_request": {}, "avoid_topics": [], "order_by": "none", "reader_is_visitor": False,
        "reply_language": "ar", "whole_series": [], "first_publication": False, "publication_years": {"from": 0, "to": 0},
        "reader_age": 0, "physical_wishes": [], "author_countries": [], "about_shown": False, "copy_language": "any",
        "about_raising_child": False, "avoid_author_countries": [], "visitor_female": False, "adaptation_company": ""}
_orig, _interp, _resp = L.complete_json, C.interpret, C.respond
seen = {}
def _fake_resp(conv, facts, guidance, active, *a, **k):
    seen["facts"], seen["guidance"] = json.loads(facts), guidance
    return {"reply": "تفضل.", "show_book_ids": [], "suggestions": [], "unanswered": "", "excluded_book_ids": []}
def run(raw, msgs, patch=None):
    try:
        L.complete_json = lambda *a, **k: json.loads(json.dumps(raw))
        it = _interp([{"role": "user", "content": "x"}], active=("fake", "fake"))
    finally:
        L.complete_json = _orig
    if patch:
        patch(it)
    C.interpret = lambda conv, active, *a, **k: json.loads(json.dumps(it, default=list))
    C.respond = _fake_resp
    return A.run_chat([{"role": "user", "content": m} for m in msgs], active=("fake", "m"))
try:
    run(_raw, ["روايات لكتاب من المغرب أو الجزائر أو تونس"])
    ok("جائزة المغرب" not in seen["guidance"], "4.1: «المغرب» in a country wish is not «جائزة المغرب للكتاب»")
    _q = json.loads(json.dumps(_raw)); _q["mentioned_titles"] = ["Pride Test Novel"]; _q["copy_language"] = "ar"; _q["intent"] = "about_book"
    run(_q, ["عندكم ترجمة عربية لرواية Pride Test Novel؟"])
    ok(any(f["id"] == b2 for f in seen["facts"]), "43.1: the Arabic copy by the same author goes to the model")
    ok("قبل أن تقول إنه ما عندنا" in seen["guidance"], "43.1: no «no Arabic copy» before checking the translated title")
    _r = json.loads(json.dumps(_raw)); _r["mentioned_titles"] = ["حي فحص يقظان"]
    def _refs(it):
        it["references"] = [{"title": "حي فحص يقظان", "author": "", "relation": "example", "known": False, "themes": [], "moods": [], "format": None}]
    run(_r, ["أبي شي على منوال حي فحص يقظان"], _refs)
    ok("نموذجًا لما يبغاه، وهو متوفر عندنا نفسه" in seen["guidance"], "11.1: the named work in stock is said and shown first")
    _d = json.loads(json.dumps(_raw)); _d["preferences"] = {"genres": ["رواية"], "adaptation": "animated"}; _d["adaptation_company"] = "ديزني"
    run(_d, ["رواية سووا منها فيلم كرتون ديزني"])
    ok("ما نسجّل الشركة" in seen["guidance"], "35.1: the studio is said to be unrecorded")
finally:
    C.interpret, C.respond = _interp, _resp

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
