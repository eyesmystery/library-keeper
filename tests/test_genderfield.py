"""[GENDERFIELD] The author's gender as its own field (Ibrahim 2026-11-05 «سوي الحقل»).

authors.gender ('f' / 'm' / NULL) and authors.gender_source come with the schema migration; the chat reads the field
first and the style note's first word only as a fallback; the backfill fills it from today's audit rows and from the
notes; an update carries it and keeps a library-side value. Throwaway copies only."""
import importlib.util, json, os, sqlite3, sys, tempfile
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models
models.init_db()
import app as A
import authors as AU
AU.sync()   # the frozen test catalog has books only: the author records and links are made here
ROOT = os.path.dirname(os.getcwd())
sys.path.append(os.path.join(ROOT, "scripts"))
import apply_update as U
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

DB = models.DB_PATH
def cols(db, t="authors"):
    con = sqlite3.connect(db)
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
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
def one(db, sql, args=()):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()

# ---- 1. schema: the columns exist after init_db, also on a database from before them
ok({"gender", "gender_source"} <= set(cols(DB)), "init_db: authors.gender and authors.gender_source exist")
try:
    run(DB, "ALTER TABLE authors DROP COLUMN gender_source", "ALTER TABLE authors DROP COLUMN gender")
    dropped = "gender" not in cols(DB)
except sqlite3.OperationalError:
    dropped = False
models.init_db(); models.init_db()   # twice: idempotent
ok(dropped and {"gender", "gender_source"} <= set(cols(DB)), "an old database (no gender columns) gets them from init_db, twice without error")
_msrc = open(models.__file__, encoding="utf-8").read()
ok('_add_column(conn, "authors", "gender", "TEXT")' in _msrc and '_add_column(conn, "authors", "gender_source", "TEXT")' in _msrc,
   "the columns are added by _migrate_schema's _add_column (structural only)")

# ---- 2. reading: the field first, the note as a fallback
ok(A.author_gender("f", "كاتب مصري") == "f" and A.author_gender("m", "كاتبة سعودية") == "m", "author_gender: the field wins over the note")
ok(A.author_gender(None, "كاتبة سعودية") == "f" and A.author_gender("", "شاعر عراقي") == "m" and A.author_gender(None, "روايات عن الحب") is None,
   "author_gender: an empty field falls back to the note's first word")
ok(A.author_gender("x", "كاتبة سعودية") is None, "author_gender: any other value in the field is unknown, not read from the note")
solo = [r[0] for r in sqlite3.connect(DB).execute(
    "SELECT book_id FROM book_authors WHERE role='author' GROUP BY book_id HAVING COUNT(*)=1 ORDER BY book_id LIMIT 3")]
b1, b2, b3 = solo
aid = {b: one(DB, "SELECT author_id FROM book_authors WHERE book_id=? AND role='author'", (b,))[0] for b in solo}
run(DB, ("UPDATE authors SET gender='f', gender_source='https://www.wikidata.org/wiki/Q1', style_note_ar='روايات اجتماعية' WHERE author_id=?", (aid[b1],)),
    ("UPDATE authors SET gender=NULL, gender_source=NULL, style_note_ar='كاتبة؛ قصص قصيرة' WHERE author_id=?", (aid[b2],)),
    ("UPDATE authors SET gender='m', gender_source='style_note_ar', style_note_ar='كاتبة؛ نص قديم' WHERE author_id=?", (aid[b3],)))
A._gender_cache["map"] = None
g = A.book_author_gender()
ok(g.get(b1) == "f", "book_author_gender: the field gives a woman although the note says nothing (a rewritten note no longer loses it)")
ok(g.get(b2) == "f", "book_author_gender: no field -> the note «كاتبة؛ …» still counts")
ok(g.get(b3) == "m", "book_author_gender: the field wins over a note that says otherwise")
_asrc = open(A.__file__, encoding="utf-8").read()
ok("def women_award_counts" in _asrc and "g = book_author_gender()" in _asrc and "_g = book_author_gender()" in _asrc
   and _asrc.count("note_gender(r[") == 0, "every gender read (women's wish, women_award_counts) goes through book_author_gender")
d = AU.author_detail(aid[b1])
ok(d["gender"] == "f" and d["gender_source"] == "https://www.wikidata.org/wiki/Q1", "admin author detail carries the gender and its source")
_adm = open(os.path.join(ROOT, "admin", "index.html"), encoding="utf-8").read()
ok("a.gender_source" in _adm and "الجنس: " in _adm, "admin author sheet shows the gender read-only with its source (developer block)")

# merging two spellings of one person keeps the gender
run(DB, "INSERT INTO authors (name_ar) VALUES ('مؤلفة للدمج أ')",
    "INSERT INTO authors (name_ar, gender, gender_source, style_note_ar) VALUES ('مؤلفة للدمج ب', 'f', 'https://www.wikidata.org/wiki/Q2', 'كاتبة؛ شعر')")
keep_id = one(DB, "SELECT author_id FROM authors WHERE name_ar='مؤلفة للدمج أ'")[0]
drop_id = one(DB, "SELECT author_id FROM authors WHERE name_ar='مؤلفة للدمج ب'")[0]
AU.merge_authors(keep_id, drop_id)
ok(one(DB, "SELECT gender, gender_source FROM authors WHERE author_id=?", (keep_id,)) == ("f", "https://www.wikidata.org/wiki/Q2"),
   "merge_authors: the kept record takes the dropped one's gender and source when it has none")

# ---- 3. the backfill (research/genderfield_2026-11-05/genderfield_backfill.py) on a throwaway copy
WORK = tempfile.mkdtemp(prefix="gf-")
BF = os.path.join(WORK, "bf.db")
U.copy_db(DB, BF)
x1, x2, x3, x4 = [r[0] for r in sqlite3.connect(BF).execute("SELECT author_id FROM authors ORDER BY author_id LIMIT 4")]
run(BF, "UPDATE authors SET gender=NULL, gender_source=NULL",
    ("UPDATE authors SET style_note_ar='كاتبة؛ روايات' WHERE author_id=?", (x1,)),
    ("UPDATE authors SET style_note_ar='شاعر عراقي' WHERE author_id=?", (x2,)),
    ("UPDATE authors SET style_note_ar='روايات عن الحرب' WHERE author_id=?", (x3,)),
    ("UPDATE authors SET style_note_ar='كاتب؛ نص' WHERE author_id=?", (x4,)),
    ("INSERT INTO audit_log (at, username, action, target, details) VALUES ('t', 'MGR', 'author.gender_recorded', ?, ?)",
     (str(x1), json.dumps({"gender": "f", "source": "https://www.wikidata.org/wiki/Q10"}))),
    ("INSERT INTO audit_log (at, username, action, target, details) VALUES ('t', 'MGR', 'author.gender_recorded', ?, ?)",
     (str(x4), json.dumps({"gender": "f", "source": "https://www.wikidata.org/wiki/Q11"}))))
_bfp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research", "genderfield_2026-11-05", "genderfield_backfill.py")
_path0 = list(sys.path)
spec = importlib.util.spec_from_file_location("genderfield_backfill", _bfp)
BFM = importlib.util.module_from_spec(spec); spec.loader.exec_module(BFM)
sys.path[:] = _path0
before = one(BF, "SELECT COUNT(*) FROM authors WHERE gender IS NOT NULL")[0]
BFM.main(False, db=BF, report_dir=WORK)
ok(one(BF, "SELECT COUNT(*) FROM authors WHERE gender IS NOT NULL")[0] == before == 0, "backfill dry run: the database is not changed")
rep = BFM.main(True, db=BF, report_dir=WORK)
g1, g2, g3, g4 = (one(BF, "SELECT gender, gender_source, style_note_ar FROM authors WHERE author_id=?", (x,)) for x in (x1, x2, x3, x4))
ok(g1 == ("f", "https://www.wikidata.org/wiki/Q10", "كاتبة؛ روايات"), "backfill: today's audit row -> gender + its source; the note's prefix stays")
ok(g2[:2] == ("m", "style_note_ar"), "backfill: an older note «شاعر …» -> gender with «style_note_ar» as source")
ok(g3[:2] == (None, None), "backfill: a note without gender stays NULL")
ok(g4[:2] == (None, None) and any(s["author_id"] == x4 for s in rep["skipped"]), "backfill: a note that disagrees with the audit row is left out and listed")
ok(one(BF, "SELECT COUNT(*) FROM audit_log WHERE action='author.gender_field_set'")[0] == len(rep["set_from_today"]) + len(rep["set_from_note"]) > 0,
   "backfill: one audit row author.gender_field_set per author filled")
n_set = one(BF, "SELECT COUNT(*) FROM authors WHERE gender IS NOT NULL")[0]
rep2 = BFM.main(True, db=BF, report_dir=WORK)
ok(not rep2["set_from_today"] and not rep2["set_from_note"] and rep2["already_set"] == n_set, "backfill: a second run changes nothing")

# ---- 4. the update carries the field (scripts/apply_update.py) and keeps a library-side value
OLD = os.path.join(WORK, "old.db")      # the version before the field: no gender columns
U.copy_db(DB, OLD)
run(OLD, "ALTER TABLE authors DROP COLUMN gender_source", "ALTER TABLE authors DROP COLUMN gender")
ok("gender" not in cols(OLD), "update test: an old library database without the columns")
BASE = U.build_catalog(OLD, os.path.join(WORK, "base.db"), {"version": "old"}, {})
UP = os.path.join(WORK, "up.db")
U.copy_db(DB, UP)
y1, y2 = [r[0] for r in sqlite3.connect(UP).execute("SELECT author_id FROM authors ORDER BY author_id LIMIT 2")]
run(UP, ("UPDATE authors SET gender='f', gender_source='https://www.wikidata.org/wiki/Q20' WHERE author_id=?", (y1,)),
    ("UPDATE authors SET gender='m', gender_source='style_note_ar' WHERE author_id=?", (y2,)))
NEW = U.build_catalog(UP, os.path.join(WORK, "new.db"), {"version": "new"}, {})
ok({"gender", "gender_source"} <= set(cols(NEW)), "the update's catalog (build_catalog, used by make_release) has the columns")
LIB = os.path.join(WORK, "lib.db")
U.copy_db(OLD, LIB)
U.merge(LIB, NEW, BASE)
ok({"gender", "gender_source"} <= set(cols(LIB)), "merge: the library's authors table gets the columns")
ok(one(LIB, "SELECT gender, gender_source FROM authors WHERE author_id=?", (y1,)) == ("f", "https://www.wikidata.org/wiki/Q20")
   and one(LIB, "SELECT gender FROM authors WHERE author_id=?", (y2,)) == ("m",), "merge: the update's gender values are written")
LIB0 = os.path.join(WORK, "lib0.db")      # first update without a base
U.copy_db(OLD, LIB0)
U.merge(LIB0, NEW, None)
ok(one(LIB0, "SELECT gender FROM authors WHERE author_id=?", (y1,)) == ("f",), "merge without a base: the gender is written too")
# next update: the library changed y2 itself; the update changes y1 and leaves y2 as it was
run(LIB, ("UPDATE authors SET gender='f', gender_source='موظف المكتبة' WHERE author_id=?", (y2,)))
UP2 = os.path.join(WORK, "up2.db")
U.copy_db(UP, UP2)
run(UP2, ("UPDATE authors SET gender_source='https://www.wikidata.org/wiki/Q21' WHERE author_id=?", (y1,)))
NEW2 = U.build_catalog(UP2, os.path.join(WORK, "new2.db"), {"version": "new2"}, {})
U.merge(LIB, NEW2, NEW)
ok(one(LIB, "SELECT gender, gender_source FROM authors WHERE author_id=?", (y2,)) == ("f", "موظف المكتبة"),
   "merge: a gender the library changed itself is kept (not wiped by the update)")
ok(one(LIB, "SELECT gender_source FROM authors WHERE author_id=?", (y1,)) == ("https://www.wikidata.org/wiki/Q21",),
   "merge: a source the update changed is written")
# a later update that does not know the field any more (older catalog) never empties it
U.merge(LIB, BASE, NEW2)
ok(one(LIB, "SELECT gender FROM authors WHERE author_id=?", (y1,)) == ("f",), "merge from a catalog without the columns: the values stay")

print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
