"""Prizes and screen adaptations, meaning search, questions we could not answer (2026-10-08)."""
import os, sys, json, sqlite3
for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LIBRARY_KEEPER_PROVIDER", "LIBRARY_KEEPER_MODEL", "LIBRARY_KEEPER_LLM"]:
    os.environ.pop(k, None)
sys.path.insert(0, os.getcwd())
import models, llm, chat, honors, open_questions, embeddings, keyword_prefs, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
con = sqlite3.connect(models.DB_PATH)
tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
ok({"book_awards", "author_awards", "book_adaptations", "open_questions", "book_embeddings"} <= tables, "migration creates the new tables")
ids = [r[0] for r in con.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 6")]
b1, b2, b3 = ids[0], ids[1], ids[2]
aid = con.execute("insert into authors (name_ar, research_status, created_at) values ('كاتب اختبار الجوائز','pending','x')").lastrowid
con.execute("insert into book_authors (book_id, author_id, role) values (?,?, 'author')", (b3, aid))
con.commit()
U = "https://example.org/src"

# ---------- validation and storage
try:
    honors.add("book_awards", b1, {"award_key": "nope", "result": "winner", "source_url": U}, "test"); ok(False, "unknown prize refused")
except ValueError: ok(True, "unknown prize refused")
try:
    honors.add("book_awards", b1, {"award_key": "ipaf", "result": "winner", "year": "20x2", "source_url": U}, "test"); ok(False, "bad year refused")
except ValueError: ok(True, "bad year refused")
try:
    honors.add("book_awards", b1, {"award_key": "other", "result": "winner", "source_url": U}, "test"); ok(False, "«other» needs a name")
except ValueError: ok(True, "«other» needs a name")
r1 = honors.add("book_awards", b1, {"award_key": "ipaf", "result": "winner", "year": 2022, "source_url": U}, "test")
ok(r1 and honors.add("book_awards", b1, {"award_key": "ipaf", "result": "winner", "year": 2022, "source_url": U}, "test") is None, "exact duplicate skipped")
honors.add("book_awards", b2, {"award_key": "ipaf", "result": "shortlist", "year": 2019, "source_url": U}, "test")
honors.add("author_awards", aid, {"award_key": "nobel", "result": "winner", "year": 1988, "source_url": U}, "test")
honors.add("book_adaptations", b2, {"kind": "film", "title": "فيلم تجربة", "year": 1970, "source_url": U}, "test")
honors.add("book_adaptations", b1, {"kind": "series", "title": "مسلسل تجربة", "year": 2001, "source_url": U}, "test")
h = honors.for_book(b1)
ok(h["awards"] == ["فاز بالجائزة العالمية للرواية العربية (البوكر العربية) 2022"] and h["adaptations"] == ["مسلسل «مسلسل تجربة» (2001)"], "labels for a book")
ok(honors.for_book(b3)["author_awards"] == ["مؤلفه فاز بجائزة نوبل للآداب 1988"], "the author's own prize is listed with each of his books")

# ---------- matching («البوكر ٢٠٢٢», «حائزة على نوبل», «تحولت لفيلم»)
ok(honors.matches_award(b1, ["ipaf"], 2022) and not honors.matches_award(b1, ["ipaf"], 2021), "prize + year")
ok(not honors.matches_award(b2, ["any"]) and honors.matches_award(b2, ["ipaf"]) == ["وصل إلى القائمة القصيرة للجائزة العالمية للرواية العربية (البوكر العربية) 2019"]
   and honors.matches_award(b1, ["any"]) == ["فاز بالجائزة العالمية للرواية العربية (البوكر العربية) 2022"], "«any prize» = won; a shortlist counts for the named prize, worded as such")
ok(honors.matches_award(b3, ["nobel"]) == ["مؤلفه فاز بجائزة نوبل للآداب 1988"] and not honors.matches_award(b1, ["nobel"]), "a Nobel counts through the author")
ok(honors.matches_adaptation(b2, "film") and not honors.matches_adaptation(b2, "series") and honors.matches_adaptation(b1, "screen"), "film / series / either")
ok(honors.to_english(honors.for_book(b1)["awards"][0]) == "Won International Prize for Arabic Fiction (Arabic Booker) 2022", "English wording")

# ---------- Ibrahim's prize lists (2026-10-11): wins and named prizes first, the branch on the staff card
_extra = [honors.add("author_awards", aid, {"award_key": "other", "award_name": "جائزة الكتاب الوطنية الأمريكية", "result": "winner", "year": 2020, "source_url": U}, "prize_lists"),
          honors.add("author_awards", aid, {"award_key": "booker", "result": "shortlist", "year": 2021, "source_url": U}, "prize_lists"),
          honors.add("author_awards", aid, {"award_key": "booker", "result": "winner", "year": 1990, "source_url": U}, "prize_lists")]
ok(honors.for_book(b3)["author_awards"] == ["مؤلفه فاز بجائزة البوكر 1990", "مؤلفه فاز بجائزة نوبل للآداب 1988",
   "مؤلفه فاز بجائزة الكتاب الوطنية الأمريكية 2020", "مؤلفه وصل إلى القائمة القصيرة لجائزة البوكر 2021"],
   "named prizes won come first (the chat gets only the first three), then other prizes, then lists")
ok(honors.award_label({"award_key": "pulitzer", "result": "winner", "year": 2007, "category": "الأدب القصصي"}) == "جائزة بوليتزر: فائز 2007 — الأدب القصصي"
   and honors.award_label({"award_key": "hugo", "result": "winner", "year": 1985, "category": "الرواية"}) == "جائزة هوغو: فائز 1985",
   "staff label names the branch (not the plain «الرواية»)")
for _r in _extra:
    honors.delete("author_awards", _r)
_t = honors.add("book_awards", b3, {"award_key": "zayed", "result": "winner", "year": 2018, "category": "الترجمة", "source_url": U}, "prize_lists")
_s = honors.add("author_awards", aid, {"award_key": "zayed", "result": "winner", "year": 2018, "work_title": "كتاب", "source_url": U}, "prize_lists")
_h3 = honors.for_book(b3)
ok(_h3["awards"] == ["فازت ترجمته بجائزة الشيخ زايد للكتاب 2018"] and _h3["author_awards"] == ["مؤلفه فاز بجائزة نوبل للآداب 1988"]
   and honors.to_english(_h3["awards"][0]) == "Its translation won Sheikh Zayed Book Award 2018",
   "a translation prize is the translation's; the person's row for the same prize is not repeated on the book")
honors.delete("book_awards", _t); honors.delete("author_awards", _s)

# ---------- interpretation fields and constraints
cp = llm.clean_prefs({"awards": ["ipaf", "booker", "bad"], "award_year": 2022, "adaptation": "film"})
ok(cp["awards"] == ["ipaf", "booker"] and cp["award_year"] == 2022 and cp["adaptation"] == "film", "clean_prefs keeps known prizes, year, adaptation")
ok(llm.clean_prefs({"award_year": 99, "adaptation": "none"})["award_year"] is None and llm.clean_prefs({"adaptation": "none"})["adaptation"] is None, "bad year / «none» dropped")
ok(all(k in llm.PREFS_SCHEMA["required"] for k in ("awards", "award_year", "adaptation")), "schema requires the new fields")
k = keyword_prefs.understand(["ابغى رواية فائزة بالبوكر 2022"])
ok(set(k["awards"]) == {"ipaf", "booker"} and k["award_year"] == 2022, "keyword fallback: «البوكر 2022»")
ok(keyword_prefs.understand(["رواية تحولت الى فيلم"])["adaptation"] == "film" and keyword_prefs.understand(["حائزة على نوبل"])["awards"] == ["nobel"], "keyword fallback: film / Nobel")
allb = [dict(x) for x in models.list_books(only_active=True)]
cards = models.get_profile_cards()
def wish(**kw): return A.prefs_from_ai(llm.clean_prefs(kw), "")
pr = wish(awards=["ipaf"], award_year=2022)
got, labels = A.constrain(allb, pr[0], pr[1], pr[2], cards)
ok({b["book_id"] for b in got} <= {b1} and "له صلة بـالجائزة العالمية للرواية العربية (البوكر العربية) 2022" in labels, "constraint: only the 2022 IPAF book, with its label")
pr = wish(awards=["any"])
got, _ = A.constrain(allb, pr[0], pr[1], pr[2], cards)
ok({b["book_id"] for b in got} <= {b1, b3}, "constraint «any prize» (won only)")
pr = wish(adaptation="film")
got, labels = A.constrain(allb, pr[0], pr[1], pr[2], cards)
ok({b["book_id"] for b in got} <= {b2} and "تحوّل إلى فيلم" in labels, "constraint: adapted into a film")
s, reasons = A.score_book(dict(next(b for b in allb if b["book_id"] == b1)), wish(awards=["ipaf"])[0])
ok(s >= 4 and reasons and reasons[0].startswith("فاز ب"), "a matching prize ranks and gives the reason")
fact = A.book_fact(dict(next(b for b in allb if b["book_id"] == b1)), cards.get(b1), "x")
ok(fact.get("awards") and fact.get("adaptations"), "chat facts carry awards and adaptations")
ok(A.localize_reason("حائز على جائزة نوبل للآداب") == "Won Nobel Prize in Literature", "English constraint label")

# ---------- chat with a scripted model
c = login(A.app.test_client())
c.post("/api/admin/llm", json={"provider": "openai", "model": "gpt-6-luna"})
os.environ["OPENAI_API_KEY"] = "k-test"
EMPTY = llm.clean_prefs({})
def prefs(**kw): d = {k: (v if v is not None else "any") for k, v in EMPTY.items()}; d.update(kw); return d
script, calls = {}, []
def fake_complete(system, messages, schema, max_tokens=700, active=None):
    kind = "interpret" if "intent" in schema["properties"] else "respond"
    calls.append({"kind": kind, "system": system, "schema": schema})
    step = script[kind]
    return step(system, messages) if callable(step) else step
llm.complete_json = fake_complete
def facts_of(system): return json.loads(system.split("<facts>\n")[1].split("\n</facts>")[0])
def show_all(system, messages):
    return {"reply": "تفضل", "show_book_ids": [f["id"] for f in facts_of(system)][:4], "suggestions": [], "unanswered": ""}
script["interpret"] = {"intent": "recommend", "preferences": prefs(awards=["ipaf"], award_year=2022), "mentioned_titles": [], "wants_new_options": False}
script["respond"] = show_all
r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "جائزة البوكر ٢٠٢٢"}]}).get_json()
ok([b["book_id"] for b in r["books"]] == [b1], "chat «البوكر ٢٠٢٢»: only the recorded winner is shown")
ok("اذكر الجائزة أو العمل المرئي كما هو مكتوب" in calls[-1]["system"] and facts_of(calls[-1]["system"])[0].get("awards"), "the model is told to name the prize from the facts")
ok("unanswered" in calls[-1]["schema"]["required"], "reply schema asks for unanswered questions")
script["interpret"] = {"intent": "recommend", "preferences": prefs(awards=["katara"]), "mentioned_titles": [], "wants_new_options": False}
r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "رواية فازت بكتارا"}]}).get_json()
ok(r["books"] == [] and "ما لقينا في سجلاتنا كتابًا متوفرًا" in calls[-1]["system"], "no recorded book: said plainly, no fillers")
rows, total = open_questions.list_rows("open")
ok(any(x["kind"] == "award" and "كتارا" in x["phrase"] for x in rows), "the unanswered prize is counted in «أسئلة بلا جواب»")
script["interpret"] = {"intent": "about_book", "preferences": prefs(), "mentioned_titles": [allb[0]["title"]], "wants_new_options": False}
script["respond"] = {"reply": "ما عندي تفاصيل عنه الآن", "show_book_ids": [], "suggestions": [], "unanswered": "من رسم الغلاف؟"}
c.post("/api/chat", json={"messages": [{"role": "user", "content": "من رسم غلاف الكتاب؟"}]})
c.post("/api/chat", json={"messages": [{"role": "user", "content": "من رسم غلاف الكتاب؟"}]})
rows, _ = open_questions.list_rows("open")
q = next((x for x in rows if x["kind"] == "question"), None)
ok(q and q["times"] == 2 and q["book_title"] == allb[0]["title"], "a question the model could not answer is counted (twice = one row, 2 times)")
ok("المعروف عن هذا العمل عمومًا" in chat.RESPOND_SYSTEM_AR and "لا تستعمل المعرفة العامة أبدًا في: الجوائز" in chat.RESPOND_SYSTEM_AR, "general-knowledge rule with its limits")
ok(chat.respond([{"role": "user", "content": "x"}], "[]", "g")["unanswered"] == "من رسم الغلاف؟", "respond returns the unanswered phrase")
script["respond"] = {"reply": "تفضل", "show_book_ids": [], "suggestions": []}
ok(chat.respond([{"role": "user", "content": "x"}], "[]", "g")["unanswered"] == "", "a model that omits it still works")
open_questions.record("topic", "  " + "س" * 200)
ok(all(len(x["phrase"]) <= 80 for x in open_questions.list_rows("all")[0]), "phrases are capped at 80 characters")

# ---------- admin: book card, add / delete, author awards, question inbox
d = c.get(f"/api/admin/books/{b1}").get_json()
ok(d["honors"]["awards"] and d["honors"]["adaptations"] and d["honor_options"]["awards"], "book card carries prizes, adaptations and the options")
res = c.post(f"/api/admin/books/{b1}/honors", json={"type": "award", "award_key": "zayed", "result": "longlist", "year": 2020, "source_url": U})
ok(res.status_code == 200 and any(x["award_key"] == "zayed" for x in res.get_json()["honors"]["awards"]), "staff add a prize")
ok(c.post(f"/api/admin/books/{b1}/honors", json={"type": "award", "award_key": "zayed", "result": "winner", "year": "x"}).status_code == 400, "bad input refused")
row = next(x for x in res.get_json()["honors"]["awards"] if x["award_key"] == "zayed")
ok(c.delete(f"/api/admin/books/{b2}/honors/award/{row['row_id']}").status_code == 404, "a row of another book cannot be deleted through this book")
ok(c.delete(f"/api/admin/books/{b1}/honors/award/{row['row_id']}").status_code == 200, "staff delete a prize")
res = c.post(f"/api/admin/books/{b1}/honors", json={"type": "adaptation", "kind": "film", "title": "فيلم ٢", "year": 1999, "source_url": U})
ok(res.status_code == 200 and len(res.get_json()["honors"]["adaptations"]) == 2, "staff add a film")
res = c.post(f"/api/admin/authors/{aid}/awards", json={"award_key": "other", "award_name": "جائزة تجربة", "result": "winner", "year": 2001, "source_url": U})
ok(res.status_code == 200 and len(res.get_json()["awards"]) == 2, "staff add an author prize")
ok(c.get(f"/api/admin/authors/{aid}").get_json()["awards"], "author sheet carries the prizes")
qs = c.get("/api/admin/inbox/questions?status=open").get_json()
ok(qs["total"] >= 2 and qs["counts"]["open"] >= 2, "question inbox lists the open questions")
ok(c.post(f"/api/admin/inbox/questions/{qs['items'][0]['q_id']}", json={"status": "done"}).status_code == 200, "a question can be marked handled")
ok(A.app.test_client().get("/api/admin/inbox/questions").status_code == 401, "question inbox needs sign-in")

# ---------- meaning search: silently off without vectors / Ollama
ok(embeddings.search(["الغربة"], ids) == {}, "no vectors stored: meaning search returns nothing (word search still works)")
txt = embeddings.book_text({"title": "ع", "summary": "نبذة", "subjects": '["س"]'})
ok(txt and "نبذة" in txt and embeddings.book_text({"title": "عنوان فقط"}) is None, "only books with something to read get a vector")
import array
v = array.array("f", [1.0, 0.0]); w = array.array("f", [0.0, 1.0])
# a vector counts only while its text_hash is the hash of the book's text now (AUDITFIX 2026-10-09; see test_auditfix.py)
con.execute("update books set summary='نبذة عن الغربة' where book_id=?", (b1,))
con.execute("update books set summary='نبذة عن الطبخ' where book_id=?", (b2,))
con.commit()
con.row_factory = sqlite3.Row
_h = lambda bid: embeddings.text_hash(embeddings.book_text(dict(con.execute("select * from books where book_id=?", (bid,)).fetchone())))
h1, h2 = _h(b1), _h(b2)
con.row_factory = None
con.execute("insert or replace into book_embeddings values (?,?,?,?,?,?)", (b1, embeddings.model_name(), h1, 2, v.tobytes(), "x"))
con.execute("insert or replace into book_embeddings values (?,?,?,?,?,?)", (b2, embeddings.model_name(), h2, 2, w.tobytes(), "x"))
con.commit()
embeddings._cache["vecs"] = None
embeddings.embed = lambda texts, timeout=8.0: [array.array("f", [0.9, 0.1]) for _ in texts]
hits = embeddings.search(["الغربة"], ids)
ok([i for i, _ in hits.get("الغربة", [])] == [b1], "closest book above the threshold is found, the far one is not")
print(f"TOTAL PASS={P} FAIL={F}")
