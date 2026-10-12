"""«تأكيد هوية الكتاب» (2026-10-04): staff answer stored for the manager, with undo; the book is not changed."""
import os, sys, json, sqlite3
sys.path.insert(0, os.getcwd())
import models, app as A, confirmations
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F; print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
b1, b2 = [r[0] for r in con.execute("select book_id from books order by book_id limit 2")]
cands = [{"title": "كتاب أول", "author": "مؤلف أول", "publisher": "دار", "year": 2020, "url": "https://example.com/a"},
         {"title": "كتاب ثان", "author": "مؤلف ثان", "publisher": None, "year": None, "url": "javascript:alert(1)"}]
con.execute("update books set research_status='needs_confirmation', research_note='note', research_candidates=? where book_id=?",
            (json.dumps(cands, ensure_ascii=False), b1))
con.execute("update books set research_status='matched' where book_id=?", (b2,))
con.commit()
before = dict(con.execute("select * from books where book_id=?", (b1,)).fetchone())
c = login(A.app.test_client(), "staff")

d = c.get(f"/api/admin/books/{b1}").get_json()
cf = d["confirmation"]
ok(cf and cf["needed"] and cf["current"] is None and cf["note"] == "note", "card of a book needing confirmation carries the section data")
keys = [o["key"] for o in cf["options"]]
ok(keys == ["candidate:0", "candidate:1", "current", "none", "no_copy"], "options: research candidates first, then the fixed ones")
ok(cf["options"][0]["url"] == "https://example.com/a" and cf["options"][1]["url"] is None, "only http(s) candidate links are passed on")
ok(c.get(f"/api/admin/books/{b2}").get_json()["confirmation"] is None, "a matched book has no confirmation section")

r = c.post(f"/api/admin/books/{b1}/confirmation", json={"choice": "", "comment": "  "})
ok(r.status_code == 400, "empty answer is refused")
ok(c.post(f"/api/admin/books/{b1}/confirmation", json={"choice": "bogus"}).status_code == 400, "unknown choice is refused")
ok(c.post(f"/api/admin/books/{b1}/confirmation", json={"choice": "none"}).status_code == 400, "«ولا واحد» needs a comment")
ok(A.app.test_client().post(f"/api/admin/books/{b1}/confirmation", json={"choice": "current"}).status_code in (401, 403),
   "signed-out request is refused")

r = c.post(f"/api/admin/books/{b1}/confirmation", json={"choice": "candidate:0", "comment": "المترجم   على الغلاف فلان"})
cur = r.get_json()["confirmation"]["current"]
ok(r.status_code == 200 and cur["choice"] == "candidate:0" and "كتاب أول" in cur["label"] and "مؤلف أول" in cur["label"]
   and cur["comment"] == "المترجم على الغلاف فلان" and cur["by"], "a staff member can store a choice with a comment, under their name")
ok(dict(con.execute("select * from books where book_id=?", (b1,)).fetchone()) == before, "the book record itself is not changed")
cat = c.get("/api/admin/catalog").get_json()
ok(next(b for b in cat if b["book_id"] == b1)["confirmation_answered"] and not next(b for b in cat if b["book_id"] == b2)["confirmation_answered"],
   "catalog rows say which books have an answer")
ans = confirmations.open_answers()
ok(len(ans) == 1 and ans[0]["book_id"] == b1 and ans[0]["chosen_candidate"]["author"] == "مؤلف أول", "the manager's reader lists the open answer with the chosen candidate")

r = c.post(f"/api/admin/books/{b1}/confirmation", json={"comment": "تعليق فقط"})
ok(r.get_json()["confirmation"]["current"]["choice"] is None and len(confirmations.open_answers()) == 1, "a new answer replaces the earlier one; comment alone is enough")
r = c.post(f"/api/admin/books/{b1}/confirmation", json={"undo": True})
ok(r.status_code == 200 and r.get_json()["confirmation"]["current"] is None and confirmations.open_answers() == [], "undo withdraws the answer")
ok(c.post(f"/api/admin/books/{b1}/confirmation", json={"undo": True}).status_code == 404, "nothing left to undo")
ok(con.execute("select count(*) from book_confirmations").fetchone()[0] == 2, "history rows are kept")

c.post(f"/api/admin/books/{b1}/confirmation", json={"choice": "current"})
aid = confirmations.open_answers()[0]["confirmation_id"]
ok(confirmations.mark_handled(aid, "done") == 1 and confirmations.open_answers() == [] and len(confirmations.open_answers(True)) == 1, "handled answers leave the open list")
ok(c.post(f"/api/admin/books/{b1}/confirmation", json={"undo": True}).status_code == 400, "a handled answer cannot be undone")
ok(con.execute("select count(*) from audit_log where action like 'book.confirmation%'").fetchone()[0] >= 4, "answers and undos are in the audit log")

html = open("../admin/index.html", encoding="utf-8").read()
ok("section.block.confirm{" in html and "${confirmBlock(d)}" in html and 'id="cfUndo"' in html and 'name="cfChoice"' in html and 'id="cfComment"' in html,
   "admin card has the coloured confirmation section with option buttons, comment box and undo")
ok(html.index("${confirmBlock(d)}") < html.index('aria-labelledby="nbTitle"') and html.index("البيانات الأساسية</h3>") < html.index("${confirmBlock(d)}"),
   "the section sits right under the basic data")

# ---- out to Claude and back (the app on the library's own computer)
m = login(A.app.test_client(), "manager")
ok(c.get("/api/admin/confirmations").status_code == 403, "staff cannot export the answers")
con.execute("update books set research_status='needs_confirmation', summary='نبذة قديمة' where book_id=?", (b2,)); con.commit()
c.post(f"/api/admin/books/{b2}/confirmation", json={"choice": "none", "comment": "المؤلف على الغلاف: كاتب جديد"})
pk = m.get("/api/admin/confirmations").get_json()
a2 = next(a for a in pk["answers"] if a["book_id"] == b2)
ok(pk["format"] == "book-confirmations-1" and pk["count"] == 1 and a2["comment"].endswith("كاتب جديد") and a2["book"]["title"], "manager export carries the open answers with the book fields")
import inbox_export
md9, cnt9 = inbox_export.build("open", "manager")
blk = md9.split("## 9. ")[1] if "## 9. " in md9 else ""
js9 = json.loads(blk.split("```json\n")[1].split("\n```")[0]) if "```json" in blk else {}
ok(cnt9["confirmations"] == 1 and "ردود الموظفين على تأكيد هوية الكتب (1)" in md9 and f"({b2})" in blk and "كاتب جديد" in blk
   and js9.get("format") == "book-confirmations-1" and js9["answers"][0]["book_id"] == b2 and js9["answers"][0]["book"]["title"]
   and "confirmation-corrections-1" in blk, "the inbox file carries the open answers in section 9, with the full package for Claude")
ok(inbox_export.build("done", "manager")[1]["confirmations"] == 1, "«تمت معالجتها» export lists the handled answer only")
bk = a2["book"]
def pkg(**kw):
    item = {"book_id": b2, "confirmation_id": a2["confirmation_id"],
            "expect": {"title": bk["title"], "author": bk["author"], "research_status": bk["research_status"]},
            "set": {"author": "كاتب جديد", "research_status": "matched"}, "clear_summary": True, "note": "من الغلاف"}
    item.update(kw); return {"format": "confirmation-corrections-1", "items": [item]}
post = lambda p, apply=False: m.post("/api/admin/confirmations/corrections", json={"package": p, "apply": apply})
ok(post({"format": "x"}).get_json()["ok"] is False, "a file of another format is refused")
ok(post(pkg(expect={"title": "غيره", "author": bk["author"], "research_status": bk["research_status"]})).get_json()["items"][0]["problem"],
   "a stale file (record changed since export) is refused")
ok("القيمة الأصلية" in post(pkg(expect={"title": bk["title"], "author": bk["author"]})).get_json()["items"][0]["problem"],
   "a file that changes a field without naming the value it saw is refused")
# audit 2026-10-05: a staff edit made after the preview must not be overwritten by the file
pub = pkg(set={"author": "كاتب جديد", "publisher": "دار الملف", "research_status": "matched"},
          expect={"title": bk["title"], "author": bk["author"], "publisher": bk["publisher"], "research_status": bk["research_status"]})
ok(post(pub).get_json()["ok"], "preview accepts the file while the record is as exported")
con.execute("update books set publisher='دار عدّلها الموظف' where book_id=?", (b2,)); con.commit()
r = post(pub, True)
ok(r.status_code == 400 and "الناشر" in r.get_json()["items"][0]["problem"]
   and con.execute("select publisher, author from books where book_id=?", (b2,)).fetchone()[:] == ("دار عدّلها الموظف", bk["author"]),
   "apply refuses the file when a field it changes was edited after the export; the newer value stays")
con.execute("update books set publisher=? where book_id=?", (bk["publisher"], b2)); con.commit()
# audit 2026-10-05: the old work's attributes must not survive an identity correction
con.execute("delete from attribute_evidence where book_id=?", (b2,))
for attr, val, verified in (("moods", "sad", 0), ("pace", "slow", 0), ("format", "novel", 1)):
    con.execute("""insert into attribute_evidence (book_id, attribute_name, attribute_value, confidence, evidence_summary, source_urls,
                   source_site, review_count, conflict_seen, analyzed_at, human_verified) values (?,?,?,0.9,'x','[]','goodreads',5,0,'2026-10-01',?)""",
                (b2, attr, val, verified))
con.execute("insert or ignore into book_profiles (book_id, overall_status) values (?, 'enriched')", (b2,))
con.execute("update book_profiles set moods='[\"sad\"]', pace='slow', format='novel', themes='[\"x\"]', overall_status='enriched' where book_id=?", (b2,))
con.commit()
ok(post(pkg(set={"price": 1})).get_json()["ok"] is False and post(pkg(set={"research_status": "zzz"})).get_json()["ok"] is False, "only the listed fields and statuses are accepted")
pl = post(pkg()).get_json()
ok(pl["ok"] and {x["field"] for x in pl["items"][0]["changes"]} == {"author", "research_status", "summary", "evidence"}, "preview lists the changes, the old attributes among them")
ok(con.execute("select author from books where book_id=?", (b2,)).fetchone()[0] == bk["author"], "preview writes nothing")
ok(post(pkg(set={"price": 1}), True).status_code == 400, "apply refuses a file with a problem")
r = post(pkg(), True); d = r.get_json()
row = con.execute("select author, research_status, summary, research_note from books where book_id=?", (b2,)).fetchone()
ok(r.status_code == 200 and d["applied"] and d["books"] == 1 and d["handled"] == 1 and row["author"] == "كاتب جديد" and row["research_status"] == "matched"
   and row["summary"] is None and "staff confirmation" in row["research_note"], "apply corrects the record, clears the old summary and notes the source")
ok(os.path.exists(os.path.join(os.path.dirname(models.DB_PATH), "..", "backups", d["backup"])), "a backup is written before the corrections")
ev = {r_["attribute_name"]: (r_["rejected"], r_["rejected_by"]) for r_ in con.execute("select * from attribute_evidence where book_id=?", (b2,))}
card = con.execute("select moods, pace, format, themes from book_profiles where book_id=?", (b2,)).fetchone()
ok(ev["moods"][0] == 1 and ev["pace"][0] == 1 and "تصحيح الهوية" in ev["moods"][1] and ev["format"][0] in (0, None),
   "identity correction rejects the old work's extracted attributes and keeps the staff-confirmed one")
ok(json.loads(card["moods"]) == [] and card["pace"] in (None, "unknown") and card["format"] == "novel" and card["themes"] is None,
   "the card no longer carries the old work's mood and pace")
ok(con.execute("select count(*) from book_relations where source='computed' and (source_book_id=? or target_book_id=?) and relation_type like '%mood%'", (b2, b2)).fetchone()[0] == 0,
   "no computed relation rests on the old mood")
ok(c.post(f"/api/admin/books/{b1}/confirmation", json={"choice": ["x"]}).status_code == 400
   and c.post(f"/api/admin/books/{b1}/confirmation", json={"comment": {"a": 1}}).status_code == 400, "a list or an object as the answer is refused, not a server error")
ok(con.execute("select count(*) from authors where name_ar='كاتب جديد'").fetchone()[0] == 1, "people records follow the corrected author")
ok(m.get("/api/admin/confirmations").get_json()["count"] == 0 and post(pkg(expect={"title": bk["title"], "author": "كاتب جديد", "research_status": "matched"}), True).status_code == 400, "the answer is closed and cannot be applied twice")
ok(c.post("/api/admin/confirmations/corrections", json={"package": pkg()}).status_code == 403, "staff cannot apply corrections")
ok('id="cxCopy"' not in html and 'id="cxDownload"' not in html and 'id="cxApply"' in html and "/api/admin/confirmations/corrections" in html
   and html.index('id="cxFold"') > html.index('id="inboxView"') and html.index('id="cxFold"') < html.index('id="eventsView"')
   and "if (which === 'inbox') { loadInbox(); if (ME && ME.role === 'manager') loadConfirmCount(); }" in html
   and "$('cxFold').hidden = !manager;" in html and 'id="cxFold" hidden>' in html, "the apply-corrections fold sits on the inbox page; the copy / download step is gone")
print(f"TOTAL PASS={P} FAIL={F}")
