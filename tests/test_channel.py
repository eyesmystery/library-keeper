"""AUTOUPD (Ibrahim 2026-10-16 «approve»): updates and messages sent from Ibrahim's side through a signed channel,
installed at the library after the manager's approval. Ed25519 against RFC 8032, the signed file, the hourly check,
the download / checksum / dry run / pending file, scripts/auto_update.py, the result after a restart, permissions.
The whole chain with a real server restart is tests/e2e_autoupdate.py (not in run_all: it starts servers)."""
import io, json, os, subprocess, sys, zipfile, hashlib, shutil, tempfile
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
os.environ.pop("LIBRARY_KEEPER_LAUNCHER", None)
import models; models.init_db()
import ed25519, channel, dev_notes, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# ---------------------------------------------------------------- Ed25519 (RFC 8032 §7.1 test vectors 1-3)
for sk, pk, msg, sig in (
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb", "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7", "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a")):
    s, m = bytes.fromhex(sk), bytes.fromhex(msg)
    ok(ed25519.public_key(s).hex() == pk and ed25519.sign(s, m).hex() == sig and ed25519.verify(bytes.fromhex(pk), m, bytes.fromhex(sig)),
       f"RFC 8032 vector (message {msg or 'empty'}): key, signature and verification")
ok(not ed25519.verify(bytes.fromhex(pk), b"\x00", bytes.fromhex(sig)), "a changed message does not verify")

SK = bytes(range(32)); PK = ed25519.public_key(SK)
channel.public_key = lambda: PK
def signed(payload, sk=SK, fmt="manar-channel-1"):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return json.dumps({"format": fmt, "payload": text, "signature": ed25519.sign(sk, text.encode()).hex()}).encode()

# ---------------------------------------------------------------- the signed file
pl = {"seq": 5, "messages": [], "update": None}
ok(channel.read_manifest(signed(pl))["seq"] == 5, "a file signed with Ibrahim's key is read")
bad = json.loads(signed(pl)); bad["payload"] = bad["payload"].replace('"seq": 5', '"seq": 6')
for raw, what in ((json.dumps(bad).encode(), "a changed payload"), (signed(pl, sk=bytes(32)), "another key"),
                  (signed(pl, fmt="x"), "an unknown format"), (b"{not json", "a broken file")):
    try:
        channel.read_manifest(raw); ok(False, f"{what} is refused")
    except channel.ChannelError:
        ok(True, f"{what} is refused")
ok(channel.version_key("0.9.10 beta") > channel.version_key("0.9.9 beta") > channel.version_key("0.9.8b"), "0.9.10 is newer than 0.9.9")

# ---------------------------------------------------------------- the hourly check
for f in (channel.STATE, channel.PENDING, channel.RESULT):
    if os.path.exists(f): os.remove(f)
installed = channel.installed_version()
newer = "9.9.9 beta"
up = {"version": newer, "asset": "ThalathSmart-update-test.zip", "sha256": "x", "size": 10, "notes": "إصلاحات"}
msg = {"ref": "DN-test-channel", "title": "رسالة من القناة", "body": "تجربة", "action": {"type": "books", "label": "عرض", "publisher_any": ["دار تجربة"]}}
s = channel.check(fetch=lambda: ({}, signed({"seq": 7, "messages": [msg], "update": up})))
ok(s["update"]["status"] == "available" and s["update"]["version"] == newer and s["last_error"] is None, "a newer version in the signed file is offered")
ok(any(n["ref"] == "DN-test-channel" for n in dev_notes.list_rows("all")), "the channel's message joins «ملاحظات من المطور»")
s = channel.check(fetch=lambda: ({}, signed({"seq": 6, "messages": [], "update": None})))
ok("أقدم" in (s["last_error"] or "") and s["update"]["status"] == "available", "an older file (lower seq) is ignored")
s = channel.check(fetch=lambda: ({}, signed({"seq": 8, "messages": [], "update": dict(up, version=installed)})))
ok(s["update"]["status"] == "none", "the installed version itself is not offered")
s = channel.check(fetch=lambda: ({}, signed({"seq": 9, "messages": [], "update": up}, sk=bytes(32))))
ok("توقيع" in (s["last_error"] or ""), "a file with a wrong signature changes nothing")

# ---------------------------------------------------------------- install: download, checksum, dry run, pending
def make_zip(path, ok_apply=True):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ThalathSmart-update-test/apply_update.py", "import sys\nsys.exit(0)\n" if ok_apply else "import sys\nsys.exit(3)\n")
        z.writestr("ThalathSmart-update-test/payload/VERSION", newer + "\n")
        z.writestr("ThalathSmart-update-test/catalog.db", "x")
tmp = tempfile.mkdtemp()
zp = os.path.join(tmp, "ThalathSmart-update-test.zip"); make_zip(zp)
data = open(zp, "rb").read()
up_ok = dict(up, sha256=hashlib.sha256(data).hexdigest(), size=len(data))
channel.check(fetch=lambda: ({}, signed({"seq": 10, "messages": [], "update": up_ok})))
channel._release = lambda cfg: {"assets": [{"name": up_ok["asset"], "url": "x", "size": len(data)}]}
class _R(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
channel._asset_stream = lambda cfg, asset, timeout=60: _R(data)
calls = []
channel._install("مدير تجربة", dry_run=lambda upd: (calls.append(upd), (0, "✓ فحص تجريبي"))[1], exit_after=False)
st = channel.state()["update"]
pend = json.load(open(channel.PENDING, encoding="utf-8")) if os.path.exists(channel.PENDING) else {}
ok(st["status"] == "ready" and pend.get("version") == newer and os.path.isfile(os.path.join(pend.get("update_dir", ""), "apply_update.py"))
   and calls and pend.get("by") == "مدير تجربة", "approved: downloaded, checked, tried, and left pending for the start file to install")
os.remove(channel.PENDING)
os.environ["LIBRARY_KEEPER_LAUNCHER"] = "1"
channel._set_update(status="available")
channel._install("مدير", dry_run=lambda upd: (0, "ok"), exit_after=False)
ok(channel.state()["update"]["status"] == "restarting" and os.path.exists(channel.PENDING), "under run_server.py the server restarts itself to install it")
os.environ.pop("LIBRARY_KEEPER_LAUNCHER"); os.remove(channel.PENDING)
channel._set_update(status="available")
channel._asset_stream = lambda cfg, asset, timeout=60: _R(data[:-5] + b"XXXXX")
channel._install("مدير", dry_run=lambda upd: (0, "ok"), exit_after=False)
ok(channel.state()["update"]["status"] == "failed" and "بصمة" in channel.state()["update"]["message"] and not os.path.exists(channel.PENDING),
   "a file whose checksum differs from the signed one is not installed")
channel._set_update(status="available")
channel._asset_stream = lambda cfg, asset, timeout=60: _R(data)
channel._install("مدير", dry_run=lambda upd: (1, "✗ لم يتم التحديث: خطأ"), exit_after=False)
ok(channel.state()["update"]["status"] == "failed" and not os.path.exists(channel.PENDING), "a failed dry run stops it before anything changes")
evil = os.path.join(tmp, "evil.zip")
with zipfile.ZipFile(evil, "w") as z:
    z.writestr("../outside.txt", "x")
try:
    channel._safe_extract(evil, os.path.join(tmp, "x")); ok(False, "a path leaving the folder is refused")
except channel.ChannelError:
    ok(True, "a path leaving the folder is refused")

# ---------------------------------------------------------------- scripts/auto_update.py, then the next start
root = os.path.dirname(os.getcwd())
for code, want in ((0, True), (3, False)):
    upd = os.path.join(tmp, f"u{code}"); os.makedirs(upd)
    with open(os.path.join(upd, "apply_update.py"), "w") as f:
        f.write(f"import sys\nprint('applying', sys.argv[1:])\nsys.exit({code})\n")
    json.dump({"version": newer, "update_dir": upd, "by": "مدير", "from_version": installed}, open(channel.PENDING, "w"))
    env = dict(os.environ, LIBRARY_KEEPER_PORT="8899")
    subprocess.run([sys.executable, os.path.join(root, "scripts", "auto_update.py")], env=env, capture_output=True)
    res = json.load(open(channel.RESULT, encoding="utf-8"))
    ok(res["ok"] is want and not os.path.exists(channel.PENDING) and "--port" in open(os.path.join(root, res["log"]), encoding="utf-8").read(),
       f"auto_update.py records {'success' if want else 'a failure'} and never runs the same job twice")
    audits = []
    r = channel.after_start(lambda u, a, t, d: audits.append(a))
    st = channel.state()["update"]
    ok(r and st["status"] == ("installed" if want else "failed") and audits == ["update.installed" if want else "update.failed"]
       and not os.path.exists(channel.RESULT), "the next start shows and logs the outcome once")

# ---------------------------------------------------------------- apply_update carries the channel settings
sys.path.insert(0, os.path.join(root, "scripts"))
import apply_update
app_dir, upd_dir = os.path.join(tmp, "app"), os.path.join(tmp, "upd")
os.makedirs(os.path.join(app_dir, "data")); os.makedirs(upd_dir)
json.dump({"repo": "a/b", "token": "LIBRARY-TOKEN"}, open(os.path.join(app_dir, "data", "channel.json"), "w"))
json.dump({"repo": "eyesmystery/library-keeper", "tag": "manar-channel", "enabled": True}, open(os.path.join(upd_dir, "channel.json"), "w"))
notes = []
apply_update.apply_channel(app_dir, upd_dir, notes)
c = json.load(open(os.path.join(app_dir, "data", "channel.json")))
ok(c["repo"] == "eyesmystery/library-keeper" and c["token"] == "LIBRARY-TOKEN" and not notes, "an update's channel settings keep the library's token")

# ---------------------------------------------------------------- who may do what
s = channel.check(fetch=lambda: ({}, signed({"seq": 20, "messages": [], "update": up_ok})))
ok(s["update"]["status"] == "failed", "after a failed install the same update stays «failed» (the manager may try again)")
channel._set_update(status="available")
m = login(A.app.test_client()); s_ = login(A.app.test_client(), "staff")
r = s_.get("/api/admin/inbox/channel").get_json()
ok(r["update"]["status"] == "available" and r["can_install"] is False, "staff see the update but cannot install it")
ok(s_.post("/api/admin/channel/install", json={"version": newer}).status_code == 403, "an install request from staff is refused")
ok(m.get("/api/admin/inbox/channel").get_json()["can_install"] is True, "the manager may")
ok(m.post("/api/admin/channel/install", json={"version": "0.0.1"}).status_code == 409, "an approval for another version than shown is refused")
al = s_.get("/api/admin/inbox/alerts").get_json()
ok(any(i["key"] == "update" and i["open"] == 1 for i in al["items"]), "«التنبيهات» shows the waiting update")
ok(A.app.test_client().get("/api/admin/inbox/channel").status_code == 401, "nothing without a sign-in")
shutil.rmtree(tmp, ignore_errors=True)
for f in (channel.STATE, channel.PENDING, channel.RESULT):
    if os.path.exists(f): os.remove(f)
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
