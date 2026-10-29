"""[CHANFIX] The library's «موافقة وتحديث» on Windows stopped with «[WinError 5] Access is denied:
'…channel_state.json.tmp' -> '…channel_state.json'» (Ibrahim's screenshot, 2026-10-27): Windows refuses to replace a file
another handle has open. Here the Windows behaviour is played by an os.replace / open that fail while «busy»."""
import builtins, json, os, sys, tempfile, threading
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import safe_io, channel, inbox_send, llm
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

tmpdir = tempfile.mkdtemp(prefix="chanfix-")
target = os.path.join(tmpdir, "channel_state.json")
real_replace, real_open = os.replace, builtins.open

# ---- a replace refused while the target is busy, then allowed
busy = {"n": 3}
def windows_replace(src, dst):
    if busy["n"] > 0:
        busy["n"] -= 1
        raise PermissionError(5, "Access is denied", src)
    return real_replace(src, dst)
os.replace = windows_replace
try:
    safe_io.write_json(target, {"update": {"progress": 40}})
    ok(json.load(real_open(target, encoding="utf-8")) == {"update": {"progress": 40}} and busy["n"] == 0,
       "a replace refused three times (Windows, file open elsewhere) still writes the file")
    busy["n"] = 10 ** 6
    try:
        safe_io.replace(target, target + ".x", tries=3, wait=0.01)
        ok(False, "a file busy for good: the error is raised in the end")
    except PermissionError:
        ok(True, "a file busy for good: the error is raised in the end (not swallowed)")
finally:
    os.replace = real_replace
ok(not [f for f in os.listdir(tmpdir) if f.endswith(".tmp")], "no temporary file is left behind")

# ---- two writers never share a temporary name
names = []
real_mkstemp = tempfile.mkstemp
def spy(*a, **k):
    fd, p = real_mkstemp(*a, **k); names.append(p); return fd, p
tempfile.mkstemp = spy
try:
    ts = [threading.Thread(target=safe_io.write_json, args=(target, {"i": i})) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
finally:
    tempfile.mkstemp = real_mkstemp
ok(len(set(names)) == 8 and json.load(real_open(target, encoding="utf-8"))["i"] in range(8),
   "eight writers at once: eight temporary files, one whole JSON in the end")

# ---- a read that meets a busy file waits instead of returning «nothing»
safe_io.write_json(target, {"update": {"version": "0.9.11 beta"}})
left = {"n": 2}
def windows_open(path, *a, **k):
    if path == target and left["n"] > 0:
        left["n"] -= 1
        raise PermissionError(13, "Permission denied", path)
    return real_open(path, *a, **k)
builtins.open = windows_open
try:
    got = safe_io.read_json(target, {})
finally:
    builtins.open = real_open
ok(got == {"update": {"version": "0.9.11 beta"}}, "a busy file is read again, not taken as an empty state")
ok(safe_io.read_json(os.path.join(tmpdir, "missing.json"), {"d": 1}) == {"d": 1}, "a missing file gives the default at once")

# ---- the channel state, the inbox state and the model settings go through it
src = {m: open(m.__file__, encoding="utf-8").read() for m in (channel, inbox_send, llm)}
ok("safe_io.write_json(path, data)" in src[channel] and "safe_io.read_json(path, default)" in src[channel],
   "channel_state.json is written and read the Windows-safe way")
ok("safe_io.write_json(path, data, private=private)" in src[inbox_send], "inbox_send's state and token too")
ok(src[llm].count("safe_io.write_json(") == 2, "the model settings and the saved keys too")
_au = open(os.path.join(os.path.dirname(channel.__file__), "..", "scripts", "auto_update.py"), encoding="utf-8").read()
ok("except PermissionError" in _au and "os.getpid()" in _au, "auto_update.py (outside the app) waits on a busy file too")

# ---- the download writes its progress every 5 %, not at every percent
writes = []
real_set = channel._set_update
def count_set(**changes):
    if "progress" in changes:
        writes.append(changes["progress"])
    return real_set(**changes)
channel._set_update = count_set
def fake_download(cfg, up, progress):
    for got in range(0, 1001):
        progress(got, 1000)
    raise channel.ChannelError("stop here (test)")
try:
    channel._install("test", download=fake_download, exit_after=False)
except Exception:  # noqa: BLE001
    pass
finally:
    channel._set_update = real_set
ok(1 <= len(writes) <= 22 and 100 in writes, f"the progress is written every 5 % ({len(writes)} writes, was ~100)")

# ---- [CHANFIX2, 2026-10-29] a state file busy for good never stops the install (the library hit WinError 5 again)
import zipfile, time as _time
channel.UPDATES = os.path.join(tmpdir, "updates")
zsrc = os.path.join(tmpdir, "upd.zip")
with zipfile.ZipFile(zsrc, "w") as z:
    z.writestr("upd/apply_update.py", "print('ok')\n")
    z.writestr("upd/payload/VERSION", "9.9.9 beta\n")
channel._set_update(status="available", version="9.9.9 beta", progress=0)
def stubborn_set(**changes):
    if "progress" in changes or changes.get("status") in ("checking", "ready", "restarting"):
        raise PermissionError(5, "Access is denied", "channel_state.json")
    return real_set(**changes)
def zip_download(cfg, up, progress):
    for got in range(0, 101):
        progress(got, 100)
    return zsrc
channel._set_update = stubborn_set
try:
    channel._install("test", download=zip_download, dry_run=lambda upd: (0, "dry run ok"), exit_after=False)
finally:
    channel._set_update = real_set
pend = safe_io.read_json(channel.PENDING, {})
ok(pend.get("version") == "9.9.9 beta" and os.path.isdir(pend.get("update_dir") or ""),
   "progress and status notes refused for good: the download still finishes and the update is queued")
ok((channel.state().get("update") or {}).get("status") != "failed", "… and the install is not marked failed")
if os.path.exists(channel.PENDING):
    os.remove(channel.PENDING)

left2 = {"n": 2}
def failed_busy(**changes):
    if changes.get("status") == "failed" and left2["n"] > 0:
        left2["n"] -= 1
        raise PermissionError(5, "Access is denied", "channel_state.json")
    return real_set(**changes)
def broken_download(cfg, up, progress):
    raise channel.ChannelError("network gone (test)")
channel._set_update = failed_busy
t0 = _time.time()
try:
    channel._install("test", download=broken_download, exit_after=False)
finally:
    channel._set_update = real_set
up = channel.state().get("update") or {}
ok(up.get("status") == "failed" and "network gone" in (up.get("message") or "") and left2["n"] == 0,
   "a failure is still recorded when the state file is busy at that moment (not left on «downloading»)")
ok("def _note_update" in src[channel] and src[channel].count("_note_update(progress=pct)") == 1,
   "progress goes through _note_update (a busy file skips the note, not the install)")
ok("_note_update(status=\"downloading\"" in src[channel] and "up[\"status\"] = \"downloading\"" in src[channel],
   "the first note may be skipped too; the page still shows «downloading» while the install runs")
ok(safe_io.TRIES * safe_io.WAIT >= 9, "a busy file is waited on for ~10 s before giving up")

# ---- [Codex audit 2026-10-29 on-demand, P2] the outcome survives a state file that refuses every write
def _join():
    t = channel._worker["thread"]
    if t:
        t.join(30)
def never_saves(**changes):
    raise PermissionError(5, "Access is denied", "channel_state.json")
for f in (channel.PENDING,):
    if os.path.exists(f):
        os.remove(f)
channel._unsaved.clear()
real_set(status="available", version="9.9.9 beta", progress=0, message="")
calls = {"n": 0}
def counted_download(cfg, up, progress):
    calls["n"] += 1
    with zipfile.ZipFile(zsrc, "w") as z:      # the install removes the zip after unpacking it
        z.writestr("upd/apply_update.py", "print('ok')\n")
        z.writestr("upd/payload/VERSION", "9.9.9 beta\n")
    return zip_download(cfg, up, progress)
os.environ.pop("LIBRARY_KEEPER_LAUNCHER", None)
channel._set_update = never_saves
try:
    ok1, _m = channel.install("test", download=counted_download, dry_run=lambda upd: (0, "ok"), exit_after=False)
    _join()
    st = channel.status("manager")["update"]
    ok2, msg2 = channel.install("test", download=counted_download, dry_run=lambda upd: (0, "ok"), exit_after=False)
    _join()
finally:
    channel._set_update = real_set
ok(ok1 and st.get("status") == "ready", f"every state write refused: the page still shows «ready» after the download ({st.get('status')})")
ok(not ok2 and calls["n"] == 1, "… and a second approval does not download the update again (pending is respected)")
real_set(progress=0)   # a later write that works saves what was kept in memory
ok((channel.state().get("update") or {}).get("status") == "ready" and not channel._unsaved,
   "the next write that succeeds saves the kept notes to the file")
if os.path.exists(channel.PENDING):
    os.remove(channel.PENDING)

channel._unsaved.clear()
real_set(status="available", version="9.9.9 beta", progress=0, message="")
def fail_after_start(**changes):
    if changes.get("status") == "downloading":
        return real_set(**changes)
    raise PermissionError(5, "Access is denied", "channel_state.json")
channel._set_update = fail_after_start
try:
    channel.install("test", download=broken_download, exit_after=False)
    _join()
    st = channel.status("manager")["update"]
    ok3, _m = channel.install("test", download=broken_download, exit_after=False)
    _join()
finally:
    channel._set_update = real_set
ok(st.get("status") == "failed" and "network gone" in (st.get("message") or ""),
   f"«failed» refused three times: the page shows the failure, not «downloading» ({st.get('status')})")
ok(ok3, "… and «إعادة المحاولة» is accepted")
channel._unsaved.clear()

# ---- [AGY-20261029-01] the install step after the download waits on busy files too
import importlib.util, re as _re
_sp = os.path.join(os.path.dirname(channel.__file__), "..", "scripts")
_spec = importlib.util.spec_from_file_location("apply_update_t", os.path.join(_sp, "apply_update.py"))
au = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(au)
src_au = open(os.path.join(_sp, "apply_update.py"), encoding="utf-8").read()
ok(not _re.findall(r"(?<![\w.(, ])os\.replace\(", src_au.replace("retry_io(os.replace", "")) and src_au.count("retry_io(os.replace") == 7
   and src_au.count("retry_io(os.remove, db + tail)") == 2,
   "apply_update.py: every replace (database swap, catalog base, settings, rollback) waits on a busy file")
a_, b_ = os.path.join(tmpdir, "au_a.txt"), os.path.join(tmpdir, "au_b.txt")
open(a_, "w").write("x")
busy["n"] = 4
os.replace = windows_replace
try:
    au.retry_io(os.replace, a_, b_, wait=0.01)
finally:
    os.replace = real_replace
ok(os.path.exists(b_) and not os.path.exists(a_) and busy["n"] == 0, "retry_io: a replace refused four times still happens")
src_auto = open(os.path.join(_sp, "auto_update.py"), encoding="utf-8").read()
ok("os.remove(PENDING)" in src_auto and "for _ in range(100):" in src_auto,
   "auto_update.py: the pending file is moved away after waiting, or removed — never installed twice")
safe_io.write_json(channel.RESULT, {"ok": True, "version": "9.9.9 beta", "at": "x"})
real_sr = safe_io.replace
def always_busy(src, dst, *a, **k):
    if src == channel.RESULT:
        raise PermissionError(5, "Access is denied", src)
    return real_sr(src, dst, *a, **k)
safe_io.replace = always_busy
try:
    channel.after_start()
finally:
    safe_io.replace = real_sr
ok(not os.path.exists(channel.RESULT), "after_start: a result file that stays busy is removed, not read again at every start")
channel._unsaved.clear()

for f in (channel.STATE, channel.PENDING, channel.RESULT):
    if os.path.exists(f):
        os.remove(f)
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
