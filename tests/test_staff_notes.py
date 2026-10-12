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
# ---- section 5: what staff changed in the data, from audit_log (Ibrahim 2026-10-09)
ok("## 5. تعديلات نفّذها الموظفون في المكتبة (" in md, "the file has the section of staff changes")
bid5 = con.execute("select book_id from books where active=1 and quantity>0 order by book_id desc limit 1").fetchone()[0]
cur = con.execute("select original_language, shelf_location from books where book_id=?", (bid5,)).fetchone()
r = s.patch(f"/api/books/{bid5}", json={"original_language": "fr", "shelf_location": "رف تجربة 7"})
ok(r.status_code == 200, "staff change a book (origin and shelf)") or print("   ->", r.status_code, r.get_data(as_text=True)[:300])
long_summary = "نبذة تجريبية طويلة " * 30
s.patch(f"/api/books/{bid5}", json={"summary": long_summary})
md5 = s.get("/api/admin/inbox/export").get_data(as_text=True).split("## 5. ", 1)[1]
ok("تعديل بيانات كتاب" in md5 and f"book_id {bid5}" in md5 and "لغة الأصل" in md5 and "صار الفرنسية" in md5
   and "رف تجربة 7" in md5, "the change is listed with field, before and after")
ok("- بواسطة: " in md5 and "- بواسطة: —" not in md5.split("رف تجربة 7")[0][-400:], "who made the change")
ok(long_summary.split()[0] in md5 and "النبذة" in md5, "a long summary change is complete enough to read")
last = con.execute("select details from audit_log where action='book.update' order by log_id desc limit 1").fetchone()[0]
import json as _json
ok(isinstance(_json.loads(last), dict), "audit details of a long summary are valid JSON (not cut)")
ok("inbox.export" not in md5 and "login" not in md5, "exports and logins are not listed as changes")
ok("★" in md5, "changes after the previous export are marked new")
con.execute("update books set original_language=?, shelf_location=? where book_id=?", (cur[0], cur[1], bid5)); con.commit()
# ---- «البيانات الأساسية» editable by staff (Ibrahim 2026-10-09)
row0 = dict(zip(["title", "author", "publisher", "genre", "page_count", "publication_year", "isbn"],
                con.execute("select title, author, publisher, genre, page_count, publication_year, isbn from books where book_id=?", (bid5,)).fetchone()))
ok(s.patch(f"/api/books/{bid5}", json={"title": "  "}).status_code == 400, "an empty title is refused")
ok(s.patch(f"/api/books/{bid5}", json={"isbn": "9789953000000"}).status_code == 400, "an ISBN with a wrong checksum is refused")
ok(s.patch(f"/api/books/{bid5}", json={"publication_year": 3020}).status_code == 400
   and s.patch(f"/api/books/{bid5}", json={"genre": "خيال"}).status_code == 400, "a year in the future / an unknown genre are refused")
r = s.patch(f"/api/books/{bid5}", json={"title": "عنوان مصحح للتجربة", "author": "مؤلف التجربة الجديد", "publisher": "دار التجربة",
                                        "genre": "شعر", "publication_year": 2019, "page_count": 212, "isbn": "978-0-306-40615-7"})
ok(r.status_code == 200, "staff save the basic data")
row = con.execute("select title, author, publisher, genre, publication_year, page_count, isbn from books where book_id=?", (bid5,)).fetchone()
ok(row == ("عنوان مصحح للتجربة", "مؤلف التجربة الجديد", "دار التجربة", "شعر", 2019, 212, "9780306406157"), "the book has the new values (ISBN cleaned)")
ok(con.execute("select format from book_profiles where book_id=?", (bid5,)).fetchone()[0] == "poetry", "the card's format follows the genre")
ok(con.execute("select count(*) from book_authors ba join authors a using(author_id) where ba.book_id=? and a.name_ar=?",
               (bid5, "مؤلف التجربة الجديد")).fetchone()[0] == 1, "the new author is linked to the book")
d5 = _json.loads(con.execute("select details from audit_log where action='book.update' and target=? order by log_id desc limit 1", (str(bid5),)).fetchone()[0])
ok(d5["title"] == [row0["title"], "عنوان مصحح للتجربة"] and d5.get("genre", [None, "شعر"])[1] == "شعر", "the log keeps who, before and after")
md6 = s.get("/api/admin/inbox/export").get_data(as_text=True)
ok("العنوان: كان" in md6 and "صار عنوان مصحح للتجربة" in md6 and "المؤلف: كان" in md6, "the export shows the basic-data edit")
ok(s.patch(f"/api/books/{bid5}", json={"publisher": "", "isbn": "", "page_count": None}).status_code == 200
   and con.execute("select publisher, isbn, page_count from books where book_id=?", (bid5,)).fetchone() == (None, None, None), "fields can be emptied")
con.execute("update books set title=?, author=?, publisher=?, genre=?, page_count=?, publication_year=?, isbn=? where book_id=?",
            (row0["title"], row0["author"], row0["publisher"], row0["genre"], row0["page_count"], row0["publication_year"], row0["isbn"], bid5)); con.commit()
# ---- «اقتراحات الموظفين»: general suggestions about the app (Ibrahim 2026-10-09)
ok(visitor.post("/api/admin/inbox/staff-ideas", json={"text": "فكرة"}).status_code == 401, "ideas need sign-in")
ok(s.post("/api/admin/inbox/staff-ideas", json={"text": "  "}).status_code == 400, "an empty idea is refused")
r = s.post("/api/admin/inbox/staff-ideas", json={"text": "ودنا زر يطبع قائمة الكتب الناقصة\nوخانة بحث بالرف"})
ok(r.status_code == 200 and r.get_json()["counts"]["open"] >= 1, "staff send a general suggestion")
iid = r.get_json()["idea_id"]
lst = s.get("/api/admin/inbox/staff-ideas").get_json()
ok(any(i["idea_id"] == iid and i["created_by"] and "الكتب الناقصة" in i["text"] for i in lst["items"]), "it is listed with the writer")
ok(s.get("/api/admin/inbox/count").get_json().get("ideas_open", 0) >= 1, "the inbox count includes open ideas")
au = con.execute("select username, details from audit_log where action='staff_idea.add' and target=?", (str(iid),)).fetchone()
ok(au and "الكتب الناقصة" in au[1], "the idea is in the audit log with its text")
md7 = s.get("/api/admin/inbox/export").get_data(as_text=True)
ok("## 7. اقتراحات الموظفين على التطبيق" in md7 and "> ودنا زر يطبع قائمة الكتب الناقصة" in md7 and "> وخانة بحث بالرف" in md7, "the idea is in the downloaded file")
ok(s.post(f"/api/admin/inbox/staff-ideas/{iid}", json={"status": "done"}).status_code == 200
   and not any(i["idea_id"] == iid for i in s.get("/api/admin/inbox/staff-ideas").get_json()["items"])
   and s.post("/api/admin/inbox/staff-ideas/99999", json={"status": "done"}).status_code == 404, "marked handled; unknown id 404")
admin2 = visitor.get("/admin").get_data(as_text=True)
ok('id="inboxTabIdeas"' in admin2 and "loadIdeas(" in admin2, "the inbox has the «اقتراحات الموظفين» tab")
ok('id="basicEdit"' in admin2 and 'id="basicForm"' in admin2 and "saveBasic(b)" in admin2, "the card has the edit button and form")
ok('id="libTabConfirm"' in admin2 and admin2.index('id="libTabConfirm"') < admin2.index('id="libTabOrigin"'),
   "«تأكيد الهوية» tab comes before «أصل الكتاب»")
ok("!confirmPending(b)" in admin2, "books waiting for identity are not in «أصل الكتاب»")
ok('id="libTabPublishers"' in admin2 and admin2.index('id="libTabTranslators"') < admin2.index('id="libTabPublishers"') < admin2.index('id="libTabConfirm"')
   and "['publishers', 'libTabPublishers', 'libPublishers']" in admin2 and 'id="pubList"' in admin2, "«دور النشر» tab after the authors / translators")
ok('id="inboxExport"' in admin2, "admin inbox has the download button")

# ---- 8. Test-mode conversations in the export (Ibrahim 2026-10-10)
import json as _j, tempfile as _tf, testlog as _tl
from datetime import datetime as _dt
_old_dir = _tl.LOG_DIR
with _tf.TemporaryDirectory() as _d:
    _tl.LOG_DIR = _d
    md_empty = s.get("/api/admin/inbox/export").get_data(as_text=True)
    ok("## 8. محادثات وضع الاختبار (0)" in md_empty and "لا يوجد." in md_empty.split("## 8.")[1], "section 8 is there and empty without logs")
    bk = con.execute("select book_id, title from books where active=1 limit 1").fetchone()
    now = _dt.now().isoformat(timespec="seconds")
    rows = [{"ts": now, "kind": "turn", "conversation_id": "conv-test-0001", "turn": 1, "provider": "codex", "model": "gpt-x",
             "understanding": "ai", "visitor": "ابي رواية خفيفة", "reply": "عندي لك «كتاب» خفيف وممتع",
             "books_shown": [{"id": bk[0], "title": bk[1], "reason": "خفيف"}], "language": "ar"},
            {"ts": now, "kind": "turn", "conversation_id": "conv-test-0001", "turn": 2, "provider": None, "model": None,
             "understanding": "fallback", "visitor": "غيرها", "reply": "هذي خيارات ثانية", "books_shown": [], "language": "ar"},
            {"ts": now, "kind": "choice", "conversation_id": "conv-test-0001", "book_id": bk[0], "choice_id": 1},
            {"ts": now, "kind": "feedback", "conversation_id": "conv-test-0001", "rating": 5, "book_id": bk[0], "choice_id": 1},
            {"ts": "2020-01-01T10:00:00", "kind": "turn", "conversation_id": "conv-old-0001", "visitor": "قديمة", "reply": "قديمة"}]
    with open(os.path.join(_d, "chat-" + now[:10] + ".jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(_j.dumps(r, ensure_ascii=False) for r in rows) + "\nnot json\n[]\n42\n")
    md8 = s.get("/api/admin/inbox/export").get_data(as_text=True).split("## 8. ")[1]
    ok(md8.startswith("محادثات وضع الاختبار (1)") and "2 رسالة · codex/gpt-x" in md8, "one conversation, two turns, the model named")
    ok("- **الزائر:** ابي رواية خفيفة" in md8 and "- **منار:** عندي لك «كتاب» خفيف وممتع" in md8
       and f"({bk[0]})" in md8 and "_(بالنظام البديل)_" in md8, "visitor text, reply, shown books and fallback mark")
    ok(f"- **اختار الزائر:** «{bk[1]}» ({bk[0]})" in md8 and "- **تقييمه للاختيار:** 5" in md8, "the choice and the rating")
    ok("قديمة" not in md8, "conversations older than 30 days are left out")
    md_all = s.get("/api/admin/inbox/export?status=all").get_data(as_text=True).split("## 8. ")[1]
    ok(md_all.startswith("محادثات وضع الاختبار (2)") and "قديمة" in md_all, "status=all includes the old one")
_tl.LOG_DIR = _old_dir
# ---- the update turns «وضع الاختبار» on at the library (apply_update.apply_settings)
sys.path.insert(0, os.path.join(os.getcwd(), "..", "scripts"))
import apply_update as _au
with _tf.TemporaryDirectory() as _app, _tf.TemporaryDirectory() as _upd:
    os.makedirs(os.path.join(_app, "data"))
    with open(os.path.join(_app, "data", "llm_settings.json"), "w") as f:
        _j.dump({"provider": "openai", "model": "m", "openai_key": "secret-stays", "test_log": False}, f)
    notes = []
    ok(_au.apply_settings(_app, _upd, notes, say=lambda *a: None) == {}, "no settings.json in the update: nothing changes")
    with open(os.path.join(_upd, "settings.json"), "w") as f:
        _j.dump({"test_log": True, "provider": "evil"}, f)
    ch = _au.apply_settings(_app, _upd, notes, say=lambda *a: None)
    st = _j.load(open(os.path.join(_app, "data", "llm_settings.json")))
    ok(ch == {"test_log": [False, True]} and st["test_log"] is True and st["provider"] == "openai"
       and st["openai_key"] == "secret-stays" and not notes, "test_log turned on; other settings (provider, key) untouched")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
