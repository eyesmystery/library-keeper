"""PICKALERT (Ibrahim 2026-11-01 «اضف التنبيه المقترح»): «التنبيهات» shows one row while this month's
«اختياراتنا» is not approved; the row opens «تنظيم منار» → «اختياراتنا»."""
import os, sys
sys.path.insert(0, os.getcwd())

import models; models.init_db()
import app as A, picks
from auth_helper import login

P = F = 0
def ok(cond, msg):
    global P, F
    if cond: P += 1; print("PASS", msg)
    else: F += 1; print("FAIL", msg)

ROOT = os.path.dirname(os.getcwd())
m = login(A.app.test_client())
month = picks.this_month()
name = A._MONTHS_AR[int(month[5:7]) - 1]

book = next(dict(b) for b in A.list_books(only_active=True) if A.is_available(dict(b)))
ok(m.post("/api/admin/picks", json={"action": "set", "month": month, "slot": 1, "book_id": book["book_id"]}).status_code == 200,
   "staff put a book in slot 1 of this month")
picks.set_approved(month, False, "test")
r = m.get("/api/admin/inbox/alerts").get_json()
it = {i["key"]: i for i in r["items"]}.get("picks")
ok(it is not None and it["open"] == 1 and it["go"] == "organize:picks", "a month not approved → one open «اختياراتنا» alert that opens organize:picks")
ok(it and f"لشهر {name} تنتظر الاعتماد" in it["label"] and it["latest"] == f"{month}-01T00:00:00+03:00",
   "the label names the month; «latest» is the month's first day (one «جديد» per month)")
a0 = r["attention"]

picks.set_approved(month, True, "test")
r = m.get("/api/admin/inbox/alerts").get_json()
it = {i["key"]: i for i in r["items"]}.get("picks")
ok(it and it["open"] == 0 and it["latest"] is None and f"لشهر {name} معتمدة" in it["label"], "approved → the row is quiet and says «معتمدة»")
ok(r["attention"] == a0 - 1, "and the badge count drops by one")

html = open(os.path.join(ROOT, "admin", "index.html"), encoding="utf-8").read()
ok("view === 'organize'" in html and "selectSub('organize', sub === 'events' ? 'events' : 'picks')" in html,
   "«افتح» on the alert goes to «تنظيم منار» → «اختياراتنا»")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
