"""[QADFIX18] The library inbox of 2026-10-24, items 2, 3, 5, 6 (Ibrahim 2026-10-24: «صحح الخطأ»، «نفذ الاقتراح»).
Checks without the model: an author's prize for another work, the interpreter's «qualities», the responder rules, the
country that has books but none fits the prize wish, and Arabic copies first for an Arabic visitor."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm as L, chat as C, app as A, honors as H
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# --- rules given to the model (items 2, 3, 5, 6)
ok("qualities" in L.PREFS_SCHEMA["required"] and L.QUALITIES == ["simple_style", "original"], "5: the interpreter reads how a book is written")
ok("never put in unsupported_requests" in L.PREFS_RULES, "5: a quality is never searched as a subject")
ok(L.clean_prefs({"qualities": ["simple_style", "x"]})["qualities"] == ["simple_style"], "5: only known qualities are kept")
ok("«رواية عربية»" in C.RESPOND_SYSTEM_AR and "Arabic novel" in C.RESPOND_SYSTEM_EN, "2: «رواية عربية» only for a work written in Arabic")
ok("لا تقل «عندنا كتابان»" in C.RESPOND_SYSTEM_AR and "never state a count" in C.RESPOND_SYSTEM_EN, "3: facts are not counted as all we have")
ok("فابدأ بالكتب اللي نسختها عربية" in C.RESPOND_SYSTEM_AR, "6: Arabic copies first for an Arabic visitor")
_src = open(os.path.join(os.path.dirname(A.__file__), "app.py"), encoding="utf-8").read()
ok("عندنا كتب متوفرة لمؤلفين من هناك، لكن ما فيها كتاب يناسب بقية طلبه" in _src, "2: a country with books but none fitting is said as such")
ok("هذه الأعداد ضمن شروطه الأخرى الحالية" in _src, "3: the prize counts are said as within the other wishes, beside the whole library's")

# --- item 3: an author's prize for another named work is not this book's (for «رواية فائزة»)
con = models.get_connection()
H.ensure_table(con)
avail = [b for b in (dict(r) for r in A.list_books(only_active=False)) if A.is_available(b)]
b1, b2, b3, b4 = [b["book_id"] for b in avail[:4]]
# --- item 3: «جائزة للرواية» is a prize for the novel, not a prize named «للرواية»
for n, nm in enumerate(["جائزة ألف للرواية", "جائزة باء للرواية", "جائزة تاء للرواية", "جائزة ثاء للرواية", "جائزة جيم للرواية", "جائزة فحص ستوكر للرواية"]):
    con.execute("INSERT INTO book_awards (book_id, award_key, award_name, result, year, source) VALUES (?, 'other', ?, 'winner', ?, 'test')", (b4, nm, 2000 + n))
con.commit(); H.clear_cache()
ok(A.prize_named("أبي جائزة للرواية") is None, "3: «أبي جائزة للرواية» names no prize (a word many prizes share)")
ok(H.name_is_generic("للرواية") and not H.name_is_generic("فحص ستوكر"), "3: a shared word is generic; a real name is not")
ok(A.prize_named("أبي جائزة فحص ستوكر") is not None, "3: a prize asked for by its own name is still found")
con.execute("UPDATE books SET title='كتاب فحص الجائزة', author='مؤلف فحص الجوائز', genre='رواية', language='ar', quantity=3 WHERE book_id=?", (b1,))
cur = con.execute("INSERT INTO authors (name_ar, origin) VALUES ('مؤلف فحص الجوائز', 'الكويت')")
aid = cur.lastrowid
con.execute("DELETE FROM book_authors WHERE book_id=?", (b1,))
con.execute("INSERT INTO book_authors (book_id, author_id, role) VALUES (?, ?, 'author')", (b1, aid))
con.execute("INSERT INTO author_awards (author_id, award_key, award_name, result, year, work_title, source) VALUES (?, 'other', 'جائزة فحص', 'winner', 2001, 'عمل آخر للفحص', 'test')", (aid,))
con.commit()
H.clear_cache()
ok(not H.matches_award(b1, ["any"]), "3: «لوياثان»: the author's prize for another work does not make this book a prize winner")
con.execute("INSERT INTO author_awards (author_id, award_key, award_name, result, year, work_title, source) VALUES (?, 'nobel', 'جائزة نوبل للآداب', 'winner', 2010, NULL, 'test')", (aid,))
con.commit(); H.clear_cache()
ok(any(l.startswith("مؤلفه") for l in H.matches_award(b1, ["any"])), "3: a person's prize (no work named) still counts")
con.execute("DELETE FROM author_awards WHERE author_id=? AND award_key='nobel'", (aid,))
con.execute("INSERT INTO author_awards (author_id, award_key, award_name, result, year, work_title, source) VALUES (?, 'other', 'جائزة فحص ثانية', 'winner', 2003, 'كتاب فحص الجائزة', 'test')", (aid,))
con.commit(); H.clear_cache()
ok(bool(H.matches_award(b1, ["any"])), "3: the author's prize named for this very book counts")
con.execute("DELETE FROM author_awards WHERE author_id=?", (aid,))
# a list for the Kuwaiti book, no win
con.execute("INSERT INTO book_awards (book_id, award_key, award_name, result, year, source) VALUES (?, 'ipaf', 'الجائزة العالمية للرواية العربية (البوكر العربية)', 'longlist', 2021, 'test')", (b1,))
con.commit(); H.clear_cache()
ok(not H.matches_award(b1, ["any"]), "2: a longlist is not «حائزة على جائزة»")

# --- item 6: Arabic copies first for an Arabic visitor
con.execute("UPDATE books SET title='رواية فحص إنجليزية', author='كاتب فحص', genre='رواية', language='en', quantity=3 WHERE book_id=?", (b2,))
con.execute("UPDATE books SET title='رواية فحص عربية', author='كاتب فحص ثان', genre='رواية', language='ar', quantity=3 WHERE book_id=?", (b3,))
con.commit(); con.close()
for fn in ("clear_cache", "invalidate_cache"):
    if hasattr(A, fn):
        getattr(A, fn)()
H.clear_cache()
_raw = {"intent": "recommend", "preferences": {"genres": ["رواية"]}, "mentioned_titles": [], "title_variants": [], "wants_new_options": False,
        "reference_books": [], "author_request": {}, "avoid_topics": [], "order_by": "none", "reader_is_visitor": False,
        "reply_language": "ar", "whole_series": [], "first_publication": False, "publication_years": {"from": 0, "to": 0},
        "reader_age": 0, "physical_wishes": [], "author_countries": [], "about_shown": False, "copy_language": "any",
        "about_raising_child": False, "avoid_author_countries": [], "visitor_female": False}
_orig, _interp, _resp = L.complete_json, C.interpret, C.respond
seen = {}
def _fake_resp(conv, facts, guidance, active, *a, **k):
    seen["facts"], seen["guidance"] = json.loads(facts), guidance
    return {"reply": "تفضل.", "show_book_ids": [], "suggestions": [], "unanswered": "", "excluded_book_ids": []}
def run(raw, msgs):
    try:
        L.complete_json = lambda *a, **k: json.loads(json.dumps(raw))
        it = _interp([{"role": "user", "content": "x"}], active=("fake", "fake"))
    finally:
        L.complete_json = _orig
    C.interpret = lambda conv, active, *a, **k: json.loads(json.dumps(it, default=list))
    C.respond = _fake_resp
    return A.run_chat([{"role": "user", "content": m} for m in msgs], active=("fake", "m"))
try:
    run(_raw, ["ابي رواية"])
    langs = [A.list_books and f.get("copy_language") for f in seen["facts"]]
    first_en = next((n for n, f in enumerate(seen["facts"]) if f.get("copy_language") == "الإنجليزية"), None)
    first_ar = next((n for n, f in enumerate(seen["facts"]) if f.get("copy_language") == "العربية"), None)
    ok(first_ar is not None and (first_en is None or first_ar < first_en), f"6: an Arabic copy comes before an English one (ar {first_ar}, en {first_en})")
    _q = json.loads(json.dumps(_raw)); _q["preferences"] = {"genres": ["رواية"], "qualities": ["simple_style", "original"]}
    run(_q, ["ابي رواية غير مبتذلة وسردها بسيط"])
    ok("سردًا بسيطًا" in seen["guidance"] and "ما عندنا مقياس لهذا" in seen["guidance"], "5: both qualities get honest guidance")
    # AGY-20261024-02 (case 7.1): «ليالي ألف ليلة» (a novel) for «شي من نوع ألف ليلة وليلة», though the model guessed the
    # subgenre «حكايات» and the shelf «قصص قصيرة»
    _k = json.loads(json.dumps(_raw)); _k["mentioned_titles"] = ["ألف ليلة وليلة"]
    _k["preferences"] = {"genres": ["قصص قصيرة"], "subgenres": ["حكايات"]}
    _con = models.get_connection()
    _con.execute("UPDATE books SET title='ليالي ألف ليلة', author='كاتب فحص الليالي', genre='رواية', subgenres=NULL, language='ar', quantity=3, active=1 WHERE book_id=?", (b4,))
    _con.execute("UPDATE books SET quantity=0 WHERE title IN ('ألف ليلة وليلة', 'الف ليلة وليلة') AND book_id<>?", (b4,))
    _con.commit()
    _b4492 = _con.execute("SELECT book_id FROM books WHERE book_id=?", (b4,)).fetchone()
    _con.close()
    for fn in ("clear_cache", "invalidate_cache"):
        if hasattr(A, fn):
            getattr(A, fn)()
    if _b4492:
        _kid = _b4492[0]
        def _fake_k(conv, facts, guidance, active, *a, **k):
            seen["facts"] = json.loads(facts)
            return {"reply": "«ليالي ألف ليلة» من نوعه.", "show_book_ids": [_kid], "suggestions": [], "unanswered": "", "excluded_book_ids": []}
        try:
            L.complete_json = lambda *a, **k: json.loads(json.dumps(_k))
            _itk = _interp([{"role": "user", "content": "x"}], active=("fake", "fake"))
        finally:
            L.complete_json = _orig
        C.interpret = lambda conv, active, *a, **k: json.loads(json.dumps(_itk, default=list))
        C.respond = _fake_k
        _o = A.run_chat([{"role": "user", "content": "ابي شي من نوع الف ليلة وليلة"}], active=("fake", "m"))
        ok(any(f["id"] == _kid for f in seen["facts"]), "7.1: «ليالي ألف ليلة» is in facts despite the guessed subgenre «حكايات»")
        ok(_kid in [b["book_id"] for b in _o["books"]], "7.1: its card is kept despite the guessed shelf «قصص قصيرة»")
    else:
        ok(True, "7.1: «ليالي ألف ليلة» not in this copy (skipped)")
finally:
    C.interpret, C.respond = _interp, _resp

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
