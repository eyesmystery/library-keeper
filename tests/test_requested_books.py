"""«الكتب المطلوبة» (Ibrahim 2026-10-24: «سو قسم جديد في الادارة وهي فيها الكتب اللي تطلب ومو متوفرة وتسجل كم مرة طلب»):
the admin list of titles and authors visitors asked for that we did not have, with the number of times, a sign that a
title is in stock now, and «تم» for staff. No model, test database only."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, open_questions as OQ
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

for _ in range(3):
    OQ.record("missing_book", "كتاب غير موجود للفحص")
OQ.record("missing_book", "كتاب ثاني للفحص")
OQ.record("missing_author", "مؤلف غير موجود للفحص")
OQ.record("topic", "موضوع للفحص")
avail = [b for b in (dict(r) for r in A.list_books(only_active=False)) if A.is_available(b)]
OQ.record("missing_book", avail[0]["title"])          # asked once when missing, in stock now

c = login(A.app.test_client())
r = c.get("/api/admin/inbox/requested?status=open")
ok(r.status_code == 200, "the list answers for a signed-in account")
d = r.get_json()
items = {x["phrase"]: x for x in d["items"]}
ok("كتاب غير موجود للفحص" in items and items["كتاب غير موجود للفحص"]["times"] == 3, "a title asked three times shows 3")
ok(d["items"][0]["times"] >= d["items"][-1]["times"], "the most asked first")
ok("مؤلف غير موجود للفحص" in items and items["مؤلف غير موجود للفحص"]["kind"] == "missing_author", "missing authors are listed too")
ok("موضوع للفحص" not in items, "subjects and other unanswered questions are not in this list")
ok(items[avail[0]["title"]]["in_stock_now"], "a title that is in stock now is marked as such")
ok(not items["كتاب ثاني للفحص"]["in_stock_now"], "a title still missing is not marked in stock")
q = items["كتاب ثاني للفحص"]["q_id"]
ok(c.post(f"/api/admin/inbox/questions/{q}", json={"status": "done"}).status_code == 200, "staff can mark a request «تم»")
ok("كتاب ثاني للفحص" not in {x["phrase"] for x in c.get("/api/admin/inbox/requested?status=open").get_json()["items"]},
   "a handled request leaves the open list")
ok("كتاب ثاني للفحص" in {x["phrase"] for x in c.get("/api/admin/inbox/requested?status=done").get_json()["items"]},
   "and shows under «تم»")
ok(A.app.test_client().get("/api/admin/inbox/requested").status_code == 401, "the list needs sign-in")
html = open(os.path.join(os.path.dirname(A.__file__), "..", "admin", "index.html"), encoding="utf-8").read()
ok('id="subRequests"' in html and 'id="tabReaders"' in html and 'id="requestedView"' in html and "loadRequested" in html, "requested books live in «القرّاء ← طلبات القرّاء» (ADMSTF)")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
