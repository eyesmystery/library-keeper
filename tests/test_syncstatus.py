"""[SYNCSTATUS 2026-11-11] Ibrahim: «اذا وصلنا ملف من المكتبة لازم تاخذ بعين الاعتبار وتعدل ملفاتنا». At the library a staff
«تمت مراجعة الكتاب» on a book that needs identity confirmation marks it matched; mirroring that review from the inbox
(scripts/apply_library_edits.py) now does the same here, with the same note and an audit row."""
import os, sys, sqlite3, json, importlib.util
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

spec = importlib.util.spec_from_file_location("ale", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "apply_library_edits.py"))
ALE = importlib.util.module_from_spec(spec); spec.loader.exec_module(ALE)
con = sqlite3.connect(models.DB_PATH)
b1, b2 = [r[0] for r in con.execute("SELECT book_id FROM books WHERE active = 1 ORDER BY book_id LIMIT 2")]
con.execute("UPDATE books SET research_status = 'needs_confirmation', research_note_ar = 'x' WHERE book_id = ?", (b1,))
con.execute("UPDATE books SET research_status = 'matched' WHERE book_id = ?", (b2,))
con.commit()
now = "2026-11-11T08:00:00+00:00"
ok(ALE.confirm_like_library(con, b1, "layan", "2026-11-07 15:09", now, "inbox.md", 187) is True, "a needs_confirmation book reviewed at the library is confirmed")
r = con.execute("SELECT research_status, research_note_ar FROM books WHERE book_id = ?", (b1,)).fetchone()
ok(r[0] == "matched" and r[1] == "أكّد layan هوية الكتاب عند مراجعته في المكتبة (2026-11-07)", "status matched with the library's note")
a = con.execute("SELECT details FROM audit_log WHERE action = 'library.identity_confirmed' AND target = ? ORDER BY log_id DESC LIMIT 1", (str(b1),)).fetchone()
d = json.loads(a[0]) if a else {}
ok(d.get("research_status") == ["needs_confirmation", "matched"] and d.get("research_note_ar", [None])[0] == "x" and "§5.187" in d.get("source", ""),
   "the audit row keeps the before value and the inbox item")
ok(ALE.confirm_like_library(con, b1, "layan", "2026-11-07 15:09", now, "inbox.md", 187) is False, "twice: no second change")
ok(ALE.confirm_like_library(con, b2, "layan", "2026-11-07 15:09", now, "inbox.md", 188) is False, "a matched book is left alone")
src = open(ALE.__file__, encoding="utf-8").read()
ok('if act == "book.reviewed":' in src and "confirm_like_library(conn, bid" in src, "the mirror calls it for reviews (not for «إلغاء المراجعة»)")
con.close()
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
