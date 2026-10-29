"""[DEVSEARCH] Ibrahim 2026-10-29: «ابغاك تضيف لي كمطور في بطاقة كل كتاب اقدر ابحث في الانترنت يدويا عن بيانات الكتاب
واعدلها» — a developer block in the book card with search links for the book's data, and the page used saved as the source
(summary / age source, and written with the change in the audit)."""
import os, sys, sqlite3, json
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, auth
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

con = sqlite3.connect(models.DB_PATH)
bid = con.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 1").fetchone()[0]
d = login(A.app.test_client(), "developer")
src = "https://www.neelwafurat.com/itempage.aspx?id=lbb12345"
r = d.patch(f"/api/books/{bid}", json={"summary": "نبذة تجريبية عن موضوع الكتاب.", "source_url": src}, headers={"X-CSRF-Token": d.csrf})
ok(r.status_code == 200, "a summary with its source page is saved")
row = con.execute("select summary, summary_source from books where book_id=?", (bid,)).fetchone()
ok(row[0] == "نبذة تجريبية عن موضوع الكتاب." and row[1] == src, f"the page is the summary's source ({row[1]})")
r = d.patch(f"/api/books/{bid}", json={"page_count": 321, "source_url": src}, headers={"X-CSRF-Token": d.csrf})
last = auth.recent_audit(5) if hasattr(auth, "recent_audit") else [dict(x) for x in sqlite3.connect(models.DB_PATH).execute(
    "select details from audit_log order by log_id desc limit 1")]
det = json.loads(last[0]["details"]) if isinstance(last[0].get("details"), str) else last[0].get("details")
ok(r.status_code == 200 and det.get("source") == src and det.get("page_count", [None, None])[1] == 321,
   f"an edit made from a page is written in the audit with that page ({det})")
r = d.patch(f"/api/books/{bid}", json={"summary": "نبذة أخرى.", "source_url": "javascript:alert(1)"}, headers={"X-CSRF-Token": d.csrf})
ok(r.status_code == 400, "a source that is not an http(s) link is refused")
r = d.patch(f"/api/books/{bid}", json={"summary": "نبذة بدون رابط."}, headers={"X-CSRF-Token": d.csrf})
ok(r.status_code == 200 and con.execute("select summary_source from books where book_id=?", (bid,)).fetchone()[0] == "staff",
   "without a link the summary's source stays «staff»")

html = open(os.path.join(os.path.dirname(A.__file__), "..", "admin", "index.html"), encoding="utf-8").read()
ok("function webSearchBlock(b)" in html and "${webSearchBlock(b)}" in html, "the book card has the search block")
ok('<section class="block dev-only dev-box"><h3>بحث يدوي عن بيانات الكتاب' in html, "drawn as the developer's own (dev-only, dev colour)")
ok(all(x in html for x in ("site:neelwafurat.com", "site:abjjad.com", "goodreads.com/search", "openlibrary.org/search")),
   "links: Google, نيل وفرات, أبجد, Goodreads, Open Library …")
ok("Object.assign({ summary: $('fSummary').value }, devSource())" in html and "Object.assign({}, body, devSource())" in html,
   "summary and basic edits send the source link")
con.close()
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
