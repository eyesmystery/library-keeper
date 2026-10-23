"""INBOXSEND (Ibrahim 2026-10-23 «رفع تلقائي مشفّر»): the library's inbox file sealed for Ibrahim and uploaded every
week and when he asks. X25519 against RFC 7748, the sealed file (tampering, wrong key), the manager's settings and
the token kept out of every answer, the weekly and requested sends, the request in the signed channel, the alert,
the upload errors, and the Mac-side tools."""
import json, os, re, stat, sys, time
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import ed25519, sealed, inbox_send, channel, app as A
from auth_helper import login
from datetime import datetime, timedelta, timezone
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

h = bytes.fromhex
# ---------------------------------------------------------------- X25519 (RFC 7748 §5.2 vector 1 and §6.1)
ok(sealed.x25519(h("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4"),
                 h("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c")).hex()
   == "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552", "RFC 7748 §5.2 vector 1")
a, b = h("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"), h("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
ok(sealed.public_key(a).hex() == "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
   and sealed.public_key(b).hex() == "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
   and sealed.x25519(a, sealed.public_key(b)).hex() == sealed.x25519(b, sealed.public_key(a)).hex()
   == "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742", "RFC 7748 §6.1 Alice and Bob agree")

# ---------------------------------------------------------------- the sealed file
SK = sealed.new_private(); PK = sealed.public_key(SK)
text = ("بلاغ زائر: الغلاف خطأ — " * 3000).encode()
blob = sealed.seal(PK, text)
ok(sealed.open_sealed(SK, blob) == text and text not in blob and b"\xd8" not in blob[:14], "only the key holder opens it; the text is not readable inside")
ok(sealed.seal(PK, text) != blob, "every file has its own key and nonce")
for bad, what in ((blob[:-1] + bytes([blob[-1] ^ 1]), "a changed tag"), (blob[:60] + bytes([blob[60] ^ 1]) + blob[61:], "a changed byte"),
                  (b"X" + blob[1:], "a wrong header"), (blob[:40], "a cut file")):
    try:
        sealed.open_sealed(SK, bad); ok(False, f"{what} is refused")
    except sealed.SealedError:
        ok(True, f"{what} is refused")
try:
    sealed.open_sealed(sealed.new_private(), blob); ok(False, "another key cannot open it")
except sealed.SealedError:
    ok(True, "another key cannot open it")

# ---------------------------------------------------------------- the shipped public key
import inbox_key
ok(re.fullmatch(r"[0-9a-f]{64}", inbox_key.PUBLIC_KEY or "") is not None, "Ibrahim's public key is in backend/inbox_key.py")
ok(inbox_send.DEFAULTS["repo"] == "eyesmystery/manar-inbox" and inbox_send.DEFAULTS["enabled"] is False
   and inbox_send.DEFAULTS["token"] is None, "off by default, no token shipped, the private inbox repository")

inbox_send.public_key = lambda: PK          # files in this test are sealed for the test key
for f in (inbox_send.CONFIG, inbox_send.STATE, channel.STATE):
    if os.path.exists(f): os.remove(f)

# ---------------------------------------------------------------- permissions and settings
m = login(A.app.test_client()); s_ = login(A.app.test_client(), "staff")
st = s_.get("/api/admin/inbox/send").get_json()
ok(st["enabled"] is False and st["can_manage"] is False and "يشغّله مدير المكتبة" in st["not_ready"], "staff see it is off and cannot manage it")
ok(s_.post("/api/admin/send-inbox/settings", json={"enabled": True}).status_code == 403
   and s_.post("/api/admin/send-inbox/now", json={}).status_code == 403, "staff cannot turn it on or send")
r = m.post("/api/admin/send-inbox/settings", json={"token": "short", "enabled": True})
ok(r.status_code == 400 and not inbox_send.config()["enabled"], "a broken token is refused and nothing changes")
TOKEN = "github_pat_" + "A1b2C3d4" * 8
r = m.post("/api/admin/send-inbox/settings", json={"token": TOKEN, "enabled": True})
body = r.get_data(as_text=True)
ok(r.status_code == 200 and r.get_json()["enabled"] and r.get_json()["has_token"] and TOKEN not in body, "the manager saves the token and turns it on; the token is not sent back")
ok(TOKEN not in m.get("/api/admin/inbox/send").get_data(as_text=True) and TOKEN not in s_.get("/api/admin/inbox/send").get_data(as_text=True),
   "the token is in no status answer")
if os.name == "posix":
    ok(stat.S_IMODE(os.stat(inbox_send.CONFIG).st_mode) == 0o600, "data/inbox_send.json is readable by this user only")
import auth
rows = [dict(x) for x in models.get_connection().execute("SELECT * FROM audit_log WHERE action='inbox_send.settings'")]
ok(rows and all(TOKEN not in (x.get("details") or "") for x in rows), "the setting change is in the audit log, without the token")

# ---------------------------------------------------------------- one send
sent = []
def put(cfg, name, blob):
    sent.append((cfg, name, blob))
okk, msg = inbox_send.send(reason="manual", by="المدير", put=put)
cfg, name, blob = sent[-1]
doc = json.loads(sealed.open_sealed(SK, blob))
ok(okk and re.fullmatch(r"\d{8}-\d{6}-manual\.manar", name) and cfg["token"] == TOKEN, "a sealed file is uploaded with the saved token")
ok(doc["format"] == "manar-inbox-1" and "بلاغات الكتب من الزوار" in doc["markdown"] and isinstance(doc["counts"], dict)
   and doc["reason"] == "manual" and doc["scope"] == "open", "inside: the same file as «تنزيل البلاغات», with its counts")
s = inbox_send.state()
due = datetime.fromisoformat(inbox_send.next_due())
ok(s["last_sent"] and s["last_file"] == name and s["last_error"] is None
   and timedelta(days=6, hours=23) < due - datetime.now(timezone.utc) <= timedelta(days=7), "the next file is due in a week")
ok(inbox_send.tick(put=put) is None and len(sent) == 1, "nothing is sent before the week is over")
inbox_send._save_state(last_sent=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat())
r = inbox_send.tick(put=put)
ok(r and r[0] and sent[-1][1].endswith("-weekly.manar"), "after a week the weekly file is sent")
logged = models.get_connection().execute("SELECT COUNT(*) FROM audit_log WHERE action='inbox.sent'").fetchone()[0]
ok(logged == 0, "send() without audit writes nothing to the log (the server passes auth.audit)")

# ---------------------------------------------------------------- upload errors
def fail(cfg, name, blob):
    raise inbox_send.SendError("رمز الرفع غير صالح أو انتهت مدته — اطلب رمزًا جديدًا من المطوّر")
okk, msg = inbox_send.send(reason="manual", put=fail)
ok(not okk and "رمز الرفع" in inbox_send.state()["last_error"] and not inbox_send.state()["sending"], "a refused upload is reported and nothing is stuck")

# ---------------------------------------------------------------- Ibrahim's request through the signed channel
m.post("/api/admin/send-inbox/settings", json={"enabled": False})
CSK = bytes(range(32)); channel.public_key = lambda: ed25519.public_key(CSK)
def signed(payload):
    t = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return json.dumps({"format": "manar-channel-1", "payload": t, "signature": ed25519.sign(CSK, t.encode()).hex()}).encode()
runs = []
inbox_send.send_async = lambda **kw: runs.append(kw)
channel.check(fetch=lambda: ({}, signed({"seq": 1, "messages": [], "update": None, "inbox_request": {"id": "REQ-1", "scope": "all"}})))
ok(inbox_send.state()["pending_request"]["id"] == "REQ-1" and not runs, "a request while sending is off waits")
items = {i["key"]: i for i in m.get("/api/admin/inbox/alerts").get_json()["items"]}
ok(items["send"]["open"] == 1 and items["send"]["go"] == "alerts:send", "«التنبيهات» tells the manager Ibrahim asked for the file")
m.post("/api/admin/send-inbox/settings", json={"enabled": True})
r = inbox_send.tick(put=put)
ok(r and r[0] and sent[-1][1].endswith("-request.manar") and json.loads(sealed.open_sealed(SK, sent[-1][2]))["request_id"] == "REQ-1"
   and json.loads(sealed.open_sealed(SK, sent[-1][2]))["scope"] == "all", "once sending is on, the waiting request is sent (with its scope)")
s = inbox_send.state()
ok("REQ-1" in s["requests_done"] and not s.get("pending_request"), "the request is marked done")
items = {i["key"]: i for i in m.get("/api/admin/inbox/alerts").get_json()["items"]}
ok(items["send"]["open"] == 0, "the alert is gone")
channel.check(fetch=lambda: ({}, signed({"seq": 2, "messages": [], "update": None, "inbox_request": {"id": "REQ-1", "scope": "all"}})))
ok(not runs, "the same request is not sent twice")
channel.check(fetch=lambda: ({}, signed({"seq": 3, "messages": [], "update": None, "inbox_request": {"id": "REQ-2"}})))
ok(len(runs) == 1 and runs[0]["request_id"] == "REQ-2" and runs[0]["scope"] == "open", "a new request is sent right away when sending is on")
bad = json.loads(signed({"seq": 4, "messages": [], "update": None, "inbox_request": {"id": "REQ-3"}}))
bad["payload"] = bad["payload"].replace("REQ-3", "REQ-4")
channel.check(fetch=lambda: ({}, json.dumps(bad).encode()))
ok(len(runs) == 1, "a request in a file with a wrong signature is ignored")

# ---------------------------------------------------------------- «أرسل الآن»
r = m.post("/api/admin/send-inbox/now", json={"scope": "all"})
ok(r.status_code == 200 and runs[-1]["reason"] == "manual" and runs[-1]["scope"] == "all", "the manager sends now")
m.post("/api/admin/send-inbox/settings", json={"enabled": False})
ok(m.post("/api/admin/send-inbox/now", json={}).status_code == 400, "«أرسل الآن» is refused while sending is off")
m.post("/api/admin/send-inbox/settings", json={"clear_token": True})
ok(not inbox_send.config()["token"] and m.get("/api/admin/inbox/send").get_json()["has_token"] is False, "the token can be removed")

# ---------------------------------------------------------------- page and Mac-side tools
UP = os.path.dirname(os.path.dirname(os.path.abspath(inbox_send.__file__)))
page = open(os.path.join(UP, "admin", "index.html"), encoding="utf-8").read()
ok('id="sendBox"' in page and "/api/admin/send-inbox/settings" in page and "sub === 'send'" in page
   and 'type="password"' in page, "«إرسال البلاغات للمطوّر» in «التنبيهات»")
pull = open(os.path.join(UP, "scripts", "inbox_pull.py"), encoding="utf-8").read()
pub = open(os.path.join(UP, "scripts", "channel_publish.py"), encoding="utf-8").read()
ok("open_sealed" in pull and "manar-inbox" in pull and '"request-inbox"' in pub and "inbox_request" in pub, "the Mac opens the files and can ask for one")

for f in (inbox_send.CONFIG, inbox_send.STATE, channel.STATE):
    if os.path.exists(f): os.remove(f)
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
