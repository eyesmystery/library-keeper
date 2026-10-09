"""Visitor choices, satisfaction ratings and the statistics page (2026-09-27)."""
import os, sys, sqlite3
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models, insights, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
con = sqlite3.connect(models.DB_PATH)
visitor = A.app.test_client()
empty = login(A.app.test_client()).get("/api/admin/stats?days=7").get_json()
ok(empty["totals"]["chosen"] == 0 and empty["satisfaction"]["satisfied_pct"] is None, "empty period -> zeros, no percentage")

avail = [r[0] for r in con.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 3")]
bid, bid2, bid3 = avail
qty_before = con.execute("select quantity from books where book_id=?", (bid,)).fetchone()[0]

# ---- choosing a book
r = visitor.post("/api/choice", json={"book_id": bid, "match": "direct", "language": "ar"})
ok(r.status_code == 201 and isinstance(r.get_json()["choice_id"], int), "choice recorded, id returned")
cid, tok = r.get_json()["choice_id"], r.get_json()["choice_token"]
ok(con.execute("select quantity from books where book_id=?", (bid,)).fetchone()[0] == qty_before, "choosing a book does NOT change stock")
ok(visitor.post("/api/choice", json={"book_id": 999999}).status_code == 400 and visitor.post("/api/choice", json={"book_id": "x"}).status_code == 400, "unknown book refused")
out = con.execute("select book_id from books where book_id not in (?,?,?) limit 1", avail).fetchone()[0]
con.execute("update books set quantity=0 where book_id=?", (out,)); con.commit()
ok(visitor.post("/api/choice", json={"book_id": out}).status_code == 400, "out-of-stock book cannot be chosen")
cols = [r[1] for r in con.execute("pragma table_info(book_choices)")]
ok(cols == ["choice_id", "book_id", "created_at", "language", "match"], "choice record holds no visitor text or id")

# ---- rating linked to the choice
ok(visitor.post("/api/feedback", json={"rating": "perfect", "choice_id": cid}).status_code == 400
   and visitor.post("/api/feedback", json={"rating": "perfect", "choice_id": cid, "choice_token": "0" * 32}).status_code == 400
   and visitor.post("/api/feedback", json={"rating": "perfect", "choice_id": cid, "choice_token": A.choice_token(cid + 1)}).status_code == 400,
   "a rating without the choice's own token is refused (missing, wrong, another choice's)")
ok(con.execute("select count(*) from feedback").fetchone()[0] == 0, "refused ratings store nothing")
ok(visitor.post("/api/feedback", json={"rating": "perfect", "choice_id": cid, "choice_token": tok}).status_code == 201, "rating saved for the choice")
ok(visitor.post("/api/feedback", json={"rating": "no", "choice_id": cid, "choice_token": tok}).status_code == 409, "one rating per choice")
ok(visitor.post("/api/feedback", json={"rating": "no", "choice_id": 987654, "choice_token": A.choice_token(987654)}).status_code == 400, "unknown choice refused")
ok(con.execute("select book_id, rating from feedback where choice_id=?", (cid,)).fetchone() == (bid, "perfect"), "rating stored with its book")
ok(visitor.post("/api/feedback", json={"rating": "close", "book_id": bid2}).status_code == 400, "a rating for a book without a choice is refused")

# ---- suggestions are counted
visitor.post("/api/recommend", json={"query": "رواية"})
visitor.post("/api/chat", json={"messages": [{"role": "user", "content": "أبغى رواية"}]})
shown = con.execute("select coalesce(sum(shown),0) from book_daily where day=?", (insights.local_day(),)).fetchone()[0]
ok(shown >= 4, f"books shown by recommend and chat are counted ({shown})")

# more data: book2 chosen 3 times rated 'no' 3 times; book3 shown 6 times never chosen
for _ in range(3):
    c2 = visitor.post("/api/choice", json={"book_id": bid2}).get_json()
    visitor.post("/api/feedback", json={"rating": "no", "choice_id": c2["choice_id"], "choice_token": c2["choice_token"]})
for _ in range(6):
    insights.record_shown([bid3])

# ---- statistics page
ok(visitor.get("/api/admin/stats").status_code == 401, "stats need sign-in")
ok(login(A.app.test_client(), "staff").get("/api/admin/stats").status_code == 403, "stats are manager-only")
m = login(A.app.test_client())
d = m.get("/api/admin/stats?days=30").get_json()
ok(d["totals"]["chosen"] == 4, "total choices in the period")
_c = d["satisfaction"]["counts"]
ok((_c.get("perfect"), _c.get("close", 0), _c.get("no")) == (1, 0, 3) and d["satisfaction"]["satisfied_pct"] == 25, "satisfaction counts and percentage (only ratings tied to a choice)")
ok(d["top_books"][0]["book_id"] == bid2 and d["top_books"][0]["chosen"] == 3, "most chosen book first")
ok(any(b["book_id"] == bid2 for b in d["low_rated"]), "book with mostly 'not suitable' flagged for review")
ok(any(b["book_id"] == bid3 for b in d["shown_not_chosen"]), "often suggested, never chosen listed")
ok(d["daily"][-1]["day"] == insights.local_day() and d["daily"][-1]["chosen"] == 4 and len(d["daily"]) == 30, "daily series covers the period, today last")
ok(d["genres"] and d["genres"][0]["chosen"] >= 1, "genres ranked")
ok(d["previous"]["chosen"] == 0, "previous period compared")
ok(m.get("/api/admin/stats?days=13").get_json()["days"] == 30 and m.get("/api/admin/stats?days=0").get_json()["from"] is None, "period limited to 7/30/90/365/all")
ok(d["recent_ratings"][0]["rating"] == "no", "recent ratings listed")
print(f"TOTAL PASS={P} FAIL={F}")
