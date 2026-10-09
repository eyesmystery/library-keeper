"""Fixes for the daily audit's open findings (2026-10-02 / 2026-10-03), track FIX."""
import os, sys, json, sqlite3
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
os.environ["LIBRARY_KEEPER_RATE_FEEDBACK"] = "5"
import models, limits, llm, chat, relations, visitor_notes, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
con = sqlite3.connect(models.DB_PATH)
visitor = A.app.test_client()

# ---- every stated constraint holds for related books and for books shown earlier
relations.rebuild()
avail = {r[0] for r in con.execute("select book_id from books where active=1 and quantity>0")}
cards0 = models.get_profile_cards()
src = target = None
for (cand,) in con.execute("select distinct source_book_id from book_relations order by source_book_id"):
    first = (relations.for_books([cand]).get(cand) or [None])[0]
    if cand in avail and first and first["target_book_id"] in avail and first["target_book_id"] != cand \
            and A.age_allowed(cards0.get(first["target_book_id"]), None, ()):
        src, target = cand, first["target_book_id"]
        break
con.execute("update books set page_count=560 where book_id=?", (target,))
con.execute("update books set page_count=120 where book_id=?", (src,))
con.commit()
src_title = A.get_book(src)["title"]
seen = {}
def show_everything(conv, facts_json, guidance, active):
    seen["facts"] = json.loads(facts_json)
    rel = [r["id"] for f in seen["facts"] for r in f.get("related", [])]
    return {"reply": "x", "show_book_ids": rel + [f["id"] for f in seen["facts"]], "suggestions": []}
chat.respond = show_everything
def interpret(prefs, titles):
    chat.interpret = lambda conv, active: {"intent": "recommend", "prefs": llm.clean_prefs(prefs),
                                           "mentioned_titles": titles, "wants_new_options": False}
interpret({}, [src_title])
out = A.run_chat([{"role": "user", "content": "كلمني عن " + src_title}], active=("fake", "m"))
listed = [r["id"] for f in seen["facts"] for r in f.get("related", [])]
ok(target in listed, "without a length wish the related book is offered (the fixture is valid)")
interpret({"length": "short"}, [src_title])
out = A.run_chat([{"role": "user", "content": "أبغى كتاب قصير مثل " + src_title}], active=("fake", "m"))
listed = [r["id"] for f in seen["facts"] for r in f.get("related", [])]
ok(target not in listed and target not in [b["book_id"] for b in out["books"]],
   "«قصير»: a 560-page related book is neither listed nor shown")
ok(src in [b["book_id"] for b in out["books"]], "the book the visitor asked about by name is still shown")
interpret({"length": "short"}, [])
out = A.run_chat([{"role": "user", "content": "أبغى رواية"},
                  {"role": "assistant", "content": "هذي اقتراحات", "book_ids": [target]},
                  {"role": "user", "content": "أبغى شي قصير"}], active=("fake", "m"))
ok(target not in [b["book_id"] for b in out["books"]], "a long book shown earlier is not shown again after «قصير»")
ok(all(not A.is_long(b, None) or b["book_id"] == src for b in out["books"]), "no long book among the cards")

# ---- unexpected input answers 400, never 500
chat.interpret = lambda conv, active: None
for path in ("/api/chat", "/api/recommend", "/api/choice", "/api/feedback"):
    r = visitor.post(path, json=["a"])
    ok(r.status_code == 400, f"{path}: a JSON list -> 400")
    ok(visitor.post(path, data="{not json", content_type="application/json").status_code == 400, f"{path}: broken JSON -> 400")
r = visitor.post("/api/chat", json={"messages": [{"role": "assistant", "content": "x", "book_ids": 5},
                                                 {"role": "user", "content": "رواية"}]})
ok(r.status_code == 200, "chat: book_ids given as a number is ignored, no crash")
ok(visitor.post("/api/chat", json={"messages": "x"}).status_code == 400, "chat: messages as text -> 400")
ok(visitor.post("/api/recommend", json={"query": "رواية", "exclude": 7}).status_code == 200, "recommend: exclude as a number is ignored")
bid = con.execute("select book_id from books where active=1 and quantity>0 limit 1").fetchone()[0]
for bad in ([], {}, 5, None):
    ok(visitor.post("/api/report", json={"book_id": bid, "reason": bad}).status_code == 400, f"report: reason {bad!r} -> 400")
ok(visitor.post("/api/choice", json={"book_id": [bid]}).status_code == 400, "choice: book_id as a list -> 400")
# audit 2026-10-05: digits that are not ASCII, numbers past the database range, non-ASCII tokens
for bad in ("²", "١٢", 2 ** 70, str(2 ** 70), -1, 0, True, 1.5):
    ok(visitor.post("/api/choice", json={"book_id": bad}).status_code == 400, f"choice: book_id {bad!r} -> 400")
    ok(visitor.post("/api/report", json={"book_id": bad, "reason": "x", "note": "y"}).status_code == 400, f"report: book_id {bad!r} -> 400")
for bad in ("²", 2 ** 70):
    ok(visitor.post("/api/feedback", json={"rating": "perfect", "choice_id": bad, "choice_token": "x"}).status_code == 400, f"feedback: choice_id {bad!r} -> 400")
ok(visitor.post("/api/feedback", json={"rating": "perfect", "choice_id": 1, "choice_token": "رمز"}).status_code == 400, "feedback: a non-ASCII token -> 400")
limits.reset()
ok(visitor.post("/api/feedback", json={"rating": ["satisfied"]}).status_code == 400, "feedback: rating as a list -> 400")

# ---- ratings have their own allowance per address
limits.reset()
rating = next(iter(A.FEEDBACK_RATINGS))
made = [visitor.post("/api/choice", json={"book_id": bid}).get_json() for _ in range(7)]
codes = [visitor.post("/api/feedback", json={"rating": rating, "choice_id": m["choice_id"], "choice_token": m["choice_token"]}).status_code for m in made]
ok(codes[:5] == [201] * 5 and codes[5:] == [429, 429], "ratings: 5 accepted, then 429 (LIBRARY_KEEPER_RATE_FEEDBACK=5)")
limits.reset()
ok(visitor.post("/api/feedback", json={"rating": rating}).status_code == 400
   and visitor.post("/api/feedback", json={"rating": rating, "book_id": bid}).status_code == 400, "a rating without a choice is refused")
ok(len(made[0]["choice_token"]) == 32 and made[0]["choice_token"] != made[1]["choice_token"], "each choice has its own token")
limits.reset()

# ---- the inbox reaches notes beyond the first page
for i in range(visitor_notes.PAGE + 5):
    visitor_notes.add_suggestion(f"اقتراح رقم {i}")
m = login(A.app.test_client())
d = m.get("/api/admin/inbox?kind=suggestion&status=open").get_json()
ok(len(d["items"]) == visitor_notes.PAGE and d["total"] == visitor_notes.PAGE + 5 and d["has_more"] is True,
   "first page holds PAGE notes, total and has_more say there are older ones")
d2 = m.get(f"/api/admin/inbox?kind=suggestion&status=open&offset={visitor_notes.PAGE}").get_json()
ok(len(d2["items"]) == 5 and d2["has_more"] is False and d2["items"][-1]["note"] == "اقتراح رقم 0",
   "the next page returns the rest, down to the oldest note")
ok(not {n["note_id"] for n in d["items"]} & {n["note_id"] for n in d2["items"]}, "pages do not overlap")
ok(len(m.get("/api/admin/inbox?kind=suggestion&offset=abc").get_json()["items"]) == visitor_notes.PAGE, "a bad offset is read as 0")

# ---- model usage: a call with a corrective retry counts both answers
llm.last_call.clear()
llm._add_usage({"prompt_tokens": 100, "completion_tokens": 20})
llm._add_usage({"prompt_tokens": 200, "completion_tokens": 30})
ok(llm.last_call["usage"] == {"prompt_tokens": 300, "completion_tokens": 50} and llm.last_call["attempts"] == 2,
   "token counts of both answers are summed; two attempts recorded")
before = con.execute("select coalesce(sum(model_calls),0), coalesce(sum(tokens),0) from usage_daily").fetchone()
limits.record_call(dict(llm.last_call))
after = sqlite3.connect(models.DB_PATH).execute("select sum(model_calls), sum(tokens) from usage_daily").fetchone()
ok(after[0] - before[0] == 2 and after[1] - before[1] == 350, "usage_daily: +2 requests, +350 tokens")
limits.record_call({"usage": {"prompt_tokens": 10}})
after2 = sqlite3.connect(models.DB_PATH).execute("select sum(model_calls), sum(tokens) from usage_daily").fetchone()
ok(after2[0] - after[0] == 1 and after2[1] - after[1] == 10, "a call without a retry still counts as one request")

# ---- visitor page: the three client-side guards are present (behaviour is checked in a browser, not here)
js = open(os.path.join(os.path.dirname(os.getcwd()), "frontend", "app.js"), encoding="utf-8").read()
ok("if (askedIn !== conversationId) return;" in js and "pendingChat.abort()" in js, "a reply to an earlier conversation is dropped and its request cancelled")
ok("if (sentFrom === noteSeq) closeNote();" in js, "a finished send closes only the sheet it was sent from")
ok("choice_token: choiceToken" in js and "if (!choiceId) { showToast(t('choiceFailed')); return; }" in js and js.count("choiceFailed:") == 2, "an unrecorded choice does not move on to the rating screen (Arabic and English text)")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
