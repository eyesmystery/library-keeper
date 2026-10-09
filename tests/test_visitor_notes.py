"""Visitor reports about a book's information, visitor suggestions, and the admin inbox (2026-10-03)."""
import os, sys, sqlite3
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
os.environ["LIBRARY_KEEPER_RATE_NOTES"] = "25"
import models, limits, visitor_notes, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
con = sqlite3.connect(models.DB_PATH)
visitor = A.app.test_client()
bid, bid2 = [r[0] for r in con.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 2")]
title = con.execute("select title from books where book_id=?", (bid,)).fetchone()[0]
before = con.execute("select * from books where book_id=?", (bid,)).fetchone()

# ---- nothing yet
m = login(A.app.test_client())
d = m.get("/api/admin/inbox").get_json()
ok(d["items"] == [] and d["counts"]["open"] == 0, "empty inbox -> no items, zero count")

# ---- reporting a mistake in a book's information
r = visitor.post("/api/report", json={"book_id": bid, "reason": "title_author", "note": " اسم المؤلف غلط ", "language": "ar"})
ok(r.status_code == 201 and r.get_json() == {"ok": True}, "report saved")
ok(visitor.post("/api/report", json={"book_id": bid2, "reason": "cover"}).status_code == 201, "report without a note is accepted")
ok(visitor.post("/api/report", json={"book_id": bid, "reason": "nonsense"}).status_code == 400, "unknown reason refused")
ok(visitor.post("/api/report", json={"book_id": bid}).status_code == 400, "missing reason refused")
ok(visitor.post("/api/report", json={"book_id": bid, "reason": "other"}).status_code == 400
   and visitor.post("/api/report", json={"book_id": bid, "reason": "other", "note": "   "}).status_code == 400, "«شيء آخر» needs a note")
ok(visitor.post("/api/report", json={"book_id": 999999, "reason": "cover"}).status_code == 400
   and visitor.post("/api/report", json={"book_id": "x", "reason": "cover"}).status_code == 400
   and visitor.post("/api/report", json={"reason": "cover"}).status_code == 400, "unknown or missing book refused")
ok(visitor.post("/api/report", data="{not json", content_type="application/json").status_code == 400
   and visitor.post("/api/report", json=["a"]).status_code == 400
   and visitor.post("/api/suggestion", data="{not json", content_type="application/json").status_code == 400, "malformed request -> 400, not a crash")
ok(visitor.post("/api/report", json={"book_id": bid, "reason": "summary", "note": {"a": 1}}).status_code == 201, "a note that is not text is ignored, report still saved")
ok(con.execute("select * from books where book_id=?", (bid,)).fetchone() == before, "a report does NOT change the book")
long = visitor.post("/api/report", json={"book_id": bid, "reason": "other", "note": "ط" * 2000 + "\x00\x07"})
stored = con.execute("select note from visitor_notes order by note_id desc limit 1").fetchone()[0]
ok(long.status_code == 201 and len(stored) == 500 and "\x00" not in stored, "long note is cut at 500 characters, control characters removed")
out = con.execute("select book_id from books where book_id not in (?,?) limit 1", (bid, bid2)).fetchone()[0]
con.execute("update books set quantity=0 where book_id=?", (out,)); con.commit()
ok(visitor.post("/api/report", json={"book_id": out, "reason": "location"}).status_code == 201, "a book that just went out of stock can still be reported")

# ---- suggestions
ok(visitor.post("/api/suggestion", json={"note": "أتمنى توفرون كتب أكثر عن الفلك", "language": "en"}).status_code == 201, "suggestion saved")
ok(visitor.post("/api/suggestion", json={"note": ""}).status_code == 400 and visitor.post("/api/suggestion", json={"note": " ا "}).status_code == 400
   and visitor.post("/api/suggestion", json={}).status_code == 400, "empty or too short suggestion refused")

# ---- anonymous
cols = [r[1] for r in con.execute("pragma table_info(visitor_notes)")]
ok(cols == ["note_id", "kind", "book_id", "book_title", "reason", "note", "language", "created_at", "status", "handled_by", "handled_at"],
   "stored columns hold no visitor name, address or conversation id")

# ---- access
ok(visitor.get("/api/admin/inbox").status_code == 401 and visitor.get("/api/admin/inbox/count").status_code == 401
   and visitor.post("/api/admin/inbox/1", json={"status": "done"}).status_code == 401, "inbox needs sign-in")
s = login(A.app.test_client(), "staff")
ok(s.get("/api/admin/inbox").status_code == 200 and s.get("/api/admin/inbox/count").status_code == 200, "staff can read the inbox")
ok(s._c.post("/api/admin/inbox/1", json={"status": "done"}).status_code == 403, "changing a note needs the CSRF token")

# ---- inbox content
d = m.get("/api/admin/inbox?kind=report&status=open").get_json()
ok(d["counts"] == {"report": {"open": 5, "done": 0}, "suggestion": {"open": 1, "done": 0}, "open": 6}, "counts per kind and total open")
ok(len(d["items"]) == 5 and d["items"][0]["note_id"] > d["items"][-1]["note_id"], "reports listed newest first")
first = [n for n in d["items"] if n["reason"] == "title_author"][0]
ok(first["book_id"] == bid and first["book_title"] == title and first["note"] == "اسم المؤلف غلط"
   and first["reason_label"] == "العنوان أو المؤلف" and first["book_exists"] is True, "report carries the book, the reason label and the trimmed note")
sg = m.get("/api/admin/inbox?kind=suggestion").get_json()["items"]
ok(len(sg) == 1 and sg[0]["book_id"] is None and sg[0]["language"] == "en" and sg[0]["note"].startswith("أتمنى"), "suggestion listed separately, no book")
ok(m.get("/api/admin/inbox?kind=whatever").get_json()["kind"] == "report", "unknown kind falls back to reports")

# ---- handling
nid = first["note_id"]
r = s.post(f"/api/admin/inbox/{nid}", json={"status": "done"})
ok(r.status_code == 200 and r.get_json()["counts"]["open"] == 5, "staff marks a report handled; open count goes down")
row = con.execute("select status, handled_by, handled_at from visitor_notes where note_id=?", (nid,)).fetchone()
ok(row[0] == "done" and row[1] == "اختبار staff" and row[2], "handled by and time recorded")
ok(nid not in [n["note_id"] for n in m.get("/api/admin/inbox?status=open").get_json()["items"]]
   and [n["note_id"] for n in m.get("/api/admin/inbox?status=done").get_json()["items"]] == [nid]
   and len(m.get("/api/admin/inbox?status=all").get_json()["items"]) == 5, "status filter: open / done / all")
allitems = m.get("/api/admin/inbox?status=all").get_json()["items"]
ok(allitems[-1]["note_id"] == nid, "handled notes come after open ones")
ok(m.post(f"/api/admin/inbox/{nid}", json={"status": "open"}).status_code == 200
   and con.execute("select status, handled_by, handled_at from visitor_notes where note_id=?", (nid,)).fetchone() == ("open", None, None), "a handled note can be reopened")
ok(m.post(f"/api/admin/inbox/{nid}", json={"status": "gone"}).status_code == 400 and m.post("/api/admin/inbox/987654", json={"status": "done"}).status_code == 404,
   "invalid status -> 400, unknown note -> 404")
acts = [r[0] for r in con.execute("select action from audit_log where action like 'inbox.%' order by log_id")]
ok(acts == ["inbox.done", "inbox.open"], "handling is written to the audit log")

# ---- a report about a book that was later deleted keeps its title
con.execute("insert into visitor_notes (kind, book_id, book_title, reason, created_at) values ('report', 987654, 'كتاب قديم', 'cover', '2026-10-01T00:00:00+00:00')"); con.commit()
gone = [n for n in m.get("/api/admin/inbox?status=all").get_json()["items"] if n["book_id"] == 987654][0]
ok(gone["book_title"] == "كتاب قديم" and gone["book_exists"] is False, "report about a deleted book keeps the saved title")

# ---- rate limit (25 per address in this test)
limits.reset()
codes = [visitor.post("/api/suggestion", json={"note": f"اقتراح رقم {i}"}).status_code for i in range(27)]
ok(codes[:25] == [201] * 25 and codes[25:] == [429, 429], "more than the allowed notes from one address -> 429")
ok(visitor.post("/api/report", json={"book_id": bid, "reason": "cover"}).status_code == 429, "reports share the same allowance")
limits.reset()
ok(visitor.post("/api/chat", json={"messages": [{"role": "user", "content": "أبغى رواية"}]}).status_code == 200, "chat still works after notes were limited")

# ---- pages carry the new controls
html = visitor.get("/").get_data(as_text=True)
js = visitor.get("/static/app.js").get_data(as_text=True)
ok('id="reportBtn"' in html and 'id="suggestBtn"' in html and 'id="noteSheet"' in html, "visitor page has the report button, the suggestion button and the note sheet")
ok("report ? 'report' : 'suggestion'" in js and "reportReasons" in js, "visitor script sends reports and suggestions")
ok(all(f"'{k}'" in js for k in visitor_notes.REASONS), "every reason the server accepts is offered to the visitor")
ok(js.count("title_author") == 2, "reasons offered in both Arabic and English")
admin = visitor.get("/admin").get_data(as_text=True)
ok('id="tabInbox"' in admin and 'id="inboxN"' in admin and "/api/admin/inbox" in admin, "admin page has the «البلاغات» tab with its counter")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
