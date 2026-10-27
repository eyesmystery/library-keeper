"""[QADFIX15] The two chat cases left after QADFIX14, found by the Codex audit and Antigravity on 2026-10-23 (Ibrahim
2026-10-24: «صلحهم بس لا تبني لسه»). Behaviour checks through run_chat with the model's two calls replaced (no model, no
live database):
- case 36.2 "Which of those is the shortest?": an order among the books just shown is not the wish «a short book»; the
  shown books stay valid cards, the shortest first, and a one-word English title in the reply counts as named.
- case 4.2 «دون كيخوته إسبانية مو من أمريكا اللاتينية!»: a book the visitor rules out (relation rejected, read by the
  model), or one the data shows is not from the region asked for, is never a card nor «an example of what he wants»."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm as L, chat as C, app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# --- the interpreter: a reference can be ruled out
ok("rejected" in C.REFERENCE_SCHEMA["properties"]["relation"]["enum"], "a reference book can be «rejected» by the visitor")
_raw = {"intent": "recommend", "preferences": {}, "mentioned_titles": ["دون كيخوته"], "title_variants": [], "wants_new_options": False,
        "reference_books": [{"title": "دون كيخوته", "author": "", "format": "unknown", "moods": [], "themes": [], "subgenres": [],
                             "known": False, "relation": "rejected"}],
        "author_request": {}, "avoid_topics": [], "order_by": "none", "reader_is_visitor": False, "reply_language": "ar",
        "whole_series": [], "first_publication": False, "publication_years": {"from": 0, "to": 0}, "reader_age": 0,
        "physical_wishes": [], "author_countries": [], "about_shown": False, "copy_language": "any"}
_orig, _interp = L.complete_json, C.interpret
def interpreted(raw):
    try:
        L.complete_json = lambda *a, **k: json.loads(json.dumps(raw))
        return _interp([{"role": "user", "content": json.dumps(raw, ensure_ascii=False)}], active=("fake", "fake"))
    finally:
        L.complete_json = _orig
it_rej = interpreted(_raw)
ok(it_rej["references"][0]["relation"] == "rejected", "the rejected relation is kept by the cleaner")

# --- a small catalog of our own on the test copy
con = models.get_connection()
avail = [b for b in (dict(r) for r in A.list_books(only_active=False)) if A.is_available(b)]
ids = [b["book_id"] for b in avail[:8]]
films = {ids[0]: ("Atonement", 351), ids[1]: ("Seabiscuit", 399), ids[2]: ("Pride and Prejudice", 435), ids[3]: ("Unbroken", 528)}
for bid, (t, pages) in films.items():
    con.execute("UPDATE books SET title=?, author='Test Author', page_count=?, language='en', genre='رواية' WHERE book_id=?",
                (t, pages, bid))
quix = ids[4]
con.execute("UPDATE books SET title='دون كيخوته', author='ميغيل دي ثربانتس', genre='رواية', quantity=3 WHERE book_id=?", (quix,))
others = ids[5:8]
for n, bid in enumerate(others):
    con.execute("UPDATE books SET title=?, author=?, genre='رواية', quantity=2 WHERE book_id=?", (f"رواية تجربة {n}", f"كاتب تجربة {n}", bid))
con.commit(); con.close()
for fn in ("clear_cache", "invalidate_cache"):
    if hasattr(A, fn):
        getattr(A, fn)()

seen = {}
def responder(reply, show):
    def fake_respond(conv, facts_json, guidance, active, *a, **k):
        seen["facts"] = json.loads(facts_json); seen["guidance"] = guidance
        return {"reply": reply, "show_book_ids": list(show), "suggestions": [], "unanswered": ""}
    return fake_respond

# --- case 36.2: "Which of those is the shortest?" after four film books
it36 = interpreted(dict(_raw, mentioned_titles=[], reference_books=[], order_by="shortest", about_shown=True, reply_language="en",
                        preferences={"length": "short"}))
C.interpret = lambda conv, active, *a, **k: json.loads(json.dumps(it36, default=list))
shown = list(films)
C.respond = responder('Atonement is the shortest at 351 pages. Seabiscuit is 399 pages, Pride and Prejudice is 435 pages, and Unbroken is 528 pages.',
                      [ids[1], ids[0], ids[2], ids[3]])
msgs = [{"role": "user", "content": "what books here got made into movies?"},
        {"role": "assistant", "content": "Here are four.", "book_ids": shown},
        {"role": "user", "content": "Which of those is the shortest?"}]
out = A.run_chat(msgs, active=("fake", "m"), language="en")
got = [b["book_id"] for b in out["books"]]
ok(got and got[0] == ids[0], f"36.2: the card of the shortest shown book comes first (got {got})")
ok(set(got) <= set(shown) and len(got) >= 2, "36.2: the shown books stay cards; none is dropped by «short» read from «shortest»")

# only two books shown: «among those» stays among those (replay case 103.2 «أيها الأقصر؟»)
C.respond = responder("Seabiscuit is the shorter.", [ids[1]])
out = A.run_chat([msgs[0], {"role": "assistant", "content": "Two.", "book_ids": [ids[1], ids[2]]},
                  {"role": "user", "content": "Which of those is the shortest?"}], active=("fake", "m"), language="en")
_g = seen.get("guidance", "")
ok("Seabiscuit" in _g and "Atonement" not in _g, "two books shown: the shortest is taken among those two, not over the whole stock")

# a one-word title the reply names, with no «…», counts for a book that was shown
C.respond = responder("Unbroken is the longest of them.", [])
out = A.run_chat(msgs[:2] + [{"role": "user", "content": "Which of those is the longest?"}], active=("fake", "m"), language="en")
ok(ids[3] in [b["book_id"] for b in out["books"]], "a one-word English title named in the reply gets its card when that book was shown")

# --- case 4.2: «دون كيخوته إسبانية مو من أمريكا اللاتينية!»
C.interpret = lambda conv, active, *a, **k: json.loads(json.dumps(it_rej, default=list))
C.respond = responder("صحيح، «دون كيخوته» لثربانتس إسباني، مو من أمريكا اللاتينية. عندنا «رواية تجربة 0» و«رواية تجربة 1».",
                      [quix, others[0], others[1]])
msgs = [{"role": "user", "content": "أبغى الأقدم بين روايات أمريكا اللاتينية اللي عندكم"},
        {"role": "assistant", "content": "…", "book_ids": [quix]},
        {"role": "user", "content": "دون كيخوته إسبانية مو من أمريكا اللاتينية! أبي من أمريكا الجنوبية بس"}]
out = A.run_chat(msgs, active=("fake", "m"))
got = [b["book_id"] for b in out["books"]]
ok(quix not in got, f"4.2: the book the visitor rules out is not a card, though the model chose it and the reply names it (got {got})")
ok(others[0] in got, "4.2: the other books chosen are still shown")
ok("استبعد" in seen.get("guidance", "") and "اقترحها أولًا" not in seen.get("guidance", ""),
   "4.2: the guidance says it is ruled out, and never asks to offer it as an example")
ok(any(f["id"] == quix for f in seen.get("facts") or []), "4.2: its facts stay, so the reply can say why it does not fit")

# --- the same book named as an example (not ruled out) is not treated as ruled out
it_ex = interpreted(dict(_raw, reference_books=[dict(_raw["reference_books"][0], relation="example")]))
C.interpret = lambda conv, active, *a, **k: json.loads(json.dumps(it_ex, default=list))
seen.clear()
C.respond = responder("عندنا «دون كيخوته».", [quix])
out = A.run_chat([{"role": "user", "content": "روايات كلاسيكية"}, {"role": "assistant", "content": "…", "book_ids": [quix]},
                  {"role": "user", "content": "أبي كلاسيكيات زي دون كيخوته"}], active=("fake", "m"))
ok("استبعد" not in seen.get("guidance", "") and quix in [b["book_id"] for b in out["books"]],
   "a book given as an example (relation example, no region asked) is not ruled out and keeps its card")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
