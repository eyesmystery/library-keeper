"""[AGY-20261029-E2] The library's staff reviews reach the developer and are mirrored; a genre brought from the library
moves the card's format; a save that changed nothing writes no empty «تعديل بيانات كتاب» row."""
import os, sys, sqlite3, json, importlib.util, tempfile
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, inbox_export as IE
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

ROOT = os.path.dirname(os.getcwd())
spec = importlib.util.spec_from_file_location("ale", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "apply_library_edits.py"))
ALE = importlib.util.module_from_spec(spec); spec.loader.exec_module(ALE)

con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
b1, b2 = [r[0] for r in con.execute("SELECT book_id FROM books WHERE active = 1 ORDER BY book_id LIMIT 2")]

# 1. the export names the review actions
ok(IE.ACTION_AR.get("book.reviewed") == "تمت مراجعة الكتاب" and "book.unreviewed" in IE.ACTION_AR, "inbox §5 carries «تمت مراجعة الكتاب» / «إلغاء مراجعة الكتاب»")
lines = IE._edit_lines({"action": "book.reviewed", "target": str(b1), "username": "yara", "at": "2026-10-29T18:00:00+00:00",
                        "details": json.dumps({"by": "yara", "evidence_ids": [1, 2]})}, IE._books([b1]))
ok(lines[0].startswith("تمت مراجعة الكتاب — ") and f"(book_id {b1})" in lines[0] and not any("evidence" in l for l in lines),
   "a review's §5 item: head with the book, no evidence ids")

# 2. apply_library_edits mirrors a review once
md = f"""## 5. تعديلات نفّذها الموظفون في المكتبة (2)

### 5.1 ★ تمت مراجعة الكتاب — «x» (book_id {b1})
- بواسطة: yara · 2026-10-29 21:00

### 5.2 ★ تعديل بيانات كتاب — «y» (book_id {b2})
- بواسطة: yara · 2026-10-29 21:05

## 6. ملفات
"""
path = os.path.join(tempfile.mkdtemp(), "inbox.md"); open(path, "w", encoding="utf-8").write(md)
items = ALE.parse(path)
rv = ALE.reviews_to_mirror(items, con)
ok(len(rv) == 1 and rv[0][0] == b1 and rv[0][1] == "book.reviewed", "one review to mirror, for the right book")
con.execute("INSERT INTO audit_log (at, username, action, target, details) VALUES (?,?,?,?,?)",
            ("2026-10-29T21:30:00+00:00", "MGR (Claude)", "book.reviewed", str(b1),
             json.dumps({"by": "yara (المكتبة)", "library_ref": "yara 2026-10-29 21:00"}, ensure_ascii=False)))
con.commit()
ok(ALE.reviews_to_mirror(items, con) == [], "an already mirrored review is not repeated")
ok(A._reviewed_map().get(b1, {}).get("by") == "yara (المكتبة)", "the mirrored review takes the book out of «تحتاج مراجعة» here")

# 3. a genre brought from the library moves the format
fmt = ALE.sync_format(con, b2, "قصص قصيرة"); con.commit()
row = con.execute("SELECT format FROM book_profiles WHERE book_id = ?", (b2,)).fetchone()
ok(fmt == A.GENRE_FORMAT["قصص قصيرة"] and row and row[0] == fmt, "genre → book_profiles.format follows")
bad = con.execute("SELECT COUNT(*) FROM attribute_evidence WHERE book_id = ? AND attribute_name = 'format' AND COALESCE(rejected,0) = 0 AND attribute_value <> ?", (b2, fmt)).fetchone()[0]
ok(bad == 0, "no unrejected format evidence keeps the old format")

# 4. a save that changes nothing writes no book.update row
s = login(A.app.test_client(), "staff")
H = lambda c: {"X-CSRF-Token": c.csrf}
cur = con.execute("SELECT publisher FROM books WHERE book_id = ?", (b1,)).fetchone()[0]
n0 = con.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'book.update' AND target = ?", (str(b1),)).fetchone()[0]
r = s.patch(f"/api/books/{b1}", json={"publisher": cur}, headers=H(s))
n1 = con.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'book.update' AND target = ?", (str(b1),)).fetchone()[0]
ok(r.status_code == 200 and n1 == n0, f"unchanged save: no empty book.update row ({r.status_code}, {n0}→{n1})")
r = s.patch(f"/api/books/{b1}", json={"publisher": (cur or "") + " اختبار"}, headers=H(s))
n2 = con.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'book.update' AND target = ?", (str(b1),)).fetchone()[0]
ok(r.status_code == 200 and n2 == n0 + 1, "a real change is still written")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
