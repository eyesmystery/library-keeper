"""[AUDFIX 2026-10-25] Codex audit P2: a kind the visitor refused in words (QADFIX19 `subgenres_exclude`, case 38) must not come
back through the «a book of the kind of the named work» path (QADFIX18b, case 7.1): «ابي شي من نوع الف ليلة وليلة بس بدون واقعية
سحرية». Only the form guessed from the named work is eased; every refusal holds in each exception and in the final check.
Checks without the model (the interpreter's and the responder's answers are faked)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm as L, chat as C, app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

def clear():
    for fn in ("clear_cache", "invalidate_cache"):
        if hasattr(A, fn):
            getattr(A, fn)()

MR = "واقعية سحرية"
avail = [b for b in (dict(r) for r in A.list_books(only_active=False)) if A.is_available(b)]
kid = avail[0]["book_id"]
con = models.get_connection()
con.execute("UPDATE books SET title='ليالي ألف ليلة', author='كاتب فحص الليالي', genre='رواية', subgenres=?, language='ar', quantity=3, active=1 "
            "WHERE book_id=?", (json.dumps([MR], ensure_ascii=False), kid))
con.execute("UPDATE books SET quantity=0 WHERE title IN ('ألف ليلة وليلة', 'الف ليلة وليلة') AND book_id<>?", (kid,))
con.commit(); con.close(); clear()

# --- the single test: the guessed form is eased, a refusal is not
_b = next(dict(b) for b in A.list_books(only_active=False) if b["book_id"] == kid)
_ex = {"moods": set(), "weight": set()}
_pp = {"audience": None, "avoid": []}
_guess = {"genres": {"قصص قصيرة"}, "subgenres": ["حكايات"]}
ok(not A.meets_constraints(_b, None, _guess, _pp, _ex, {"قصص قصيرة"}), "the guessed shelf/subgenre keep it out of the ordinary path")
ok(A.meets_constraints(_b, None, _guess, _pp, _ex, {"قصص قصيرة"}, form=False), "form=False eases only the guessed shelf and subgenre")
_refused = dict(_guess, subgenres_exclude=[MR])
ok(not A.refusals_ok(_b, None, _refused, _ex), "refusals_ok: «بدون واقعية سحرية» refuses a magical-realism book")
ok(not A.meets_constraints(_b, None, _refused, _pp, _ex, {"قصص قصيرة"}, form=False), "form=False still holds the refused kind")
ok(A.refusals_ok(_b, None, _guess, _ex), "refusals_ok: nothing refused, nothing kept out")

# --- the whole chat: the same request with and without «بدون واقعية سحرية»
_raw = {"intent": "recommend", "preferences": {"genres": ["قصص قصيرة"], "subgenres": ["حكايات"]}, "mentioned_titles": ["ألف ليلة وليلة"],
        "title_variants": [], "wants_new_options": False, "reference_books": [], "author_request": {}, "avoid_topics": [], "order_by": "none",
        "reader_is_visitor": False, "reply_language": "ar", "whole_series": [], "first_publication": False, "publication_years": {"from": 0, "to": 0},
        "reader_age": 0, "physical_wishes": [], "author_countries": [], "about_shown": False, "copy_language": "any",
        "about_raising_child": False, "avoid_author_countries": [], "visitor_female": False}
_orig, _interp, _resp = L.complete_json, C.interpret, C.respond
seen = {}
def _fake_resp(conv, facts, guidance, active, *a, **k):
    seen["facts"] = json.loads(facts)
    # the responder asks for the book whatever the wishes: the final check alone decides
    return {"reply": "«ليالي ألف ليلة» من نوعه.", "show_book_ids": [kid], "suggestions": [], "unanswered": "", "excluded_book_ids": []}
def run(raw, msg):
    try:
        L.complete_json = lambda *a, **k: json.loads(json.dumps(raw))
        it = _interp([{"role": "user", "content": "x"}], active=("fake", "fake"))
    finally:
        L.complete_json = _orig
    C.interpret = lambda conv, active, *a, **k: json.loads(json.dumps(it, default=list))
    C.respond = _fake_resp
    seen.clear()
    out = A.run_chat([{"role": "user", "content": msg}], active=("fake", "m"))
    return it, out, [f["id"] for f in seen.get("facts") or []], [b["book_id"] for b in out["books"]]
try:
    _it, _o, _facts, _cards = run(_raw, "ابي شي من نوع الف ليلة وليلة")
    ok(kid in _facts and kid in _cards, f"QADFIX18b kept: without a refusal the book of the named work's kind is offered (cards {_cards})")
    _x = json.loads(json.dumps(_raw)); _x["preferences"]["subgenres_exclude"] = [MR]
    _it, _o, _facts, _cards = run(_x, "ابي شي من نوع الف ليلة وليلة بس بدون واقعية سحرية")
    ok(MR in (_it.get("prefs") or {}).get("subgenres_exclude", []), "the refusal reaches the request (subgenres_exclude)")
    ok(kid not in _facts, "«من نوع X بدون Y»: the refused kind is not put before the responder")
    ok(kid not in _cards, f"«من نوع X بدون Y»: the final check refuses its card even when the responder asks for it (cards {_cards})")
    # … nor when the reply only quotes its title (no card comes back from being named in «…»)
    _ask = {"ids": []}
    _fr = _fake_resp
    def _fake_quote(conv, facts, guidance, active, *a, **k):
        r = _fr(conv, facts, guidance, active); r["show_book_ids"] = _ask["ids"]; return r
    try:
        _fake_resp = _fake_quote
        _it, _o, _facts, _cards = run(_x, "ابي شي من نوع الف ليلة وليلة بس بدون واقعية سحرية")
    finally:
        _fake_resp = _fr
    ok(kid not in _cards, f"«من نوع X بدون Y»: quoting the refused book in the reply brings no card (cards {_cards})")
    # a refusal of another kind does not cost the book of the named work its place
    _y = json.loads(json.dumps(_raw)); _y["preferences"]["subgenres_exclude"] = ["خيال علمي"]
    _it, _o, _facts, _cards = run(_y, "ابي شي من نوع الف ليلة وليلة بس بدون خيال علمي")
    ok(kid in _cards, f"«من نوع X بدون Z» (another kind): the book is still offered (cards {_cards})")
finally:
    C.interpret, C.respond = _interp, _resp

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
