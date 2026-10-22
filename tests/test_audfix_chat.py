"""[AUDFIX] Chat cases left open after QAFIX / QAD, as tests with the answer the data gives (daily audit 2026-10-12 #8).

From the library inbox of 2026-10-11 (research/inbox_2026-10-11/for_QAFIX.md), the ones still failing on 2026-10-12:
- §3.8 «هل تركي بن فهد آل سعود شاعر؟»: «تركي» was read as Turkish literature (keyword reading, with and without
  the model's help) and Turkish novels came back; his own books are in stock.
- §3.3 «دار نشر فن الحرب»: with the model down, the title was not found at all (books about war came back); the
  title is stocked in three editions with three publishers. With the model, a message that is a stocked title is
  that book even when the interpreter names no title.
Not testable without the model: «متعلمة» -> «Educated» (an Arabic rendering of an English title is world knowledge).
"""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm, chat, keyword_prefs as K, app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# ---------------------------------------------------------------- «تركي» the given name
def langs(q):
    return K.understand([q]).get("original_languages") or []
for q in ("هل تركي بن فهد آل سعود شاعر؟", "عندكم كتب تركي بن فهد آل سعود؟", "ابي رواية لتركي الحمد", "كتب تركي الدخيل"):
    ok("tr" not in langs(q), f"«{q}»: a person named تركي, not Turkish literature")
for q in ("ابي ادب تركي", "روايات تركية", "أدب تركي معاصر", "ابي شي تركي", "رواية تركية مترجمة", "الادب التركي الحديث"):
    ok("tr" in langs(q), f"«{q}»: Turkish literature still")

# ---------------------------------------------------------------- the test catalog gets the two cases' books
con = models.get_connection()
avail = [b for b in (dict(r) for r in A.list_books(only_active=False)) if A.is_available(b)]
war = [avail[0]["book_id"], avail[1]["book_id"], avail[2]["book_id"]]
turki = avail[3]["book_id"]
for bid, pub, qty in zip(war, ("نهضة مصر", "دار الكرمة", "المركز الثقافي العربي"), (4, 3, 2)):
    con.execute("UPDATE books SET title='فن الحرب', author='سون تزو', publisher=?, quantity=? WHERE book_id=?", (pub, qty, bid))
con.execute("UPDATE books SET quantity=0, title='فن الحرب', author='سون تزو', publisher='دار نفدت' WHERE book_id=?", (avail[4]["book_id"],))
con.execute("UPDATE books SET author='تركي بن فهد آل سعود', original_language='ar', translator=NULL WHERE book_id=?", (turki,))
con.commit(); con.close()
for fn in ("clear_cache", "invalidate_cache"):
    if hasattr(A, fn):
        getattr(A, fn)()
books = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
# the frozen test catalog has a «فن الحرب» of its own: every stocked edition is expected, the most copies first
want = [b["book_id"] for b in sorted((b for b in books.values() if A.is_available(b) and b["title"] == "فن الحرب"),
                                     key=lambda b: -(b.get("quantity") or 0))]
got = [b["book_id"] for b in A.exact_title_books("دار نشر فن الحرب", books)]
ok(set(war) <= set(got) and got == want and avail[4]["book_id"] not in got,
   f"«دار نشر فن الحرب» -> every stocked edition of «فن الحرب» ({len(got)}), most copies first, not the sold-out one")
ok([b["book_id"] for b in A.exact_title_books("عندكم فن الحرب؟", books)] == want, "«عندكم فن الحرب؟» -> the same")
ok(A.exact_title_books("الحرب", books) == [] and A.exact_title_books("ابي كتاب عن فن الحرب", books) == [],
   "one word, or a wish about the subject, is not taken as the title")
cat = {1: {"book_id": 1, "title": "قصص قصيرة", "author": "أحد", "active": 1, "quantity": 2}}
ok(A.exact_title_books("ابي قصص قصيرة", cat) == [], "«ابي قصص قصيرة» stays a wish, even if a book bears that title")

# ---------------------------------------------------------------- with the model down (keyword answer)
out = A.run_chat([{"role": "user", "content": "دار نشر فن الحرب"}], active=None)
ids = [b["book_id"] for b in out["books"]]
ok(ids == want[:4] and all(p in out["reply"] for p in ("نهضة مصر", "دار الكرمة", "المركز الثقافي العربي")) and "فن الحرب" in out["reply"]
   and "دار نفدت" not in out["reply"], "model down, «دار نشر فن الحرب»: the stocked editions and their publishers, not the sold-out one")
out = A.run_chat([{"role": "user", "content": "هل تركي بن فهد آل سعود شاعر؟"}], active=None)
ok("التركية" not in out["reply"] and not any(books[b["book_id"]].get("original_language") == "tr" for b in out["books"]),
   "model down, «هل تركي بن فهد آل سعود شاعر؟»: no «Turkish literature» answer, no Turkish book picked for the word")

# ---------------------------------------------------------------- with the model: the interpreter names no title
seen = {}
def fake_respond(conv, facts_json, guidance, active, *a):
    seen["facts"] = json.loads(facts_json); seen["guidance"] = guidance
    return {"reply": "x", "show_book_ids": [f["id"] for f in seen["facts"]][:4], "suggestions": [], "unanswered": ""}
chat.respond = fake_respond
chat.interpret = lambda conv, active, *a: {"intent": "recommend", "prefs": llm.clean_prefs({}), "mentioned_titles": [],
                                          "title_variants": [], "wants_new_options": False, "references": [], "author_request": {}}
A.run_chat([{"role": "user", "content": "دار نشر فن الحرب"}], active=("fake", "m"))
# one fact for the work with its other editions under it (MGR 2026-10-17, library inbox §3.3: two separate edition
# facts each listing only the third made the answer count two of three)
_eds = [f for f in seen.get("facts") or [] if f["id"] in war]
_all = [(f["id"], f.get("publisher")) for f in _eds] + [(e["id"], e.get("publisher")) for f in _eds for e in f.get("other_editions") or []]
ok(set(war) <= {i for i, _ in _all} and all(p for i, p in _all if i in war) and len(_eds) == 1,
   "model on, no title named by the interpreter: one fact for the work, holding the three editions with their publishers")

print(f"\nTOTAL pass={P} fail={F}")
sys.exit(1 if F else 0)
