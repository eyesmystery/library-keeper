"""[CORR] Ibrahim 2026-10-30 «عدل سنة اصل الكتاب 4045»: the three-way merge keeps a value the library changed itself,
so a developer's correction of it travels as corrections.json and apply_update.apply_corrections writes it only while
the library still holds the value the developer saw («expect»)."""
import os, sys, json, sqlite3, tempfile, importlib.util
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("apply_update", os.path.join(os.path.dirname(HERE), "scripts", "apply_update.py"))
AU = importlib.util.module_from_spec(spec); spec.loader.exec_module(AU)
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "library.db")
con = sqlite3.connect(db)
con.executescript("""CREATE TABLE books (book_id INTEGER PRIMARY KEY, title TEXT, original_year INTEGER, original_year_source TEXT, price REAL);
CREATE TABLE audit_log (log_id INTEGER PRIMARY KEY, at TEXT, user_id INTEGER, username TEXT, action TEXT, target TEXT, details TEXT);
INSERT INTO books VALUES (4045, 'اختلاف المتشابه', 2025, 'staff', 50);
INSERT INTO books VALUES (10, 'كتاب آخر', 1999, 'staff', 20);
INSERT INTO books VALUES (11, 'كتاب ثالث', 1980, 'web', 20);""")
con.commit(); con.close()
upd = os.path.join(tmp, "upd"); os.makedirs(upd)
items = [
    {"book_id": 4045, "field": "original_year", "expect": 2025, "value": 1970, "source": "https://example.org/w"},
    {"book_id": 4045, "field": "original_year_source", "expect": "staff", "value": "web", "source": "https://example.org/w"},
    {"book_id": 10, "field": "original_year", "expect": 2025, "value": 1970, "source": "x"},     # library holds another value
    {"book_id": 11, "field": "original_year", "expect": 2000, "value": 1980, "source": "x"},     # already the value
    {"book_id": 4045, "field": "price", "expect": 50, "value": 1, "source": "x"},                # stock: never
    {"book_id": 999, "field": "original_year", "expect": None, "value": 1, "source": "x"},       # no such book
]
json.dump({"items": items}, open(os.path.join(upd, "corrections.json"), "w", encoding="utf-8"))

dry = AU.apply_corrections(db, upd, "0.9.15 beta", dry_run=True)
c = sqlite3.connect(db)
ok(len(dry["applied"]) == 2 and c.execute("SELECT original_year FROM books WHERE book_id=4045").fetchone()[0] == 2025,
   "dry run lists 2 corrections and writes nothing")
res = AU.apply_corrections(db, upd, "0.9.15 beta")
row = c.execute("SELECT original_year, original_year_source FROM books WHERE book_id=4045").fetchone()
ok(res["error"] is None and row == (1970, "web"), "4045: 2025 → 1970 and staff → web written")
ok(c.execute("SELECT original_year FROM books WHERE book_id=10").fetchone()[0] == 1999, "a book holding another value is left as it is")
why = {(s["book_id"], s["field"]): s["why"] for s in res["skipped"]}
ok("another value" in why.get((10, "original_year"), ""), "…and the report says why")
ok(why.get((11, "original_year")) == "already this value", "a book already at the value is skipped")
ok(why.get((4045, "price")) == "field not correctable" and c.execute("SELECT price FROM books WHERE book_id=4045").fetchone()[0] == 50,
   "stock fields are never corrected")
ok("no such book" in why.get((999, "original_year"), ""), "an unknown book is skipped")
aud = c.execute("SELECT action, target, details FROM audit_log").fetchall()
ok(len(aud) == 2 and all(a[0] == "book.corrected_by_update" and a[1] == "4045" for a in aud), "each written correction is in the audit")
again = AU.apply_corrections(db, upd, "0.9.15 beta")
ok(again["applied"] == [], "installing again changes nothing")
text = AU.report_text({"corrections": res, "notes": [], "mode": "merge", "tables": {}})
if text is not None:
    ok("Developer corrections written" in text, "the update report lists the corrections")
os.remove(os.path.join(upd, "corrections.json"))
ok(AU.apply_corrections(db, upd, "x") == {"applied": [], "skipped": [], "error": None}, "no corrections.json: nothing")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
