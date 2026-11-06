"""BACKUPFIX (Codex audit 2026-11-02, P2): one picture that cannot be copied to the iCloud mirror
(errno 11 "Resource deadlock avoided" on covers/774.jpg) must not stop the rest of the folder.

Fault injection only, on throwaway folders under /tmp: never the real iCloud folder, data/ or backups/.
"""
import errno, importlib.util, os, shutil, sqlite3, stat, sys, tempfile, time

P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

spec = importlib.util.spec_from_file_location("backup_db", os.path.join(os.path.dirname(os.getcwd()), "scripts", "backup_db.py"))
bk = importlib.util.module_from_spec(spec); spec.loader.exec_module(bk)

BASE = tempfile.mkdtemp(prefix="lk-backupfix-", dir="/tmp" if os.path.isdir("/tmp") else None)
REAL_COPY, REAL_REPLACE = bk.copy_file, os.replace
sleeps = []
bk.time = type("T", (), {"sleep": staticmethod(lambda s: sleeps.append(s))})   # no real waiting in the tests
DEADLK = OSError(errno.EDEADLK, os.strerror(errno.EDEADLK))
DEADLK_TEXT = f"[Errno {errno.EDEADLK}] {os.strerror(errno.EDEADLK)}"

def folders(name, files):
    src, dst = os.path.join(BASE, name, "src"), os.path.join(BASE, name, "dst")
    os.makedirs(src); os.makedirs(dst)
    for fname, data in files.items():
        open(os.path.join(src, fname), "wb").write(data)
    return src, dst

def read(path):
    return open(path, "rb").read()

def failing(names, err=DEADLK, times=None):
    """copy_file that raises `err` for the given names (`times` times, or always), else copies."""
    calls = {}
    def fake(src, dst):
        n = os.path.basename(dst)
        calls[n] = calls.get(n, 0) + 1
        if n in names and (times is None or calls[n] <= times):
            raise OSError(err.errno, err.strerror, dst)
        return REAL_COPY(src, dst)
    return fake, calls

PICS = {f"{i}.jpg": b"\xff\xd8\xff" + bytes([i % 256]) * (i - 670) for i in range(770, 778)}

try:
    # 1. the normal rule is unchanged: new or changed files only, nothing deleted, .part skipped
    src, dst = folders("plain", dict(PICS, **{"9.png.part": b"half"}))
    open(os.path.join(dst, "old-only-in-mirror.jpg"), "wb").write(b"keep me")
    s = {}
    n = bk.copy_assets(src, dst, s)
    ok(n == 8 and s["copied"] == 8 and s["unchanged"] == 0 and s["failed"] == [], "first run copies every picture (8)")
    ok(all(read(os.path.join(dst, f)) == d for f, d in PICS.items()), "copies are byte-identical")
    ok(not os.path.exists(os.path.join(dst, "9.png.part")), "unfinished .part files are still skipped")
    ok(read(os.path.join(dst, "old-only-in-mirror.jpg")) == b"keep me", "a file only in the mirror is never deleted")
    ok(sorted(os.listdir(src)) == sorted(list(PICS) + ["9.png.part"]), "the source folder is untouched")
    ok(not [f for f in os.listdir(dst) if "lk-tmp" in f], "no temporary file is left behind")
    s = {}
    ok(bk.copy_assets(src, dst, s) == 0 and s["unchanged"] == 8, "second run: everything unchanged, nothing copied")
    ok(bk.asset_report("covers", s) == "OK covers (0 new or changed)", "all well -> the same OK line as before")
    t = time.time() + 5
    open(os.path.join(src, "772.jpg"), "wb").write(b"new cover"); os.utime(os.path.join(src, "772.jpg"), (t, t))
    s = {}
    ok(bk.copy_assets(src, dst, s) == 1 and read(os.path.join(dst, "772.jpg")) == b"new cover", "a changed picture is copied again")
    ok(abs(os.path.getmtime(os.path.join(dst, "772.jpg")) - t) < 1, "the copy keeps the source time (copy2), so it counts as unchanged next run")
    ok(bk.copy_assets(os.path.join(BASE, "no-such-folder"), dst) is None, "no source folder -> None, as before")
    ok(bk.copy_assets(src, dst) == 0, "summary is optional (old call style still works)")

    # 2. errno 11 once on one file: retried after a short wait, then everything is there
    src, dst = folders("once", PICS)
    bk.copy_file, calls = failing({"774.jpg"}, times=1)
    sleeps.clear(); s = {}
    n = bk.copy_assets(src, dst, s)
    ok(n == 8 and s["failed"] == [] and calls["774.jpg"] == 2, "EDEADLK once: the file is tried again and copied")
    ok(sleeps == [bk.RETRY_WAITS[0]] and 0 < bk.RETRY_WAITS[0] <= 2, "the retry waits a short moment first")
    ok(read(os.path.join(dst, "774.jpg")) == PICS["774.jpg"], "the retried copy is complete")
    ok(bk.asset_report("covers", s) == "OK covers (8 new or changed)", "a retry that worked -> plain OK line, no WARN")
    bk.copy_file = REAL_COPY

    # 3. errno 11 every time on one file: the others are still copied, one WARN line names it
    src, dst = folders("always", PICS)
    open(os.path.join(dst, "774.jpg"), "wb").write(b"older mirror copy")
    os.utime(os.path.join(dst, "774.jpg"), (1, 1))
    bk.copy_file, calls = failing({"774.jpg"})
    sleeps.clear(); s = {}
    n = bk.copy_assets(src, dst, s)
    ok(n == 7 and s["copied"] == 7, "EDEADLK on 774.jpg: the 7 other pictures are still copied (the folder no longer stops)")
    ok(all(read(os.path.join(dst, f)) == d for f, d in PICS.items() if f != "774.jpg"), "files after the bad one (775-777) are in the mirror")
    ok(calls["774.jpg"] == len(bk.RETRY_WAITS) + 1 and len(sleeps) == len(bk.RETRY_WAITS) and sum(sleeps) <= 5,
       "the bad file gets a few tries with short waits (not forever)")
    ok(s["failed"] == [("774.jpg", DEADLK_TEXT)], "the summary names the file and the error")
    ok(read(os.path.join(dst, "774.jpg")) == b"older mirror copy", "the older mirror copy of the bad file is left as it was (not deleted)")
    line = bk.asset_report("covers", s)
    ok(line.startswith("WARN covers incomplete: copied 7, unchanged 0, failed 1 (774.jpg: " + DEADLK_TEXT + ")")
       and "\n" not in line and "next run" in line, "one WARN line: counts + the file name + the error")
    bk.copy_file = REAL_COPY

    # 4. a lasting error (permission) is not retried; the rest is copied
    src, dst = folders("perm", PICS)
    bk.copy_file, calls = failing({"770.jpg"}, err=OSError(errno.EACCES, os.strerror(errno.EACCES)))
    sleeps.clear(); s = {}
    ok(bk.copy_assets(src, dst, s) == 7 and calls["770.jpg"] == 1 and sleeps == [],
       "a permission error is not retried; the other 7 pictures are copied")
    bk.copy_file = REAL_COPY

    # 5. many files failing (iCloud folder evicted): no endless waiting, the line stays short
    many = {f"{i}.jpg": b"x" * i for i in range(100, 112)}
    src, dst = folders("many", many)
    bk.copy_file, calls = failing(set(many))
    sleeps.clear(); s = {}
    bk.copy_assets(src, dst, s)
    ok(len(s["failed"]) == 12 and sum(sleeps) == bk.SLOW_RETRY_LIMIT * sum(bk.RETRY_WAITS),
       f"12 failing files: only the first {bk.SLOW_RETRY_LIMIT} get the slow retries (total wait {sum(sleeps)}s)")
    line = bk.asset_report("covers", s)
    ok("failed 12" in line and "+7 more" in line and line.count(".jpg:") == bk.SHOW_FAILED, "the WARN line lists the first 5 names and '+7 more'")
    bk.copy_file = REAL_COPY

    # 6. copy_file writes a temporary file, then swaps it in: the existing copy is never opened for writing
    src, dst = folders("replace", {"774.jpg": b"new picture"})
    target = os.path.join(dst, "774.jpg")
    open(target, "wb").write(b"placeholder"); os.chmod(target, stat.S_IRUSR | stat.S_IRGRP)
    before = os.stat(target).st_ino
    bk.copy_file(os.path.join(src, "774.jpg"), target)
    ok(read(target) == b"new picture" and os.stat(target).st_ino != before,
       "a read-only (placeholder-like) mirror file is replaced as a whole, not written into")
    ok(os.listdir(dst) == ["774.jpg"], "no temporary file is left after a good copy")
    os.replace = lambda a, b: (_ for _ in ()).throw(OSError(errno.EDEADLK, os.strerror(errno.EDEADLK), b))
    try:
        open(os.path.join(src, "774.jpg"), "wb").write(b"newest picture")
        try:
            bk.copy_file(os.path.join(src, "774.jpg"), target); raised = None
        except OSError as e:
            raised = e
    finally:
        os.replace = REAL_REPLACE
    ok(raised is not None and raised.errno == errno.EDEADLK, "a failing swap reports the error")
    ok(os.listdir(dst) == ["774.jpg"] and read(target) == b"new picture", "a failing swap leaves the old copy and no temporary file")
    ok(bk.error_text(OSError(errno.EDEADLK, os.strerror(errno.EDEADLK), "/very/long/path.jpg")) == DEADLK_TEXT,
       "error text is short (no long iCloud path)")

    # 7. the whole backup run on a throwaway project: backup_log.txt gets one WARN line, the rest is OK
    proj = os.path.join(BASE, "project"); mirror = os.path.join(BASE, "mirror-for-test")
    os.makedirs(os.path.join(proj, "data", "covers")); os.makedirs(os.path.join(proj, "data", "event_images"))
    con = sqlite3.connect(os.path.join(proj, "data", "library.db"))
    con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)"); con.executemany("INSERT INTO books VALUES (?)", [(1,), (2,)])
    con.commit(); con.close()
    for f, d in PICS.items():
        open(os.path.join(proj, "data", "covers", f), "wb").write(d)
    open(os.path.join(proj, "data", "event_images", "3.png"), "wb").write(b"\x89PNG event")
    bk.ROOT, bk.DB, bk.LOCAL = proj, os.path.join(proj, "data", "library.db"), os.path.join(proj, "backups")
    os.environ["LIBRARY_KEEPER_BACKUP_MIRROR"] = mirror
    ok(mirror.startswith(BASE) and "Mobile Documents" not in mirror, "the test mirror is a temporary folder, never iCloud")
    bk.copy_file, calls = failing({"774.jpg"})
    sys.argv = ["backup_db.py", "--reason", "backupfix-test"]
    rc = bk.main()
    bk.copy_file = REAL_COPY
    logtxt = open(os.path.join(proj, "backups", "backup_log.txt"), encoding="utf-8").read()
    warns = [l for l in logtxt.splitlines() if "WARN" in l]
    ok(rc == 0, "the backup still ends OK (exit code 0)")
    ok(len(warns) == 1 and "WARN covers incomplete: copied 7, unchanged 0, failed 1 (774.jpg: " + DEADLK_TEXT in warns[0],
       "backup_log.txt: exactly one WARN line for covers, naming 774.jpg")
    ok("WARN covers mirror failed" not in logtxt and "OK covers" not in logtxt, "no more 'covers mirror failed' (the folder did not stop) and no false OK")
    ok("OK local event_images (1 new or changed)" in logtxt and "OK event_images (1 new or changed)" in logtxt,
       "event pictures: the usual OK lines (local and mirror)")
    ok(sorted(os.listdir(os.path.join(mirror, "covers"))) == sorted(f for f in PICS if f != "774.jpg"),
       "mirror/covers holds every other cover, including the ones after 774.jpg")
    ok(len([f for f in os.listdir(mirror) if f.startswith("library-")]) == 1, "the database copy reached the mirror as before")
    # the next daily run: iCloud is done syncing, the missing cover is copied
    rc = bk.main()
    logtxt2 = open(os.path.join(proj, "backups", "backup_log.txt"), encoding="utf-8").read()[len(logtxt):]
    ok(rc == 0 and "OK covers (1 new or changed)" in logtxt2 and "WARN" not in logtxt2, "next run: the left-over cover is copied, log says OK")
    ok(read(os.path.join(mirror, "covers", "774.jpg")) == PICS["774.jpg"], "774.jpg is in the mirror now")
    ok(sorted(os.listdir(os.path.join(proj, "data", "covers"))) == sorted(PICS), "the source covers are untouched")
finally:
    bk.copy_file = REAL_COPY
    os.replace = REAL_REPLACE
    os.environ.pop("LIBRARY_KEEPER_BACKUP_MIRROR", None)
    shutil.rmtree(BASE, ignore_errors=True)

print(f"TOTAL PASS={P} FAIL={F}")
sys.exit(1 if F else 0)
