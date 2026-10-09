"""Edits staff ask for in a book's card, and their list in «البلاغات» (2026-10-08)."""
import os, sys, sqlite3
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models, staff_notes, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
con = sqlite3.connect(models.DB_PATH)
ok("staff_notes" in {r[0] for r in con.execute("select name from sqlite_master where type='table'")}, "migration creates the table")
bid, bid2 = [r[0] for r in con.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 2")]
before = con.execute("select * from books where book_id=?", (bid,)).fetchone()
visitor = A.app.test_client()
m = login(A.app.test_client())
s = login(A.app.test_client(), "staff")

# ---- access
ok(visitor.post(f"/api/admin/books/{bid}/notes", json={"note": "x y"}).status_code == 401
   and visitor.get("/api/admin/inbox/staff-notes").status_code == 401, "needs sign-in")
ok(s._c.post(f"/api/admin/books/{bid}/notes", json={"note": "تعديل"}).status_code == 403, "writing needs the CSRF token")
ok(s.get("/api/admin/inbox/staff-notes").status_code == 200, "staff can read the list")

# ---- empty at first
d = s.get(f"/api/admin/books/{bid}").get_json()
ok(d["staff_notes"] == [], "book card carries an empty list")
ok(s.get("/api/admin/inbox/count").get_json()["staff_open"] == 0, "count starts at zero")

# ---- writing
ok(s.post(f"/api/admin/books/{bid}/notes", json={"note": "  "}).status_code == 400
   and s.post(f"/api/admin/books/{bid}/notes", json={}).status_code == 400
   and s.post(f"/api/admin/books/{bid}/notes", json={"note": 5}).status_code == 400, "an empty note is refused")
ok(s.post("/api/admin/books/99999999/notes", json={"note": "تعديل"}).status_code == 404, "unknown book")
r = s.post(f"/api/admin/books/{bid}/notes", json={"note": "  اسم المؤلف الصحيح: فلان\x00\r\nوالغلاف لطبعة ثانية  "})
j = r.get_json()
ok(r.status_code == 200 and len(j["staff_notes"]) == 1 and j["counts"]["open"] == 1, "staff save a note")
n1 = j["staff_notes"][0]
ok(n1["note"] == "اسم المؤلف الصحيح: فلان\nوالغلاف لطبعة ثانية" and n1["status"] == "open"
   and n1["created_by"] == "اختبار staff" and n1["book_id"] == bid, "text cleaned; writer and book kept")
m.post(f"/api/admin/books/{bid}/notes", json={"note": "س" * 3000})
m.post(f"/api/admin/books/{bid2}/notes", json={"note": "العنوان ناقص"})
d = s.get(f"/api/admin/books/{bid}").get_json()
ok(len(d["staff_notes"]) == 2 and len(d["staff_notes"][0]["note"]) == 1000, "a long note is capped at 1000 characters; newest first")
ok(s.get(f"/api/admin/books/{bid2}").get_json()["staff_notes"][0]["note"] == "العنوان ناقص", "each book shows its own notes")
ok(con.execute("select * from books where book_id=?", (bid,)).fetchone() == before, "a note never changes the book")

# ---- the list
d = s.get("/api/admin/inbox/staff-notes").get_json()
ok(d["total"] == 3 and d["counts"] == {"open": 3, "all": 3} and d["items"][0]["book_id"] == bid2
   and d["items"][0]["book_exists"] and d["items"][0]["book_title"], "all open notes listed, newest first, with the book")
ok(s.get("/api/admin/inbox/count").get_json()["staff_open"] == 3, "open notes are counted for the tab")

# ---- handled / reopened
ok(s.post(f"/api/admin/inbox/staff-notes/{n1['note_id']}", json={"status": "x"}).status_code == 400
   and s.post("/api/admin/inbox/staff-notes/99999", json={"status": "done"}).status_code == 404, "bad status / unknown note")
r = m.post(f"/api/admin/inbox/staff-notes/{n1['note_id']}", json={"status": "done"}).get_json()
done = next(x for x in r["staff_notes"] if x["note_id"] == n1["note_id"])
ok(r["counts"]["open"] == 2 and done["status"] == "done" and done["handled_by"] == "اختبار manager" and done["handled_at"], "marked handled, with who and when")
ok(r["staff_notes"][-1]["note_id"] == n1["note_id"], "in the card, handled notes go after the open ones")
ok(s.get("/api/admin/inbox/staff-notes?status=open").get_json()["total"] == 2
   and s.get("/api/admin/inbox/staff-notes?status=done").get_json()["total"] == 1
   and s.get("/api/admin/inbox/staff-notes?status=all").get_json()["total"] == 3, "status filter")
r = s.post(f"/api/admin/inbox/staff-notes/{n1['note_id']}", json={"status": "open"}).get_json()
back = next(x for x in r["staff_notes"] if x["note_id"] == n1["note_id"])
ok(back["status"] == "open" and back["handled_by"] is None and r["counts"]["open"] == 3, "reopened")

# ---- audit log, deleted book, visitor inbox untouched
acts = [r[0] for r in con.execute("select action from audit_log where action like 'staff_note.%'")]
ok(acts.count("staff_note.add") == 3 and "staff_note.done" in acts and "staff_note.open" in acts, "changes are in the audit log")
con.execute("delete from books where book_id=?", (bid2,)); con.commit()
it = next(x for x in s.get("/api/admin/inbox/staff-notes").get_json()["items"] if x["book_id"] == bid2)
ok(it["book_exists"] is False and it["book_title"], "a note of a deleted book keeps the title it was written under")
c = s.get("/api/admin/inbox/count").get_json()
ok(c["open"] == 0 and c["report"]["open"] == 0, "visitor reports are counted separately")

# ---- admin page
admin = visitor.get("/admin").get_data(as_text=True)
ok('id="inboxTabStaff"' in admin and "staffNotesBlock" in admin and "/api/admin/inbox/staff-notes" in admin, "admin page has the box and the tab")

# ---- «تنزيل الكل في ملف (md)» (Ibrahim, 2026-10-09)
import visitor_notes, open_questions
b3 = tuple(con.execute("select book_id, title from books where active=1 and quantity>0 order by book_id limit 1 offset 2").fetchone())
visitor_notes.add_report({"book_id": b3[0], "title": b3[1]}, "cover", "الغلاف | لكتاب ثاني")
visitor_notes.add_suggestion("ودي تضيفون قسم للكتب المستعملة")
open_questions.record("topic", "تاريخ القهوة")
ok(visitor.get("/api/admin/inbox/export").status_code == 401, "export needs sign-in")
r = s.get("/api/admin/inbox/export")
md = r.get_data(as_text=True)
ok(r.status_code == 200 and r.mimetype == "text/markdown" and ".md" in r.headers.get("Content-Disposition", ""), "staff download a .md file")
ok("## 1. بلاغات الكتب" in md and "الغلاف ¦ لكتاب ثاني" in md and "## 2. اقتراحات الزوار" in md and "للكتب المستعملة" in md
   and "## 3. أسئلة بلا جواب" in md and "تاريخ القهوة" in md and "## 4. تعديلات مطلوبة من الموظفين" in md, "all four lists in the file")
ok("book_id" in md and "لـ Claude" in md, "book ids and a note for Claude")
ok("تمت معالجته" not in md.split("## 4.")[1], "status=open: only open edits")
s.post(f"/api/admin/inbox/staff-notes/{n1['note_id']}", json={"status": "done"})
ok("تمت معالجته" not in s.get("/api/admin/inbox/export").get_data(as_text=True).split("## 4.")[1]
   and "تمت معالجته" in s.get("/api/admin/inbox/export?status=all").get_data(as_text=True), "status=all includes handled ones")
ok(con.execute("select count(*) from visitor_notes where status='done'").fetchone()[0] == 0, "the export marks nothing handled")
ok(con.execute("select count(*) from audit_log where action='inbox.export'").fetchone()[0] >= 2, "export is in the audit log")
admin2 = visitor.get("/admin").get_data(as_text=True)
ok('id="inboxExport"' in admin2, "admin inbox has the download button")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
