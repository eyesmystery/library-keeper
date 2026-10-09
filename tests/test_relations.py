import os, sys, json, sqlite3
# Run through tests/run_all.py: cwd is a throwaway copy of backend/ with its own data/ (prototype catalog).
sys.path.insert(0, os.getcwd())
import models; models.init_db()
import relations, llm, chat, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F; print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

B = lambda i, t, a="غير معروف", **k: dict(book_id=i, title=t, author=a, language=k.pop("language", "ar"), **k)
Pf = lambda **k: dict(overall_status="enriched", **k)
books = [B(1, "أ", "كاتب", series_name="س", series_position=1), B(2, "ب", "كاتب", series_name="س", series_position=2),
         B(3, "ج"), B(4, "د"), B(5, "هـ"), B(6, "و", language="en"), B(7, "ز")]
th = lambda *x: json.dumps(list(x), ensure_ascii=False)
profiles = {3: Pf(format="novel", themes=th("الفقد", "الذاكرة"), moods=th("sad"), emotional_weight="intense", perceived_length="long", language_difficulty="advanced", pace="slow"),
            4: Pf(format="novel", themes=th("الفقد"), moods=th("sad", "contemplative"), emotional_weight="medium", perceived_length="short", language_difficulty="easy", pace="slow"),
            5: Pf(format="self_dev", themes=th("الفقد"), moods=th("sad"), emotional_weight="light"),
            6: Pf(format="novel", themes=th("الفقد", "الذاكرة"), moods=th("sad"), emotional_weight="light"),
            7: Pf(format="novel", themes=th("الفقد"), moods=th("sad"), emotional_weight="light", series_dependency="requires_previous")}
rows = relations.compute(books, profiles)
R = {(s, t, k) for s, t, k, _, _ in rows}
ok((1, 2, "same_author") in R and (2, 1, "same_author") in R, "same author both ways")
ok((1, 2, "series_next") in R and (2, 1, "series_prev") in R, "series next / previous")
ok((3, 4, "lighter_alternative") in R and (3, 4, "shorter_alternative") in R and (3, 4, "easier_alternative") in R, "lighter / shorter / easier alternative")
ok((4, 3, "lighter_alternative") not in R, "a heavier book is never a lighter alternative")
ok(not any(t == 5 for s, t, k in R if s == 3), "different format is not an alternative")
ok(not any(t == 6 for s, t, k in R if s == 3), "different edition language is not offered")
ok(not any(t == 7 for s, t, k in R if s == 3), "a later volume that needs earlier parts is never an alternative")
reason = next(r[3] for r in rows if r[:3] == (3, 4, "lighter_alternative"))
ok("متوسط بدل شديد" in reason and "الفقد" in reason, f"reason states the difference and the shared theme: {reason}")

n = relations.rebuild()
ok(n > 0, f"rebuild on the prototype catalog stored {n} relations")
con = sqlite3.connect(models.DB_PATH)
src = con.execute("select source_book_id from book_relations where relation_type='similar' limit 1").fetchone()[0]
c = login(A.app.test_client())
about = c.get(f"/api/admin/books/{src}").get_json()["about"]
ok(about["related"] and all(r["relation"] and r["reason"] for r in about["related"]), "admin card lists related books with reasons")
ok(relations.rebuild() == n and con.execute("select count(*) from book_relations").fetchone()[0] == n, "rebuild replaces, never duplicates")

# chat: related books of a candidate reach the model and can be shown; unsuitable ones never
cards = models.get_profile_cards()
target = con.execute("select target_book_id from book_relations where source_book_id=?", (src,)).fetchone()[0]
chat.interpret = lambda conv, active: {"intent": "about_book", "prefs": llm.clean_prefs({}), "mentioned_titles": [A.get_book(src)["title"]], "wants_new_options": False}
seen = {}
def fake_respond(conv, facts_json, guidance, active):
    seen["facts"] = json.loads(facts_json)
    rel = [r["id"] for f in seen["facts"] for r in f.get("related", [])]
    return {"reply": "x", "show_book_ids": rel[:2] + [f["id"] for f in seen["facts"]], "suggestions": []}
chat.respond = fake_respond
out = A.run_chat([{"role": "user", "content": "كلمني عن الكتاب"}], active=("fake", "m"))
with_rel = [f for f in seen["facts"][:3] if f.get("related")]
ok(with_rel and all(r["reason"] and r["relation"] for f in with_rel for r in f["related"]), "the asked/top books' facts carry related books with reasons")
rel_ids = {r["id"] for f in with_rel for r in f["related"]}
ok(any(f["id"] in rel_ids for f in seen["facts"][1:]), "related books join the facts so they can be shown")
ok(all(A.is_available(dict(A.get_book(r))) for r in rel_ids), "only in-stock related books are offered")
shown_rel = [b for b in out["books"] if b["match"] == "related"]
ok(shown_rel and all(b["reason"] for b in shown_rel), "a shown related book keeps its reason")
print(f"TOTAL PASS={P} FAIL={F}")
