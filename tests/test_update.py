"""Updates that merge instead of replacing (scripts/apply_update.py, 2026-10-09).

The library keeps its accounts, settings, events, stock and activity; the code is replaced; the catalog
is merged field by field against the catalog installed last time (the base). Everything runs on
throwaway copies of the test database."""
import os, sys, sqlite3, shutil, socket, json
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models
models.init_db()
import app as A   # creates the tables the admin features add on start
ROOT = os.path.dirname(os.getcwd())
sys.path.append(os.path.join(ROOT, "scripts"))   # after backend/: both folders have a confirmations.py
import apply_update as U
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

WORK = os.path.join(ROOT, "upd-test")
os.makedirs(WORK, exist_ok=True)
SRC = models.DB_PATH

def copy(name):
    p = os.path.join(WORK, name)
    U.copy_db(SRC, p)
    return p

def one(db, sql, args=()):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()

def run(db, *stmts):
    con = sqlite3.connect(db)
    try:
        for s in stmts:
            con.execute(*s) if isinstance(s, tuple) else con.execute(s)
        con.commit()
    finally:
        con.close()

# ---- the starting point both sides share: a few known rows on top of the test catalog
ids = [r[0] for r in sqlite3.connect(SRC).execute("SELECT book_id FROM books ORDER BY book_id LIMIT 8")]
A_, B_, C_, D_, E_, F_, G_, H_ = ids
run(SRC,
    ("UPDATE books SET summary='نبذة الأساس', genre='رواية', price=50, quantity=3 WHERE book_id IN (?,?,?,?,?,?,?,?)", ids),
    ("DELETE FROM attribute_evidence WHERE book_id IN (?,?)", (A_, B_)),
    ("INSERT INTO attribute_evidence (book_id, attribute_name, attribute_value, confidence, source_site) VALUES (?, 'pace', 'fast', 0.8, 'goodreads')", (A_,)),
    ("INSERT INTO attribute_evidence (book_id, attribute_name, attribute_value, confidence, source_site) VALUES (?, 'mood', 'dark', 0.7, 'goodreads')", (A_,)),
    ("INSERT INTO attribute_evidence (book_id, attribute_name, attribute_value, confidence, source_site) VALUES (?, 'mood', 'warm', 0.7, 'abjjad')", (B_,)),
    ("INSERT INTO authors (name_ar, origin) VALUES ('مؤلف الأساس', 'مصر')"),
    ("INSERT INTO authors (name_ar) VALUES ('مؤلف سيُدمج')"))
ev = {r[1]: r[0] for r in sqlite3.connect(SRC).execute("SELECT evidence_id, attribute_value FROM attribute_evidence WHERE book_id IN (?,?)", (A_, B_))}
auth_base = one(SRC, "SELECT author_id FROM authors WHERE name_ar='مؤلف الأساس'")[0]
auth_gone = one(SRC, "SELECT author_id FROM authors WHERE name_ar='مؤلف سيُدمج'")[0]
run(SRC, ("INSERT OR REPLACE INTO book_authors (book_id, author_id, role) VALUES (?, ?, 'author')", (A_, auth_gone)))
top = one(SRC, "SELECT MAX(book_id) FROM books")[0]
os.makedirs(os.path.join(WORK, "covers0"), exist_ok=True)
for name, data in ((f"{A_}.jpg", b"cover-a-v1"), (f"{B_}.jpg", b"cover-b-v1")):
    open(os.path.join(WORK, "covers0", name), "wb").write(data)
BASE = U.build_catalog(SRC, os.path.join(WORK, "base.db"), {"version": "0.9.6 beta"}, os.path.join(WORK, "covers0"))
ok(set(U.tables_of(sqlite3.connect(BASE))) >= {"books", "authors", "book_profiles", "attribute_evidence", "update_meta", "cover_files"}
   and "staff_users" not in U.tables_of(sqlite3.connect(BASE)) and "audit_log" not in U.tables_of(sqlite3.connect(BASE)),
   "catalog file: the catalog tables and the cover list, no accounts and no activity")
ok(dict(sqlite3.connect(BASE).execute("SELECT name, sha1 FROM cover_files")).keys() == {f"{A_}.jpg", f"{B_}.jpg"}, "catalog file: lists the cover files with their hashes")

# ---- Ibrahim's side after more work
UP = copy("upstream.db")
run(UP,
    ("UPDATE books SET summary='نبذة جديدة من التحديث' WHERE book_id=?", (A_,)),
    ("UPDATE books SET genre='تاريخ' WHERE book_id=?", (B_,)),
    ("UPDATE books SET summary='نبذة التحديث للكتاب ج' WHERE book_id=?", (C_,)),
    ("UPDATE books SET price=99, quantity=0 WHERE book_id=?", (D_,)),
    ("INSERT INTO books (book_id, title, author, rewaa_sku, summary, quantity) VALUES (?, 'كتاب جديد من التحديث', 'كاتب', 'SKU-UP-1', 'نبذته', 1)", (top + 1,)),
    ("INSERT INTO books (book_id, title, author, rewaa_sku, summary, quantity, price) VALUES (?, 'كتاب أضافه الطرفان', 'كاتب', 'SKU-BOTH', 'نبذة من التحديث', 1, 10)", (top + 5,)),
    ("INSERT INTO book_profiles (book_id, pace) VALUES (?, 'fast')", (top + 1,)),
    ("INSERT INTO authors (name_ar, origin) VALUES ('مؤلف جديد', 'العراق')"),
    ("UPDATE authors SET origin='السودان' WHERE author_id=?", (auth_base,)),
    ("DELETE FROM attribute_evidence WHERE evidence_id IN (?, ?)", (ev["dark"], ev["warm"])),
    ("INSERT INTO attribute_evidence (book_id, attribute_name, attribute_value, confidence, source_site) VALUES (?, 'ending_tone', 'hopeful', 0.9, 'goodreads')", (A_,)),
    ("DELETE FROM book_authors WHERE author_id=?", (auth_gone,)),
    ("DELETE FROM authors WHERE author_id=?", (auth_gone,)))
auth_new = one(UP, "SELECT author_id FROM authors WHERE name_ar='مؤلف جديد'")[0]
run(UP, ("INSERT INTO book_authors (book_id, author_id, role) VALUES (?, ?, 'author')", (A_, auth_new)))
for bid in (E_, F_):   # deleted on Ibrahim's side («ليس كتابًا»), with their records
    t, a = one(UP, "SELECT title, author FROM books WHERE book_id=?", (bid,))
    run(UP, ("INSERT INTO deleted_books (book_id, title, author, reason, deleted_at, deleted_by, snapshot) VALUES (?, ?, ?, 'not_a_book', '2026-10-09T10:00:00+00:00', 'ibrahim', '{}')", (bid, t, a)),
        ("DELETE FROM book_profiles WHERE book_id=?", (bid,)), ("DELETE FROM books WHERE book_id=?", (bid,)))
os.makedirs(os.path.join(WORK, "covers1"), exist_ok=True)
for name, data in ((f"{A_}.jpg", b"cover-a-v2"), (f"{B_}.jpg", b"cover-b-v2"), (f"{top + 1}.jpg", b"cover-new")):
    open(os.path.join(WORK, "covers1", name), "wb").write(data)
NEW = U.build_catalog(UP, os.path.join(WORK, "new.db"), {"version": "0.9.7 beta", "git_commit": "abc1234"}, os.path.join(WORK, "covers1"))

# ---- the library's side after its own changes
def library(name):
    lib = copy(name)
    run(lib,
        ("INSERT INTO staff_users (username, display_name, role, password_hash, active, created_at) VALUES ('noura', 'نورة', 'manager', 'hash-of-the-library', 1, '2026-10-09')"),
        ("INSERT INTO events (title, event_date, created_at) VALUES ('أمسية المكتبة', '2026-11-01', '2026-10-09')"),
        ("UPDATE books SET summary='نبذة عدّلها الموظف' WHERE book_id=?", (B_,)),
        ("UPDATE books SET summary='نبذة المكتبة للكتاب ج' WHERE book_id=?", (C_,)),
        ("UPDATE books SET price=70, quantity=9, shelf_location='رف 4' WHERE book_id=?", (D_,)),
        ("UPDATE books SET summary='عدّلناه قبل أن يُحذف' WHERE book_id=?", (F_,)),
        ("INSERT INTO books (title, author, rewaa_sku, quantity) VALUES ('كتاب أضافته المكتبة', 'كاتب محلي', 'SKU-LIB-1', 4)"),
        ("INSERT INTO books (title, author, rewaa_sku, quantity, price) VALUES ('كتاب أضافه الطرفان', 'كاتب', 'SKU-BOTH', 6, 45)"),
        ("UPDATE attribute_evidence SET human_verified=1, verified_by='نورة' WHERE evidence_id=?", (ev["warm"],)),
        ("DELETE FROM books WHERE book_id=?", (H_,)), ("DELETE FROM book_profiles WHERE book_id=?", (H_,)))
    return lib
LIB = library("library.db")
lib_book = one(LIB, "SELECT book_id FROM books WHERE rewaa_sku='SKU-LIB-1'")[0]
ok(lib_book == top + 1, "set-up: the library's own new book took the same id as the update's new book")
run(LIB, ("INSERT INTO book_profiles (book_id, pace) VALUES (?, 'slow')", (lib_book,)))

rep = U.merge(LIB, NEW, BASE)
g = lambda sql, args=(): one(LIB, sql, args)
ok(rep["mode"] == "three-way", "merge with a base is a three-way merge")
ok(g("SELECT username, password_hash FROM staff_users WHERE username='noura'") == ("noura", "hash-of-the-library"), "the library's account and its password stay")
ok(g("SELECT title FROM events WHERE title='أمسية المكتبة'") is not None, "the library's event stays")
ok(g("SELECT summary FROM books WHERE book_id=?", (A_,))[0] == "نبذة جديدة من التحديث", "a field only the update changed is written")
ok(g("SELECT summary, genre FROM books WHERE book_id=?", (B_,)) == ("نبذة عدّلها الموظف", "تاريخ"), "same book: the library's field stays, the update's other field arrives")
ok(g("SELECT summary FROM books WHERE book_id=?", (C_,))[0] == "نبذة المكتبة للكتاب ج", "both changed the same field: the library's value stays")
conf = [c for c in rep["conflicts"] if c["table"] == "books" and c["key"] == [C_] and c["column"] == "summary"]
ok(len(conf) == 1 and conf[0]["update"] == "نبذة التحديث للكتاب ج" and conf[0]["before"] == "نبذة الأساس", "that conflict is in the report with the three values")
ok(g("SELECT price, quantity, shelf_location FROM books WHERE book_id=?", (D_,)) == (70.0, 9, "رف 4"), "stock (price, quantity, shelf) is always the library's")
ok(g("SELECT title, rewaa_sku FROM books WHERE book_id=?", (top + 1,)) == ("كتاب جديد من التحديث", "SKU-UP-1"), "the update's new book arrives under its own id")
moved = g("SELECT book_id, quantity FROM books WHERE rewaa_sku='SKU-LIB-1'")
ok(moved[0] >= U.LOCAL_ID_START and moved[1] == 4, "the library's own book with that id moved to the library's id range, untouched")
ok(g("SELECT pace FROM book_profiles WHERE book_id=?", (moved[0],)) == ("slow",) and g("SELECT pace FROM book_profiles WHERE book_id=?", (top + 1,)) == ("fast",), "its card moved with it; the new book has its own card")
ok(rep["cover_renames"] == [[top + 1, moved[0]], [top + 2, top + 5]], "cover files of the moved and of the joined book are listed for renaming")
both = sqlite3.connect(LIB).execute("SELECT book_id, quantity, price, summary FROM books WHERE rewaa_sku='SKU-BOTH'").fetchall()
ok(both == [(top + 5, 6, 45.0, "نبذة من التحديث")], "a book both sides added (same SKU) is one row: the update's id and summary, the library's stock")
ok(g("SELECT 1 FROM books WHERE book_id=?", (E_,)) is None and g("SELECT reason FROM deleted_books WHERE book_id=?", (E_,)) == ("not_a_book",), "a book deleted in the update is deleted here, with its record in the deleted list")
ok(g("SELECT summary FROM books WHERE book_id=?", (F_,)) == ("عدّلناه قبل أن يُحذف",) and any(k["table"] == "books" and k["key"] == [F_] for k in rep["kept_after_removal"]),
   "…but not when the library changed it: kept and reported")
ok(g("SELECT 1 FROM books WHERE book_id=?", (H_,)) is None, "a book the library deleted does not come back")
ok(g("SELECT origin FROM authors WHERE author_id=?", (auth_base,)) == ("السودان",) and g("SELECT origin FROM authors WHERE name_ar='مؤلف جديد'") == ("العراق",), "authors: changed and new records arrive")
ok(g("SELECT 1 FROM book_authors WHERE book_id=? AND author_id=?", (A_, auth_new)) is not None and g("SELECT 1 FROM book_authors WHERE author_id=?", (auth_gone,)) is None
   and g("SELECT 1 FROM authors WHERE author_id=?", (auth_gone,)) is None, "author links follow; an author the update merged away is removed once nothing uses it")
ok(g("SELECT 1 FROM attribute_evidence WHERE evidence_id=?", (ev["dark"],)) is None and g("SELECT 1 FROM attribute_evidence WHERE book_id=? AND attribute_value='hopeful'", (A_,)) is not None,
   "evidence: a row the update removed is removed, a new one is added")
ok(g("SELECT human_verified, verified_by FROM attribute_evidence WHERE evidence_id=?", (ev["warm"],)) == (1, "نورة"), "evidence a staff member verified is kept although the update removed it")
seq = dict(sqlite3.connect(LIB).execute("SELECT name, seq FROM sqlite_sequence"))
ok(all(seq.get(t, 0) >= U.LOCAL_ID_START - 1 for t in ("books", "authors", "attribute_evidence")), "ids for new rows now start in the library's range")
run(LIB, ("INSERT INTO books (title, author) VALUES ('كتاب بعد التحديث', 'x')",))
ok(g("SELECT book_id FROM books WHERE title='كتاب بعد التحديث'")[0] > U.LOCAL_ID_START, "a book added after the update gets a library-range id")
ok(sqlite3.connect(LIB).execute("PRAGMA integrity_check").fetchone()[0] == "ok", "the merged database passes the integrity check")

# the same update again, now with itself as the base: nothing left to do
rep2 = U.merge(LIB, NEW, NEW)
changed = {t: {k: v for k, v in s.items() if k in ("added", "rows_updated", "removed", "renumbered", "joined")} for t, s in rep2["tables"].items()}
ok(not any(changed.values()), f"installing the same update twice changes nothing ({ {t: c for t, c in changed.items() if c} })")
ok(g("SELECT summary FROM books WHERE book_id=?", (C_,))[0] == "نبذة المكتبة للكتاب ج" and not rep2["conflicts"], "the library's own value survives the next update too; a conflict is reported once, not every time")

# ---- first update of an installation that has no base
LIB2 = library("library2.db")
rep3 = U.merge(LIB2, NEW, None)
g2 = lambda sql, args=(): one(LIB2, sql, args)
ok(rep3["mode"] == "first-update-without-base", "without a base the report says so")
ok(g2("SELECT summary FROM books WHERE book_id=?", (C_,))[0] == "نبذة التحديث للكتاب ج"
   and any(r["key"] == [C_] and r["column"] == "summary" and r["library"] == "نبذة المكتبة للكتاب ج" for r in rep3["replaced_values"]),
   "without a base the update's catalog wins and every replaced value is listed")
ok(g2("SELECT price, quantity FROM books WHERE book_id=?", (D_,)) == (70.0, 9) and g2("SELECT password_hash FROM staff_users WHERE username='noura'") == ("hash-of-the-library",)
   and g2("SELECT 1 FROM events WHERE title='أمسية المكتبة'") is not None, "…but stock, accounts and events are still the library's")
m2 = g2("SELECT book_id FROM books WHERE rewaa_sku='SKU-LIB-1'")[0]
ok(m2 >= U.LOCAL_ID_START and g2("SELECT title FROM books WHERE book_id=?", (top + 1,)) == ("كتاب جديد من التحديث",), "without a base a different book under the same id is moved, not overwritten")
ok(sqlite3.connect(LIB2).execute("SELECT COUNT(*) FROM books WHERE rewaa_sku='SKU-BOTH'").fetchone()[0] == 1, "without a base a book both sides have is still one row")

# ---- the whole installation: code, database, covers, report
def make_app(name, lib_db):
    app = os.path.join(WORK, name)
    for d in ("data/covers", "backend/venv/bin", "frontend", "admin", "scripts"):
        os.makedirs(os.path.join(app, d))
    shutil.copy2(lib_db, os.path.join(app, "data", "library.db"))
    open(os.path.join(app, "data", "llm_settings.json"), "w").write('{"provider": "ollama_cloud"}')
    open(os.path.join(app, "backend", "app.py"), "w").write("old code")
    open(os.path.join(app, "backend", "old_module.py"), "w").write("gone in the update")
    open(os.path.join(app, "backend", "requirements.txt"), "w").write("Flask==3.0.3\n")
    open(os.path.join(app, "backend", "venv", "bin", "python"), "w").write("the environment")
    open(os.path.join(app, "frontend", "index.html"), "w").write("old page")
    open(os.path.join(app, "VERSION"), "w").write("0.9.6 beta\n")
    open(os.path.join(app, "start_library.command"), "w").write("old start")
    open(os.path.join(app, "data", "covers", f"{A_}.jpg"), "wb").write(b"cover-a-v1")          # untouched by the library
    open(os.path.join(app, "data", "covers", f"{B_}.jpg"), "wb").write(b"uploaded-by-staff")   # replaced by the library
    open(os.path.join(app, "data", "covers", f"{top + 1}.jpg"), "wb").write(b"cover-of-the-library-book")
    return app

UPD = os.path.join(WORK, "ThalathSmart-update")
plat = "windows" if os.name == "nt" else "mac"
for d in ("payload/backend", "payload/frontend", "payload/admin", "payload/scripts", f"payload/start/{plat}/scripts", "covers", "bases"):
    os.makedirs(os.path.join(UPD, d))
open(os.path.join(UPD, "payload", "backend", "app.py"), "w").write("new code")
open(os.path.join(UPD, "payload", "backend", "requirements.txt"), "w").write("Flask==3.0.3\n")
open(os.path.join(UPD, "payload", "frontend", "index.html"), "w").write("new page")
open(os.path.join(UPD, "payload", "VERSION"), "w").write("0.9.7 beta\n")
open(os.path.join(UPD, "payload", "start", plat, "start_library.command"), "w").write("new start")
open(os.path.join(UPD, "payload", "start", plat, "scripts", "daily.bat"), "w").write("platform script")
shutil.copy2(NEW, os.path.join(UPD, "catalog.db"))
shutil.copy2(BASE, os.path.join(UPD, "bases", "0.9.6b.db"))
for name in os.listdir(os.path.join(WORK, "covers1")):
    shutil.copy2(os.path.join(WORK, "covers1", name), os.path.join(UPD, "covers", name))

LIB3 = library("library3.db")
APP = make_app("app", LIB3)
read = lambda *p: open(os.path.join(APP, *p), "rb").read()
before = read("data", "library.db")
busy = socket.socket(); busy.bind(("127.0.0.1", 0)); busy.listen(1)
try:
    U.apply(APP, UPD, port=busy.getsockname()[1], say=lambda *_: None); stopped = False
except U.UpdateError:
    stopped = True
busy.close()
ok(stopped and read("data", "library.db") == before and read("backend", "app.py") == b"old code", "while the server runs the update refuses and changes nothing")
free = socket.socket(); free.bind(("127.0.0.1", 0)); PORT = free.getsockname()[1]; free.close()
dry = U.apply(APP, UPD, dry_run=True, port=PORT, say=lambda *_: None)
ok(dry["dry_run"] and read("data", "library.db") == before and read("backend", "app.py") == b"old code" and read("data", "covers", f"{A_}.jpg") == b"cover-a-v1"
   and os.path.exists(dry["report_file"]), "a dry run writes the report and changes nothing")
res = U.apply(APP, UPD, port=PORT, say=lambda *_: None)
db3 = os.path.join(APP, "data", "library.db")
ok(res["base_source"].startswith("the update's base") and res["mode"] == "three-way", "first update: the base shipped for the installed version is used")
ok(read("backend", "app.py") == b"new code" and read("frontend", "index.html") == b"new page" and read("VERSION").strip() == b"0.9.7 beta"
   and read("start_library.command") == b"new start" and read("scripts", "daily.bat") == b"platform script", "the code, the version and the start files are the update's")
ok(not os.path.exists(os.path.join(APP, "backend", "old_module.py")) and read("backend", "venv", "bin", "python") == b"the environment", "a module the update dropped is gone; the Python environment is untouched")
keep = [d for d in os.listdir(os.path.join(APP, "backups")) if d.startswith("before_update_")]
ok(len(keep) == 1 and open(os.path.join(APP, "backups", keep[0], "code", "backend", "app.py")).read() == "old code"
   and open(os.path.join(APP, "backups", keep[0], "library.db"), "rb").read()[:16] == b"SQLite format 3\x00", "the old code and the old database are kept under backups/")
ok(read("data", "llm_settings.json") == b'{"provider": "ollama_cloud"}', "the AI settings file is untouched")
ok(one(db3, "SELECT password_hash FROM staff_users WHERE username='noura'") == ("hash-of-the-library",) and one(db3, "SELECT summary FROM books WHERE book_id=?", (A_,)) == ("نبذة جديدة من التحديث",),
   "the installed database is the merged one: the account stays, the catalog is new")
m3 = one(db3, "SELECT book_id FROM books WHERE rewaa_sku='SKU-LIB-1'")[0]
ok(read("data", "covers", f"{A_}.jpg") == b"cover-a-v2" and read("data", "covers", f"{B_}.jpg") == b"uploaded-by-staff"
   and read("data", "covers", f"{m3}.jpg") == b"cover-of-the-library-book" and read("data", "covers", f"{top + 1}.jpg") == b"cover-new",
   "covers: an untouched one is updated, one the staff uploaded stays, the moved book keeps its picture, the new book gets its own")
ok(os.path.exists(os.path.join(APP, "backups", keep[0], "covers_replaced", f"{A_}.jpg")), "a replaced cover is kept under backups/")
ok(open(os.path.join(APP, "data", "catalog_base.db"), "rb").read() == open(NEW, "rb").read(), "the installed catalog is saved as the base for the next update")
text = open(res["report_file"], encoding="utf-8").read()
ok("Conflicts" in text and "نبذة التحديث للكتاب ج" in text and "hash-of-the-library" not in text, "the report for Ibrahim lists the conflict and holds no account data")
ok(not [f for f in os.listdir(os.path.join(APP, "data")) if "updating" in f], "no working copy is left behind")
again = U.apply(APP, UPD, port=PORT, say=lambda *_: None)
ok(again["base_source"] == "data/catalog_base.db" and not any(s.get("added") or s.get("rows_updated") or s.get("removed") for s in again["tables"].values()),
   "the next run uses the saved base, and the same update again changes nothing")

# ---- a broken update package leaves everything as it was
APP2 = make_app("app2", library("library4.db"))
bad = os.path.join(WORK, "bad-update"); shutil.copytree(UPD, bad)
open(os.path.join(bad, "catalog.db"), "wb").write(b"not a database")
b4 = open(os.path.join(APP2, "data", "library.db"), "rb").read()
try:
    U.apply(APP2, bad, port=PORT, say=lambda *_: None); failed = False
except Exception:
    failed = True
ok(failed and open(os.path.join(APP2, "data", "library.db"), "rb").read() == b4 and open(os.path.join(APP2, "backend", "app.py")).read() == "old code"
   and not [f for f in os.listdir(os.path.join(APP2, "data")) if "updating" in f], "a damaged update stops before anything is replaced")
try:
    U.apply(os.path.join(WORK, "covers0"), UPD, port=PORT, say=lambda *_: None); wrong = False
except U.UpdateError:
    wrong = True
ok(wrong, "a folder that is not the app is refused")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
