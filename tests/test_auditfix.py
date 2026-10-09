"""Fixes for the daily audit of 2026-10-08 (track AUDITFIX, 2026-10-09).

1 backup gate of research/translators_2026-10-03/safe_apply.sh (stub commands in a temporary folder;
  no import is run), 2 stale meaning-search vectors (no Ollama: the embedding call is replaced),
3 catalog snapshot with the new tables, 4 event pictures in backup and restore, 5-6 visitor page
(static checks; the browser checks are in research/audit_fixes_2026-10-09/ui_check.py),
7 event and corrections-file input, 8 release file name.
"""
import os, sys, json, sqlite3, struct, zlib, array, shutil, socket, subprocess, tempfile, time, datetime
for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LIBRARY_KEEPER_PROVIDER", "LIBRARY_KEEPER_MODEL"]:
    os.environ.pop(k, None)
os.environ["LIBRARY_KEEPER_LLM"] = "off"
sys.path.insert(0, os.getcwd())
TMP_ROOT = os.path.dirname(os.getcwd())                                   # the throwaway copy made by run_all.py
REAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))    # the project (read only here)
sys.path.append(os.path.join(TMP_ROOT, "scripts"))   # after backend/: scripts/confirmations.py must not hide backend/confirmations.py
import models, events, embeddings, confirmations, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

def png(w=200, h=120, seed=7):
    raw = b"".join(b"\x00" + bytes([(x * seed + y) % 256 for x in range(w * 3)]) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

def status(fn):
    """HTTP status of a request; an exception inside the app counts as 500."""
    try:
        return fn().status_code
    except Exception as e:  # noqa: BLE001
        print("      exception:", type(e).__name__, str(e)[:120])
        return 500

assert os.path.realpath(TMP_ROOT) != os.path.realpath(REAL_ROOT), "this suite must run through tests/run_all.py"
models.init_db()
DBP = os.path.abspath(models.DB_PATH)
def db():
    return sqlite3.connect(DBP)

# =====================================================================================================
# 1. Backup gate before the translators import (P1)
# =====================================================================================================
SCRIPT = os.path.join(REAL_ROOT, "research", "translators_2026-10-03", "safe_apply.sh")
if os.path.exists(SCRIPT) and shutil.which("sh") and shutil.which("sqlite3"):
    STUB = r'''#!/bin/sh
# stands in for backend/venv/bin/python: records the call, fails when told to
echo "$*" >> calls.log
case "$*" in
  *backup_db.py*)
    i=0; while [ $i -lt 400 ]; do echo "backup output line $i"; i=$((i+1)); done
    [ "${FAIL_BACKUP:-}" = 1 ] && { echo "FAIL local backup"; exit 1; }
    [ "${BACKUP_NOFILE:-}" = 1 ] && { echo "SKIP no database"; exit 0; }
    if [ "${BACKUP_CORRUPT:-}" = 1 ]; then echo "this is not a database" > backups/library-99990101-000000.db
    else sqlite3 backups/library-99990101-000000.db "create table books(translator text); insert into books values('x');"; fi
    exit 0;;
  *apply.py*--apply*) [ "${FAIL_APPLY:-}" = 1 ] && { echo "apply crashed"; exit 4; }; echo "APPLIED translators written: 1"; exit 0;;
  *apply.py*) [ "${FAIL_DRY:-}" = 1 ] && { echo "dry run crashed"; exit 3; }; echo "DRY RUN translators written: 1"; exit 0;;
  *build_authors.py*) [ "${FAIL_LINKS:-}" = 1 ] && { echo "links crashed"; exit 5; }; echo "links ok"; exit 0;;
esac
exit 0
'''
    def gate(env_extra=None, args=("reason", "a.json"), live_db=True):
        box = tempfile.mkdtemp(prefix="lk-gate-")
        try:
            os.makedirs(os.path.join(box, "backend", "venv", "bin")); os.makedirs(os.path.join(box, "backups")); os.makedirs(os.path.join(box, "data"))
            stub = os.path.join(box, "backend", "venv", "bin", "python")
            open(stub, "w").write(STUB); os.chmod(stub, 0o755)
            old = os.path.join(box, "backups", "library-20000101-000000.db")
            c = sqlite3.connect(old); c.execute("create table books(translator text)"); c.commit(); c.close()
            os.utime(old, (time.time() - 3600, time.time() - 3600))   # an hour old: rule 4 lets the import start
            if live_db:
                c = sqlite3.connect(os.path.join(box, "data", "library.db")); c.execute("create table books(translator text)")
                c.execute("insert into books values('مترجم')"); c.commit(); c.close()
            else:
                open(os.path.join(box, "data", "library.db"), "w").write("broken")
            env = dict(os.environ, **(env_extra or {}))
            p = subprocess.run(["sh", SCRIPT, *args], cwd=box, env=env, capture_output=True, text=True, timeout=60)
            log = os.path.join(box, "calls.log")
            calls = open(log).read().splitlines() if os.path.exists(log) else []
            return p.returncode, calls, p.stdout + p.stderr
        finally:
            shutil.rmtree(box, ignore_errors=True)
    applied = lambda calls: any("--apply" in c and "apply.py" in c for c in calls)
    rc, calls, out = gate()
    order = ["dry" if ("apply.py" in c and "--apply" not in c) else "backup" if "backup_db.py" in c else "apply" if "apply.py" in c else "links" for c in calls]
    ok(rc == 0 and order == ["dry", "backup", "apply", "links"] and "backup verified" in out, "gate: all steps pass -> dry run, backup, apply, author links, exit 0")
    ok(out.count("backup output line") == 2, "gate: only the first lines of a step are shown, without a pipe")
    rc, calls, out = gate({"FAIL_BACKUP": "1"})
    ok(rc != 0 and not applied(calls) and "STOPPED" in out, "gate: backup exits 1 (with long output) -> apply is NOT reached, exit is not 0")
    rc, calls, out = gate({"BACKUP_NOFILE": "1"})
    ok(rc != 0 and not applied(calls) and "no new file" in out, "gate: backup says OK but wrote no new file -> apply is NOT reached")
    rc, calls, out = gate({"BACKUP_CORRUPT": "1"})
    ok(rc != 0 and not applied(calls) and "integrity" in out, "gate: new backup file is not a sound database -> apply is NOT reached")
    rc, calls, out = gate({"FAIL_DRY": "1"})
    ok(rc == 3 and len(calls) == 1, "gate: dry run fails -> no backup, no apply, the step's own exit code")
    rc, calls, out = gate({"FAIL_APPLY": "1"})
    ok(rc == 4 and not any("build_authors" in c for c in calls), "gate: apply fails -> author links are not run, exit 4")
    rc, calls, out = gate({"FAIL_LINKS": "1"})
    ok(rc == 5, "gate: author-links step fails -> exit 5 (it used to be hidden by `head`)")
    rc, calls, out = gate(live_db=False)
    ok(rc != 0, "gate: integrity check of the database after the import fails -> exit is not 0")
    rc, calls, out = gate(args=("reason-only",))
    ok(rc == 2 and calls == [], "gate: called without files -> usage, nothing run")
    text = open(SCRIPT, encoding="utf-8").read()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    ok("set -eu" in code and "| head" not in code and "data/library.db" in code, "gate: the script has set -eu and no step piped into head")
else:
    ok(True, "gate: skipped (no sh / sqlite3 / script on this computer)")

# =====================================================================================================
# 2. Meaning-search vectors follow the book's text (P2).  No Ollama: embeddings.embed is replaced.
# =====================================================================================================
def no_network(*a, **k):
    raise AssertionError("the test must not call Ollama")
embeddings.urllib.request.urlopen = no_network
con = db()
ids = [r[0] for r in con.execute("select book_id from books where active=1 and quantity>0 and research_status='matched' order by book_id limit 8")]
if len(ids) < 4:
    ids = [r[0] for r in con.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 8")]
b1, b2, b3, b4 = ids[:4]
MODEL = embeddings.model_name()
def book(bid):
    c = db(); c.row_factory = sqlite3.Row
    try:
        return dict(c.execute("select * from books where book_id=?", (bid,)).fetchone())
    finally:
        c.close()
def store(bid, vec, h=None):
    """A vector as scripts/build_embeddings.py writes it: with the hash of the book's text now."""
    c = db()
    h = h or embeddings.text_hash(embeddings.book_text(book(bid)))
    c.execute("insert or replace into book_embeddings values (?,?,?,?,?,?)", (bid, MODEL, h, len(vec), array.array("f", vec).tobytes(), "x"))
    c.commit(); c.close()
con.execute("update books set summary='رواية عن الغربة والمنفى والحنين إلى الوطن', hook='غريب في مدينة بعيدة' where book_id=?", (b1,))
con.execute("update books set summary='كتاب في الطبخ وأطباق المطبخ الشامي', hook='وصفات البيت' where book_id=?", (b2,))
con.execute("update books set summary='رواية عن المنفى الطويل', hook='بعيدًا عن البيت' where book_id=?", (b3,))
con.execute("update books set summary='سيرة مهاجر', hook='حقيبة واحدة' where book_id=?", (b4,))
con.commit(); con.close()
store(b1, [1.0, 0.0]); store(b2, [0.0, 1.0]); store(b3, [0.98, 0.199]); store(b4, [0.97, 0.243])
embeddings.embed = lambda texts, timeout=8.0: [array.array("f", [1.0, 0.0]) for _ in texts]   # the visitor asks about «الغربة»
embeddings.invalidate()
found = lambda: [i for i, _ in embeddings.search(["الغربة"], ids).get("الغربة", [])]
ok(found() == [b1, b3, b4], "vectors whose hash matches the book's text are used (closest first, the cookbook is not)")

# --- identity correction through the manager's corrections file, with the cache still warm
m = login(A.app.test_client(), "manager")
bk = book(b1)
package = {"format": "confirmation-corrections-1", "items": [
    {"book_id": b1, "expect": {"title": bk["title"], "author": bk["author"]}, "set": {"title": "أطلس الطيور المهاجرة"},
     "clear_summary": True, "note": "the record named another work"}]}
r = m.post("/api/admin/confirmations/corrections", json={"package": package, "apply": True})
ok(r.status_code == 200 and book(b1)["title"] == "أطلس الطيور المهاجرة" and not book(b1)["summary"], "identity correction applied (title changed, old summary cleared)")
ok(embeddings._cache["vecs"] is not None and b1 not in found(), "identity correction: the old work's vector no longer answers, even inside the five-minute cache")
ok(found() == [b3, b4], "the other books are still found")

# --- a rewritten summary (any writer: here plain SQL, as the import scripts do), warm cache and cold cache
con = db(); con.execute("update books set summary='دليل عملي لزراعة الحدائق المنزلية' where book_id=?", (b3,)); con.commit(); con.close()
ok(found() == [b4], "summary rewritten: the vector of the old text is not used (warm cache)")
embeddings.invalidate()
ok(set(embeddings.load()) == {b2, b4}, "load() skips every vector whose text_hash is not the book's current text")
con = db()
ok(embeddings.stale_ids(con) == sorted([b1, b3]), "stale_ids() names the two changed books")
con.close()
con = db(); con.execute("update books set title = title || ' (طبعة جديدة)' where book_id=?", (b4,)); con.commit(); con.close()
ok(found() == [], "title corrected alone (no summary change): that vector is dropped too")

# --- the approved rebuild path writes fresh vectors; nothing else changes them
import build_embeddings
calls = []
def fake_embed(texts, timeout=20.0):
    calls.append(list(texts))
    return [array.array("f", [1.0, 0.0]) for _ in texts]
embeddings.embed = fake_embed
con = db(); con.execute("delete from book_embeddings where book_id not in (?,?,?,?)", (b1, b2, b3, b4)); con.commit()
con.execute("update books set summary=NULL, hook=NULL, subjects=NULL where book_id not in (?,?,?,?)", (b1, b2, b3, b4)); con.commit(); con.close()
argv, sys.argv = sys.argv, ["build_embeddings.py"]
try:
    build_embeddings.main()
finally:
    sys.argv = argv
embedded = [t for batch in calls for t in batch]
con = db()
left = {r[0] for r in con.execute("select book_id from book_embeddings where model=?", (MODEL,))}
ok(len(embedded) == 2 and left == {b2, b3, b4} and embeddings.stale_ids(con) == [],
   "build_embeddings: re-embeds only the two books whose text changed and removes the vector of the book left without a text")
con.close()
embeddings.embed = lambda texts, timeout=8.0: [array.array("f", [1.0, 0.0]) for _ in texts]
embeddings.invalidate()
ok(found() == [b3, b4], "after the rebuild the corrected books are searchable again by their new text")
ok(embeddings.search(["الغربة"], [b1]) == {}, "a book without a vector is simply not in the meaning search")

# =====================================================================================================
# 7. Event input and the manager's corrections file (P3)
# =====================================================================================================
s = login(A.app.test_client(), "staff")
day = events.today()
base = {"title": "أمسية قراءة", "event_date": day}
r = s.post("/api/admin/events", json=dict(base, event_time="21:00", end_time="19:00"))
ok(r.status_code == 400 and "النهاية" in r.get_json()["error"] and "البداية" in r.get_json()["error"],
   "event: end before the start on the same day is refused with an Arabic reason")
ok(s.post("/api/admin/events", json=dict(base, event_time="19:00", end_time="19:00")).status_code == 400, "event: end equal to the start is refused")
r = s.post("/api/admin/events", json=dict(base, event_time="19:30", end_time="21:00", book_id=str(b2)))
ok(r.status_code == 200, "event: a normal evening with a book (id sent as text) is accepted")
eid = r.get_json()["event_id"]
ok(s.post("/api/admin/events", json=dict(base, event_time="09:05")).status_code == 200, "event: a start time without an end is accepted")
ok(s.put(f"/api/admin/events/{eid}", json=dict(base, event_time="21:00", end_time="20:59")).status_code == 400
   and events.get(eid)["end_time"] == "21:00", "event: an edit that puts the end before the start is refused, the event is unchanged")
bad_ids = [2 ** 70, -1, 0, "1180591620717411303424", "²", "١٢", "5 or 1=1", True, [b2], {"id": b2}, 1.5, "1e3"]
codes = [status(lambda v=v: s.post("/api/admin/events", json=dict(base, book_id=v))) for v in bad_ids]
ok(codes == [400] * len(bad_ids), f"event: a book number that is huge, negative, not a number or another type answers 400, never 500 ({codes})")
ok(status(lambda: s.put(f"/api/admin/events/{2 ** 70}", json=base)) == 404 and status(lambda: s.delete(f"/api/admin/events/{2 ** 70}")) == 404,
   "event: a huge event number in the address is 'not found', not a server error")
ok(events.safe_id("42") == 42 and events.safe_id(2 ** 63 - 1) == 2 ** 63 - 1 and events.safe_id(2 ** 63) is None and events.safe_id(None) is None,
   "safe_id keeps numbers inside SQLite's range")
ok(A.app.test_client().post("/api/admin/events", json=base).status_code == 401, "event: writing still needs sign-in")

def corr(body):
    return m.post("/api/admin/confirmations/corrections", json=body)
ok(status(lambda: corr([1, 2, 3])) == 400 and status(lambda: corr("x")) == 400, "corrections file: a body that is a list or a bare value answers 400")
item = lambda **kw: dict({"book_id": b2, "expect": {"title": book(b2)["title"], "author": book(b2)["author"]}, "set": {}}, **kw)
pk = lambda *items: {"package": {"format": "confirmation-corrections-1", "items": list(items)}}
r = corr(pk(item(book_id=2 ** 70)))
ok(r.status_code == 200 and r.get_json()["ok"] is False and r.get_json()["items"][0]["problem"] == "رقم الكتاب غير صالح",
   "corrections file: a book number beyond SQLite's range is a refused item in the preview, not a server error")
r = corr(pk(item(book_id=-5), item(confirmation_id=2 ** 70)))
ok(r.status_code == 200 and all(i["problem"] for i in r.get_json()["items"]), "corrections file: negative book number and huge answer number are refused items")
ok(status(lambda: corr(dict(pk(item(book_id=2 ** 70)), apply=True))) == 400, "corrections file: applying such a file answers 400 and writes nothing")

# =====================================================================================================
# 3. Catalog snapshot carries prizes, adaptations and events (P2)
# =====================================================================================================
import catalog_snapshot as cs
con = db()
aid = con.execute("insert into authors (name_ar, research_status, created_at) values ('كاتب اختبار اللقطة','pending','x')").lastrowid
con.execute("insert into book_awards (book_id, award_key, award_name, result, year, source, source_url) values (?,?,?,?,?,?,?)",
            (b2, "booker_ar", "الجائزة العالمية للرواية العربية", "shortlist", 2019, "official", "https://example.org/a"))
con.execute("insert into author_awards (author_id, award_key, award_name, result, year, source) values (?,?,?,?,?,?)",
            (aid, "nobel", "نوبل للآداب", "winner", 1988, "official"))
con.execute("insert into book_adaptations (book_id, kind, title, year, source) values (?,?,?,?,?)", (b2, "film", "فيلم «اختبار'ي»", 2001, "wikidata"))
con.commit(); con.close()
ok(cs.main("export") == 0, "snapshot: export runs")
snap = open(cs.SNAP, encoding="utf-8").read()
ok(all(f"INSERT INTO {t} " in snap for t in ("book_awards", "author_awards", "book_adaptations", "events")),
   "snapshot: prizes of books and authors, adaptations and events are exported with their rows")
ok("book_embeddings" not in snap and all(t not in snap for t in ("staff_users", "audit_log", "visitor_notes", "staff_notes", "book_choices", "open_questions")),
   "snapshot: vectors (derived, large) and visitor / staff activity stay out")
ok(cs.main("verify") == 0 and cs.main("check") == 0, "snapshot: verify rebuilds it and finds the same schema, row counts and content in every table")
con = db(); con.execute("insert into book_awards (book_id, award_key, result, source) values (?,?,?,?)", (b3, "x", "winner", "staff")); con.commit(); con.close()
src, built = sqlite3.connect(DBP), cs.rebuild(snap)
lines, problems = cs.compare(src, built)
src.close(); built.close()
ok(cs.main("verify") == 1 and len(problems) == 1 and problems[0].startswith("book_awards: rows"), "snapshot: one prize added after the export -> verify fails and names the table and the two counts")
con = db(); con.execute("update book_adaptations set title='فيلم آخر' where book_id=?", (b2,)); con.execute("delete from book_awards where award_key='x'"); con.commit(); con.close()
src, built = sqlite3.connect(DBP), cs.rebuild(snap)
problems = cs.compare(src, built)[1]
src.close(); built.close()
ok(problems == ["book_adaptations: same number of rows, different content"], "snapshot: same counts but a changed row is caught too")
ok(cs.main("export") == 0 and cs.main("verify") == 0, "snapshot: export again -> verify passes")
keep = DBP + ".kept-by-test"
os.replace(DBP, keep)
try:
    ok(cs.main("build") == 0 and os.path.exists(DBP), "snapshot: build makes a database from the text file alone")
    a_, b_ = sqlite3.connect(keep), sqlite3.connect(DBP)
    fa, fb = cs.table_facts(a_), cs.table_facts(b_)
    a_.close(); b_.close()
    ok(set(fa) == {t for t, _ in cs.TABLES} and fa == fb,
       "snapshot: the built database has all eleven tables with the same schema, row counts and content as the source")
finally:
    os.replace(keep, DBP)

# =====================================================================================================
# 4. Event pictures in the backup and in an isolated restore (P2)
# =====================================================================================================
MIRROR = os.path.join(TMP_ROOT, "mirror-for-test")      # never the real iCloud folder
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); FREE_PORT = str(sock.getsockname()[1]); sock.close()
def script(name, *args, mirror=MIRROR):
    env = dict(os.environ, LIBRARY_KEEPER_BACKUP_MIRROR=mirror, LIBRARY_KEEPER_PORT=FREE_PORT)
    p = subprocess.run([sys.executable, os.path.join(TMP_ROOT, "scripts", name), *args], env=env, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr
picture = png(600, 300, seed=11)
r = s.post("/api/admin/events", json={"title": "أمسية بصورتها", "event_date": day, "event_time": "19:30", "end_time": "21:00"})
ev_id = r.get_json()["event_id"]
import io
r = s.post(f"/api/admin/events/{ev_id}/image", data={"file": (io.BytesIO(picture), "poster.png")}, content_type="multipart/form-data")
ok(r.status_code == 200 and events.has_image(events.get(ev_id)), "an event with a picture exists")
IMG = os.path.join(TMP_ROOT, "data", "event_images", f"{ev_id}.png")
COVERS = os.path.join(TMP_ROOT, "data", "covers")
os.makedirs(COVERS, exist_ok=True)
open(os.path.join(COVERS, "777001.jpg"), "wb").write(b"\xff\xd8\xff" + b"c" * 2000)
open(os.path.join(TMP_ROOT, "data", "event_images", "9.png.part"), "wb").write(b"half written")
rc, out = script("backup_db.py", "--reason", "auditfix-test")
ok(rc == 0 and "OK local event_images (1 new or changed)" in out and "OK event_images (1 new or changed)" in out and "OK covers" in out,
   "backup: event pictures are copied beside the database backup and to the mirror (covers as before)")
ok(open(os.path.join(MIRROR, "event_images", f"{ev_id}.png"), "rb").read() == picture
   and open(os.path.join(TMP_ROOT, "backups", "event_images", f"{ev_id}.png"), "rb").read() == picture
   and not os.path.exists(os.path.join(MIRROR, "event_images", "9.png.part")), "backup: the copies are byte-identical; unfinished uploads are skipped")
rc, out = script("backup_db.py", "--reason", "again")
ok("OK event_images (0 new or changed)" in out, "backup: an unchanged picture is not copied twice")

# the application folder is lost: no database, no pictures
os.remove(DBP)
shutil.rmtree(os.path.join(TMP_ROOT, "data", "event_images")); shutil.rmtree(COVERS)
rc, out = script("restore_db.py", "--latest")
ok(rc == 0 and "pictures restored: covers 1, event_images 1" in out and "WARNING" not in out, "restore: database and picture files come back in one step")
ev = events.get(ev_id)
ok(ev is not None and ev["title"] == "أمسية بصورتها" and os.path.exists(IMG) and open(IMG, "rb").read() == picture and events.has_image(ev),
   "restore: the event is there and its picture is the same file")
r = A.app.test_client().get(f"/api/events")
shown = [e for e in r.get_json()["events"] if e["event_id"] == ev_id]
ok(shown and shown[0]["image_url"] and A.app.test_client().get(shown[0]["image_url"]).data == picture, "restore: the visitor page gets the event with its picture")

# no mirror at all (the library's computer without OneDrive): the copy in backups/ is enough
os.remove(IMG)
rc, out = script("restore_db.py", "--assets-only", mirror="")
ok(rc == 0 and open(IMG, "rb").read() == picture, "restore --assets-only: works from backups/event_images when there is no mirror")
open(IMG, "wb").write(b"\x89PNG\r\n\x1a\n" + b"newer picture uploaded after the backup" * 60)
rc, out = script("restore_db.py", "--assets-only")
ok(open(IMG, "rb").read() != picture, "restore: a picture that exists is never replaced by an older copy")
os.remove(IMG); shutil.rmtree(os.path.join(TMP_ROOT, "backups", "event_images")); shutil.rmtree(os.path.join(MIRROR, "event_images"))
rc, out = script("restore_db.py", "--assets-only")
ok(rc == 0 and "WARNING 1 picture(s)" in out and f"{ev_id}.png" in out, "restore: a picture no copy holds is reported by name, not passed over in silence")
rc, out = script("restore_db.py", "--latest", "--no-assets")
ok(rc == 0 and "pictures restored" not in out, "restore --no-assets: the database alone, as before")

# =====================================================================================================
# 8. Release file name carries the build time and the Git commit (P3)
# =====================================================================================================
import make_release as mr
when = datetime.datetime(2026, 10, 9, 10, 5)
ok(mr.release_name("0.9.5 beta", "windows", when, "84e0687", False) == "ThalathSmart-0.9.5b-20261009-1005-84e0687-windows.zip"
   and mr.release_name("0.9.5 beta", "mac", when, "84e0687", True) == "ThalathSmart-0.9.5b-20261009-1005-84e0687-dirty-mac.zip",
   "release: the zip name has the version, the Riyadh build time, the commit and a mark for uncommitted code")
info = mr.build_info("0.9.5 beta", when, "84e0687", True)
ok(info == {"version": "0.9.5 beta", "built_at_riyadh": "2026-10-09 10:05", "git_commit": "84e0687", "uncommitted_code_changes": True},
   "release: BUILD.json states the same facts inside the package")
ref, dirty = mr.git_ref(TMP_ROOT)
ok(isinstance(ref, str) and ref and isinstance(dirty, bool), f"release: a folder without Git gives a name too ({ref})")
if shutil.which("git") and os.path.isdir(os.path.join(REAL_ROOT, ".git")):
    ref, dirty = mr.git_ref(REAL_ROOT)
    ok(len(ref) == 7 and all(c in "0123456789abcdef" for c in ref), "release: in the project the commit is the short Git hash")
ok(abs((mr.riyadh_now().replace(tzinfo=None) - datetime.datetime.utcnow()).total_seconds() - 3 * 3600) < 5, "release: the build time is Riyadh time (UTC+3)")
ok(not os.path.exists(os.path.join(REAL_ROOT, "release", "_stage")), "no release was built by this test")

# =====================================================================================================
# 5-6. Visitor page: static checks (the measured checks run in a real browser, see ui_check.py)
# =====================================================================================================
css = open(os.path.join(TMP_ROOT, "frontend", "styles.css"), encoding="utf-8").read()
rule = css[css.index(".context-clear{"):].split("}", 1)[0]
ok("width:44px" in rule and "height:44px" in rule and ".context-clear:before{" in css and "inset:9px" in css,
   "✕ of a «نبحث عن» pill: the button is 44×44, the visible circle stays 26px")
js = open(os.path.join(TMP_ROOT, "frontend", "app.js"), encoding="utf-8").read()
html = open(os.path.join(TMP_ROOT, "frontend", "index.html"), encoding="utf-8").read()
ok(html.count('aria-modal="true"') == html.count('role="dialog"') >= 2 and "'[role=\"dialog\"][aria-modal=\"true\"]'" in js
   and ".inert = " in js and "'modal-close'" in js and js.count("addEventListener('modal-close'") == html.count('aria-modal="true"'),
   "every aria-modal sheet is covered by the one focus trap / inert / Escape handler and has a close listener")
print(f"TOTAL PASS={P} FAIL={F}")
