"""Inbox items solved by an update are closed at the library when it installs the update (track INBOX,
Ibrahim 2026-10-11: «البلاغات اللي انحلت تنحذف خلاص او تعامل انها مغلقة محلولة»).

scripts/apply_update.py: apply_resolutions reads inbox_resolutions.json from the update folder and marks
the open items it names «تمت معالجته» (handled_by «تحديث <version>»), with an audit_log row. Runs on the
throwaway test database."""
import os, sys, sqlite3, json, shutil, tempfile
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models
models.init_db()
import app as A   # creates the inbox tables
import visitor_notes, open_questions, staff_notes, staff_ideas, inbox_export
ROOT = os.path.dirname(os.getcwd())
sys.path.append(os.path.join(ROOT, "scripts"))
import apply_update as U
import make_release
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

DB = models.DB_PATH
con = sqlite3.connect(DB)
for t in ("visitor_notes", "open_questions", "staff_notes", "staff_ideas", "audit_log"):
    con.execute(f"DELETE FROM {t}")
book = con.execute("SELECT book_id, title FROM books LIMIT 1").fetchone()
ins = "INSERT INTO visitor_notes (kind, book_id, book_title, reason, note, language, created_at) VALUES (?,?,?,?,?,?,?)"
# times as the library stores them (UTC); the export shows them +3 (Riyadh)
r1 = con.execute(ins, ("suggestion", None, None, None, "Have the prices of the books added", "ar", "2026-10-09T17:57:12+00:00")).lastrowid
r2 = con.execute(ins, ("suggestion", None, None, None, "Have the prices of the books added", "ar", "2026-10-10T09:00:00+00:00")).lastrowid  # same text, other day
r3 = con.execute(ins, ("report", book[0], book[1], "details", "اسم المؤلف غير موجود ولا عدد الصفحات", "ar", "2026-10-09T17:30:05+00:00")).lastrowid
r4 = con.execute(ins, ("suggestion", None, None, None, "اقتراح عولج من قبل", "ar", "2026-10-09T18:04:00+00:00")).lastrowid
con.execute("UPDATE visitor_notes SET status='done', handled_by='ahmed' WHERE note_id=?", (r4,))
con.execute("INSERT INTO open_questions (kind, phrase, phrase_key, times, first_at, last_at) VALUES ('topic','الاستفادة','الاستفاده',1,'2026-10-09T17:07:00+00:00','2026-10-09T17:07:00+00:00')")
con.execute("INSERT INTO open_questions (kind, phrase, phrase_key, times, first_at, last_at) VALUES ('missing_book','قوة الآن','قوه الان',1,'2026-10-09T17:48:00+00:00','2026-10-09T17:48:00+00:00')")
q1 = con.execute("SELECT q_id FROM open_questions WHERE phrase='الاستفادة'").fetchone()[0]
e1 = con.execute("INSERT INTO staff_notes (book_id, book_title, note, created_by, created_at) VALUES (?,?,?,?,?)",
                 (book[0], book[1], "الغلاف غلط", "ahmed", "2026-10-09T10:00:00+00:00")).lastrowid
i1 = con.execute("INSERT INTO staff_ideas (text, created_by, created_at) VALUES (?,?,?)", ("زر طباعة", "ahmed", "2026-10-09T11:00:00+00:00")).lastrowid
con.commit(); con.close()

ok(U.riyadh_minute("2026-10-09T17:57:12+00:00") == "2026-10-09 20:57" and U.riyadh_minute("bad") is None,
   "stored UTC times are read as the export shows them (Riyadh, to the minute)")

upd = tempfile.mkdtemp(prefix="upd-")
items = [
    {"kind": "suggestion", "created": "2026-10-09 20:57", "text": "Have the prices", "state": "solved", "resolution": "السعر في البطاقة"},
    {"kind": "report", "book_id": book[0], "created": "2026-10-09 20:30", "text": "اسم المؤلف", "state": "waiting", "resolution": "-"},
    {"kind": "suggestion", "created": "2026-10-09 21:04", "text": "اقتراح عولج", "state": "solved", "resolution": "-"},
    {"kind": "question", "question_kind": "topic", "text": "الاستفادة", "state": "solved", "resolution": "لا يلزم شيء"},
    {"kind": "staff_edit", "id": e1, "created": "2026-10-09 13:00", "state": "solved", "resolution": "صُحح الغلاف"},
    {"kind": "staff_idea", "id": i1, "created": "2026-10-09 99:99", "state": "solved", "resolution": "-"},   # id with a wrong date: not closed
    {"kind": "suggestion", "state": "solved", "resolution": "nothing to recognise it by"},
    {"kind": "whatever", "text": "x", "state": "solved"},
]
json.dump({"format": "inbox-resolutions-1", "items": items}, open(os.path.join(upd, "inbox_resolutions.json"), "w", encoding="utf-8"), ensure_ascii=False)

dry = U.apply_resolutions(DB, upd, "0.9.10 beta", dry_run=True)
con = sqlite3.connect(DB)
ok(sorted((c["kind"], c["id"]) for c in dry["closed"]) == sorted([("suggestion", r1), ("question", q1), ("staff_edit", e1)])
   and con.execute("SELECT COUNT(*) FROM visitor_notes WHERE status='open'").fetchone()[0] == 3,
   "dry run: lists what it would close and writes nothing")
con.close()

res = U.apply_resolutions(DB, upd, "0.9.10 beta")
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
st = {r["note_id"]: dict(r) for r in con.execute("SELECT * FROM visitor_notes")}
ok(res["error"] is None and st[r1]["status"] == "done" and st[r1]["handled_by"] == "تحديث 0.9.10 beta" and st[r1]["handled_at"],
   "a solved suggestion is closed, handled by «تحديث <version>»")
ok(st[r2]["status"] == "open", "the same text on another day is a different item and stays open")
ok(st[r3]["status"] == "open", "an item whose state is not «solved» stays open")
ok(st[r4]["handled_by"] == "ahmed", "an item staff already handled is left as they left it")
ok(con.execute("SELECT status FROM open_questions WHERE q_id=?", (q1,)).fetchone()[0] == "done"
   and con.execute("SELECT status FROM open_questions WHERE phrase='قوة الآن'").fetchone()[0] == "open",
   "questions are matched by kind and phrase; other questions stay open")
ok(con.execute("SELECT status FROM staff_notes WHERE note_id=?", (e1,)).fetchone()[0] == "done"
   and con.execute("SELECT status FROM staff_ideas WHERE idea_id=?", (i1,)).fetchone()[0] == "open",
   "matched by item number, guarded by the date: a wrong date closes nothing")
aud = [dict(r) for r in con.execute("SELECT * FROM audit_log WHERE action='inbox.resolved_by_update' ORDER BY log_id")]
ok(len(aud) == 3 and aud[0]["username"] == "تحديث 0.9.10 beta" and json.loads(aud[0]["details"])["resolution"] == "السعر في البطاقة",
   "every closed item has an audit_log row with the resolution")
ok(len(res["not_open"]) == 4, "items not open here are reported, not errors (handled already, wrong date, nothing to match, unknown kind)")
con.close()

again = U.apply_resolutions(DB, upd, "0.9.11 beta")
ok(again["closed"] == [], "installing a later update with the same file closes nothing twice")

# the update report names what was closed
rep = {"from_version": "0.9.9 beta", "update": {}, "at": "x", "mode": "merge", "tables": {}, "inbox": res}
txt = U.report_text(rep)
ok("Inbox items closed by this update (3)" in txt and "السعر في البطاقة" in txt, "the update report lists the closed items")

# a broken file is a note, never a crash
open(os.path.join(upd, "inbox_resolutions.json"), "w").write("{not json")
bad = U.apply_resolutions(DB, upd, "x")
ok(bad["error"] and bad["closed"] == [], "a broken resolutions file closes nothing and is reported")
shutil.rmtree(upd)
ok(U.apply_resolutions(DB, upd, "x") == {"closed": [], "not_open": [], "error": None}, "an update without the file changes nothing")

# the export shows the item number used in the file
con = sqlite3.connect(DB)
con.execute("UPDATE visitor_notes SET status='open', handled_by=NULL, handled_at=NULL"); con.commit(); con.close()
text, _ = inbox_export.build("open", "test")
ok(f"رقم البند: {r1}" in text and f"رقم البند: {r3}" in text and "release/inbox_resolutions.json" in text,
   "the export shows «رقم البند» for reports and suggestions and tells Claude where solved items go")
ok("| رقم البند |" in text, "the unanswered-questions table has an item-number column")

# the project's own file is valid and only uses known kinds
real = os.path.join(os.path.dirname(os.environ.get("PYTHONPATH", "")), "release", "inbox_resolutions.json")
if os.path.exists(real):
    d = json.load(open(real, encoding="utf-8"))
    ok(d.get("format") == "inbox-resolutions-1" and all(i.get("kind") in U.INBOX_KINDS and i.get("state") in ("solved", "waiting")
                                                        and (i.get("text") or i.get("id") is not None) for i in d["items"]),
       "release/inbox_resolutions.json is valid")
ok("inbox_resolutions.json" in open(make_release.__file__, encoding="utf-8").read(), "make_release copies the file into the update package")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
