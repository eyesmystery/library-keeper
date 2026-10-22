"""«اختياراتنا هذا الشهر» (PICKS, Ibrahim 2026-10-14): admin «اختياراتنا» and five books on the start page."""
import os, sys, json, sqlite3, tempfile
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
SIG = os.path.join(tempfile.mkdtemp(), "picks_signals.json")
os.environ["LIBRARY_KEEPER_PICK_SIGNALS"] = SIG
import models, picks, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
con = sqlite3.connect(models.DB_PATH)
con.row_factory = sqlite3.Row
stock = [dict(r) for r in con.execute("select * from books where active=1 and quantity>0 and book_id not in "
          "(select book_id from book_profiles where sensitive_content like '%sexual_content%') order by book_id")]
sensitive = con.execute("select b.book_id from books b join book_profiles p using(book_id) where b.active=1 and b.quantity>0 "
                        "and p.sensitive_content like '%sexual_content%' limit 1").fetchone()
# the frozen test catalog has no cover files: treat every book with a hook or summary as showable
picks._has_cover = lambda b: True
con.execute("update books set hook = 'سطر قصير عن ' || title where hook is null or hook = ''")
con.commit()
month, nxt = picks.this_month(), picks._shift(picks.this_month(), 1)
visitor = A.app.test_client()
s = login(A.app.test_client(), "staff")

# ---- access
ok(visitor.get("/api/admin/picks").status_code == 401 and visitor.post("/api/admin/picks", json={}).status_code == 401,
   "admin picks need sign-in")
ok(s._c.post("/api/admin/picks", json={"action": "refresh"}).status_code == 403, "writing needs the CSRF token")
ok(visitor.get("/api/picks").get_json() == {"month": None, "books": []}, "nothing approved: the start page row is empty")

# ---- outside signals decide the order; a book that is not ours is never shown
a, b, c3, d = stock[0], stock[1], stock[2], stock[3]
json.dump({"month": month, "built_at": "2026-10-14T18:00:00+03:00",
           "lists": [{"key": "neelwafurat", "label": "الأكثر مبيعًا في نيل وفرات", "url": "https://example.org",
                      "books": [{"book_id": a["book_id"], "rank": 2}, {"book_id": 99999999, "rank": 1}]}],
           "ratings": {str(b["book_id"]): {"avg": 4.2, "count": 1000, "source": "goodreads"},
                       str(c3["book_id"]): {"avg": 5.0, "count": 10, "source": "abjjad"}}},
          open(SIG, "w", encoding="utf-8"), ensure_ascii=False)
picks.clear_cache()
r = s.get("/api/admin/picks").get_json()
ids = [x["book"]["book_id"] for x in r["slots"] if x.get("book")]
ok(len(ids) == 5 and r["approved"] is None, "a new month gets an automatic draft of five, not approved")
ok(ids[0] == a["book_id"], "a book on an outside best-seller list ranks first")
if sensitive:
    ok(not any(i["auto_ok"] for i in picks.ranking(month) if i["book"]["book_id"] == sensitive[0]),
       "a book whose card lists sexual content is never proposed automatically")
ok(b["book_id"] in ids and ids.index(b["book_id"]) == 1, "4.2 from 1,000 readers comes next")
rank = {x["book"]["book_id"]: x for x in picks.ranking(month)}
ok(rank[b["book_id"]]["score"] > rank[c3["book_id"]]["score"], "4.2 from 1,000 readers beats 5.0 from 10")
ok(99999999 not in rank, "an outside-list book we do not hold is never a candidate")
ok("نيل وفرات" in r["slots"][0]["reason"] and "المركز 2" in r["slots"][0]["reason"], "the slot says why it was proposed")
ok(len(r["candidates"]) == picks.CANDIDATES and not set(ids) & {x["book"]["book_id"] for x in r["candidates"]},
   "twelve candidates, none already in the five")
authors = [picks._author_key(x["book"]) for x in r["slots"] if x.get("book")]
ok(len(set(authors)) == len(authors), "one book per author")
ok(r["signals"]["lists"][0]["matched"] == 2 and r["signals"]["ratings"] == 2, "the admin sees what the signals file holds")

# ---- visitors' own choices weigh a lot
x = stock[10]
for _ in range(3):
    con.execute("insert into book_choices (book_id, created_at, language) values (?, datetime('now'), 'ar')", (x["book_id"],))
con.commit(); picks.clear_cache()
rank = {i["book"]["book_id"]: i for i in picks.ranking(month)}
ok(rank[x["book_id"]]["score"] >= 4.5 and "اختاره 3 من زوارنا" in rank[x["book_id"]]["reasons"], "three visitor choices count")

# ---- staff actions
ok(s.post("/api/admin/picks", json={"action": "set", "slot": 5, "book_id": 99999999, "month": month}).status_code == 400,
   "an unknown book is refused")
ok(s.post("/api/admin/picks", json={"action": "set", "slot": 9, "book_id": x["book_id"], "month": month}).status_code == 400,
   "a slot outside 1–5 is refused")
ok(s.post("/api/admin/picks", json={"action": "set", "slot": 5, "book_id": a["book_id"], "month": month}).status_code == 400,
   "the same book twice in a month is refused")
ok(s.post("/api/admin/picks", json={"action": "set", "slot": 5, "book_id": x["book_id"], "month": "2020-01"}).status_code == 400,
   "a past month cannot be changed")
ok(s.post("/api/admin/picks", json={"action": "set", "slot": 5, "book_id": x["book_id"], "month": month}).status_code == 200,
   "staff put a book in slot 5")
ok(s.post("/api/admin/picks", json={"action": "pin", "slot": 1, "pinned": True, "month": month}).status_code == 200, "pin slot 1")
ok(s.post("/api/admin/picks", json={"action": "move", "slot": 5, "to": 2, "month": month}).status_code == 200, "move slot 5 to 2")
r = s.get("/api/admin/picks").get_json()
ok(r["slots"][1]["book"]["book_id"] == x["book_id"] and r["slots"][1]["chosen_by"] == "staff", "the staff choice is in slot 2")
ok(s.post("/api/admin/picks", json={"action": "refresh", "month": month}).status_code == 200, "refresh the proposal")
r = s.get("/api/admin/picks").get_json()
ok(r["slots"][0]["book"]["book_id"] == a["book_id"] and r["slots"][0]["pinned"] and r["slots"][1]["book"]["book_id"] == x["book_id"],
   "a refresh keeps pinned slots and staff choices")
ok(visitor.get("/api/picks").get_json()["books"] == [], "still nothing for visitors before approval")

# ---- approval and the visitor
ok(s.post("/api/admin/picks", json={"action": "approve", "month": month}).status_code == 200, "approve the month")
v = visitor.get("/api/picks").get_json()
ok(v["month"] == month and [y["book_id"] for y in v["books"]] == [z["book"]["book_id"] for z in r["slots"]],
   "visitors see the approved five in the staff order")
ok(all(y.get("line") and y.get("title") and "author" in y for y in v["books"]), "each has title, author and a short line")
# a pick that runs out of stock is replaced for the visitor
con.execute("update books set quantity = 0 where book_id = ?", (x["book_id"],)); con.commit(); picks.clear_cache()
v = visitor.get("/api/picks").get_json()
ok(len(v["books"]) == 5 and x["book_id"] not in [y["book_id"] for y in v["books"]], "an out-of-stock pick is replaced")
r = s.get("/api/admin/picks").get_json()
ok(r["slots"][1]["out_of_stock"], "the admin marks it as out of stock")
con.execute("update books set quantity = 3 where book_id = ?", (x["book_id"],)); con.commit()
# next month not approved: the visitor keeps this month
ok(s.get(f"/api/admin/picks?month={nxt}").get_json()["approved"] is None, "next month gets its own draft")
ok(picks.live_month(nxt) == month, "a month nobody approved keeps showing the last approved one")
ok(x["book_id"] in picks._recent_picks(con, nxt) and not any(i["auto_ok"] for i in picks.ranking(nxt) if i["book"]["book_id"] == x["book_id"]),
   "a book picked last month is not proposed again")
ok(s.post("/api/admin/picks", json={"action": "unapprove", "month": month}).status_code == 200
   and visitor.get("/api/picks").get_json()["books"] == [], "un-approving hides the row")

# ---- AUDFIX (audit 2026-10-14): stock is read at the moment, a slot staff emptied stays empty
ok(s.post("/api/admin/picks", json={"action": "approve", "month": month}).status_code == 200, "approve the month again")
shown = [y["book_id"] for y in visitor.get("/api/picks").get_json()["books"]]
cand0 = s.get("/api/admin/picks").get_json()["candidates"][0]["book"]["book_id"]  # warms the ranking cache
gone = shown[2]
# a plain stock write, as an admin edit or a Rewaa import makes it — nobody calls picks.clear_cache()
con.execute(f"update books set quantity = 0 where book_id in ({gone}, {cand0})"); con.commit()
now_ids = [y["book_id"] for y in visitor.get("/api/picks").get_json()["books"]]
ok(len(now_ids) == 5 and gone not in now_ids and cand0 not in now_ids,
   "with a warm ranking cache, a pick that ran out is replaced and the replacement is not a book that ran out")
ok(all(con.execute("select quantity from books where book_id = ?", (i,)).fetchone()[0] > 0 for i in now_ids),
   "every book the visitor sees is in stock now")
ok(not any(i["book"]["book_id"] in (gone, cand0) for i in picks.ranking(month)), "the ranking cache renews when stock changes")
ok(cand0 not in [c["book"]["book_id"] for c in s.get("/api/admin/picks").get_json()["candidates"]],
   "a book that ran out is not offered as a candidate")
con.execute(f"update books set quantity = 3 where book_id in ({gone}, {cand0})"); con.commit()

r = s.get("/api/admin/picks").get_json()
removed = r["slots"][0]["book"]["book_id"]
ok(removed == a["book_id"], "slot 1 holds the best ranked book")
ok(s.post("/api/admin/picks", json={"action": "clear", "slot": 1, "month": month}).status_code == 200, "staff empty slot 1")
v = [y["book_id"] for y in visitor.get("/api/picks").get_json()["books"]]
ok(len(v) == 4 and removed not in v, "an emptied slot stays empty for visitors and its book does not come back")
ok(s.post("/api/admin/picks", json={"action": "clear", "slot": 1, "month": month}).status_code == 400,
   "an emptied slot cannot be emptied again")
ok(s.post("/api/admin/picks", json={"action": "pin", "slot": 1, "pinned": True, "month": month}).status_code == 400,
   "an emptied slot cannot be pinned")
r = s.get("/api/admin/picks").get_json()
ok(r["slots"][0]["book"] is None and r["slots"][0].get("cleared") and removed not in [c["book"]["book_id"] for c in r["candidates"]],
   "the admin shows the slot as emptied by staff and does not offer the book again")
ok(s.post("/api/admin/picks", json={"action": "refresh", "month": month}).status_code == 200, "refresh after emptying")
r = s.get("/api/admin/picks").get_json()
ok(r["slots"][0].get("cleared") and removed not in [x["book"]["book_id"] for x in r["slots"] if x.get("book")],
   "a refresh keeps the emptied slot and does not put its book in another slot")
other = r["slots"][2]["book"]["book_id"]
con.execute("update books set quantity = 0 where book_id = ?", (other,)); con.commit()
v = [y["book_id"] for y in visitor.get("/api/picks").get_json()["books"]]
ok(len(v) == 4 and other not in v and removed not in v,
   "only the pick that ran out is replaced, never by the book staff took out")
con.execute("update books set quantity = 3 where book_id = ?", (other,)); con.commit()
ok(removed not in picks._recent_picks(con, nxt), "a book staff took out does not count as a past pick")
ok(s.post("/api/admin/picks", json={"action": "set", "slot": 1, "book_id": removed, "month": month}).status_code == 200,
   "staff may put a book back in an emptied slot")
v = [y["book_id"] for y in visitor.get("/api/picks").get_json()["books"]]
ok(len(v) == 5 and v[0] == removed, "the slot shows again once staff fill it")
for n in range(1, 6):
    s.post("/api/admin/picks", json={"action": "clear", "slot": n, "month": nxt})
ok(s.post("/api/admin/picks", json={"action": "approve", "month": nxt}).status_code == 400,
   "a month whose slots were all emptied cannot be approved")

# ---- every change is in the audit log (the staff changes file)
acts = [r[0] for r in con.execute("select action from audit_log where action like 'picks.%'")]
ok({"picks.set", "picks.pin", "picks.move", "picks.refresh", "picks.approve", "picks.unapprove", "picks.clear"} <= set(acts),
   "staff changes are logged")
import inbox_export
ok(all(k in inbox_export.ACTION_AR for k in ("picks.set", "picks.approve")), "they appear in the staff changes file")

# ---- start page markup
html = open("../frontend/index.html", encoding="utf-8").read()
js = open("../frontend/app.js", encoding="utf-8").read()
admin = open("../admin/index.html", encoding="utf-8").read()
ok('id="picksBox"' in html and "اختياراتنا هذا الشهر" in html and "loadPicks()" in js, "the start page has the row")
ok("/api/admin/picks" in admin and "اختياراتنا" in admin, "the admin has the section")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
