"""[INBOXCOVERS] Ibrahim 2026-10-30 «خل البلاغ يحمل الاغلفة من التحديث القادم»: the sealed inbox file carries the
covers the staff uploaded / fetched / chose / removed, once each; inbox_pull saves the pictures and
scripts/apply_library_covers.py puts them in Ibrahim's catalog (old picture backed up, removals only listed)."""
import os, sys, json, random, struct, zlib, hashlib, base64, tempfile, subprocess, importlib.util
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import sealed, inbox_send, covers
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

def _png(w, h, seed):
    random.seed(seed)
    raw = b"".join(b"\x00" + bytes(random.getrandbits(8) for _ in range(w * 3)) for _ in range(h))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")

SK = sealed.new_private(); PK = sealed.public_key(SK)
inbox_send.public_key = lambda: PK
for f in (inbox_send.CONFIG, inbox_send.STATE):
    if os.path.exists(f): os.remove(f)
con = models.get_connection()
b1, b2, b3 = [r[0] for r in con.execute("SELECT book_id FROM books WHERE active = 1 ORDER BY book_id LIMIT 3")]
LIB = _png(120, 180, 1)
covers.save_cover(b1, LIB, "staff")
covers.remove_cover(b2)
def audit(action, target, user="yara"):
    con.execute("INSERT INTO audit_log (at, username, action, target, details) VALUES (datetime('now'), ?, ?, ?, '{}')", (user, action, str(target)))
    con.commit()
con.execute("DELETE FROM audit_log WHERE action LIKE 'cover.%'"); con.commit()
audit("cover.fetch", b1); audit("cover.upload", b1)
audit("cover.delete", b2)

# 1. what the file carries
items, left = inbox_send.staff_covers({})
by = {c["book_id"]: c for c in items}
ok(set(by) == {b1, b2} and left == 0, "the two books whose covers the staff changed are in, nothing else")
ok(by[b1]["sha256"] == hashlib.sha256(LIB).hexdigest() and base64.b64decode(by[b1]["data"]) == LIB and by[b1]["ext"] == "png"
   and by[b1]["by"] == "yara", "the uploaded picture itself, its sha256 and who changed it")
ok(by[b2].get("removed") is True and "data" not in by[b2], "a removed cover is sent as «removed», without a picture")
items2, _ = inbox_send.staff_covers({str(b1): hashlib.sha256(LIB).hexdigest(), str(b2): "removed"})
ok(items2 == [], "a cover already sent is not sent again")
small, left = inbox_send.staff_covers({}, max_bytes=10)
ok(left == 1 and all(c.get("removed") for c in small), "past the size limit a picture waits for the next file")

# 2. the sealed file and the state after a successful send
TOKEN = "github_pat_" + "A1b2C3d4" * 8
inbox_send.set_settings(enabled=True, token=TOKEN)
sent = []
okk, _ = inbox_send.send(reason="manual", by="المدير", put=lambda cfg, name, blob: sent.append((name, blob)))
doc = json.loads(sealed.open_sealed(SK, sent[-1][1]))
ok(okk and len(doc["covers"]) == 2 and doc["counts"]["covers"] == 2, "the sealed file carries the covers and counts them")
ok("## 14. أغلفة غيّرها الموظفون (2)" in doc["markdown"] and f"book_id {b1}" in doc["markdown"], "the Markdown lists them for Ibrahim")
st = inbox_send.state().get("covers_sent") or {}
ok(st.get(str(b1)) == hashlib.sha256(LIB).hexdigest() and st.get(str(b2)) == "removed", "after the upload they are marked sent")
okk, _ = inbox_send.send(reason="manual", by="المدير", put=lambda cfg, name, blob: sent.append((name, blob)))
doc2 = json.loads(sealed.open_sealed(SK, sent[-1][1]))
ok(doc2["covers"] == [] and "## 14. أغلفة غيّرها الموظفون (0)" in doc2["markdown"], "the next file does not repeat them")
NEW = _png(120, 180, 2)
covers.save_cover(b1, NEW, "staff"); audit("cover.upload", b1)
fails = []
def boom(cfg, name, blob): raise inbox_send.SendError("network")
okk, _ = inbox_send.send(reason="manual", put=boom)
ok(not okk and (inbox_send.state().get("covers_sent") or {}).get(str(b1)) == hashlib.sha256(LIB).hexdigest(),
   "a failed upload does not mark the new picture sent")
okk, _ = inbox_send.send(reason="manual", put=lambda cfg, name, blob: sent.append((name, blob)))
doc3 = json.loads(sealed.open_sealed(SK, sent[-1][1]))
ok([c["book_id"] for c in doc3["covers"]] == [b1] and base64.b64decode(doc3["covers"][0]["data"]) == NEW, "a changed picture is sent again")

# 3. Ibrahim's side: the pulled file's pictures applied, ours backed up
spec = importlib.util.spec_from_file_location("alc", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "apply_library_covers.py"))
ALC = importlib.util.module_from_spec(spec); spec.loader.exec_module(ALC)
tmp = tempfile.mkdtemp(); stem = "20261030-090000-manual"
cdir = os.path.join(tmp, stem + "_covers"); os.makedirs(cdir)
OURS = _png(120, 180, 3)
covers.save_cover(b3, OURS, "openlibrary")
meta = {"covers": [{"book_id": b3, "ext": "png", "sha256": hashlib.sha256(NEW).hexdigest(), "source": "staff", "by": "yara", "at": "2026-10-30 08:00"},
                   {"book_id": b2, "removed": True, "by": "yara", "at": "2026-10-30 08:01"},
                   {"book_id": b1, "ext": "png", "sha256": "0" * 64, "source": "staff", "by": "yara", "at": "x"}]}
open(os.path.join(cdir, f"{b3}.png"), "wb").write(NEW)
open(os.path.join(cdir, f"{b1}.png"), "wb").write(NEW)
mp = os.path.join(tmp, stem + ".json"); json.dump(meta, open(mp, "w", encoding="utf-8"))
_stem, todo, same, removed, missing = ALC.plan(mp)
ok([t["book_id"] for t in todo] == [b3] and [r[0] for r in removed] == [b2] and [m[0] for m in missing] == [b1],
   "plan: one picture to apply, one removal only listed, a damaged picture skipped")
sys.argv = ["x", mp, "--apply"]
ALC.main()
row = models.get_connection().execute("SELECT cover_url, cover_source FROM books WHERE book_id = ?", (b3,)).fetchone()
path = covers.cover_path(row[0].split("?")[0].rsplit("/", 1)[-1])
ok(path and open(path, "rb").read() == NEW and row[1] == "staff", "the library's picture is now ours, source «staff»")
bk = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(models.__file__))), "backups", "covers_before_" + stem, f"{b3}.png")
ok(os.path.isfile(bk) and open(bk, "rb").read() == OURS, "our old picture is kept in backups/")
ok(models.get_connection().execute("SELECT COUNT(*) FROM audit_log WHERE action = 'library.cover_applied' AND target = ?", (str(b3),)).fetchone()[0] == 1,
   "the change is in the audit with the inbox file")
_s, todo2, same2, _r, _m = ALC.plan(mp)
ok(todo2 == [] and same2 == [b3], "applying the same file again changes nothing")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
