"""Send updates and messages to the library through the signed channel (AUTOUPD, Ibrahim 2026-10-16). Run on
Ibrahim's Mac only, from the project folder, with the project's Python:

  backend/venv/bin/python scripts/channel_publish.py keygen            once: makes the signing key (kept in
                                                                       ~/.library_keeper/channel/, never in git) and
                                                                       writes the public key to backend/channel_key.py
  backend/venv/bin/python scripts/channel_publish.py token             saves a read-only GitHub token for a private
                                                                       repository (typed, not shown); the next update
                                                                       package carries it to the library
  backend/venv/bin/python scripts/channel_publish.py show              what the library sees now
  backend/venv/bin/python scripts/channel_publish.py message --ref R --title T --body B [--publisher NAME ...]
                                                                       a note in «ملاحظات من المطور» (with a list of the
                                                                       publisher's books when --publisher is given)
  backend/venv/bin/python scripts/channel_publish.py unmessage --ref R
  backend/venv/bin/python scripts/channel_publish.py update release/ThalathSmart-update-....zip --notes "…"
                                                                       offers that update; the library's manager
                                                                       approves it in «التنبيهات»
  backend/venv/bin/python scripts/channel_publish.py request-inbox [--scope all]
                                                                       asks the library for its inbox file now
                                                                       (INBOXSEND); it is sealed for Ibrahim and
                                                                       uploaded at the library's next hourly check —
                                                                       then `scripts/inbox_pull.py pull`
  … --dry-run on message/unmessage/update/request-inbox: build and check the signed file, publish nothing.

The channel is the GitHub release `manar-channel` of the repository (uploaded with the gh CLI, as a pre-release so it
is never shown as the latest version). Each publication raises `seq`; the library refuses an older file.
"""
import argparse
import getpass
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import ed25519  # noqa: E402

KEYDIR = os.path.expanduser("~/.library_keeper/channel")
SIGNING = os.path.join(KEYDIR, "signing_key")
TOKEN = os.path.join(KEYDIR, "read_token")
LAST = os.path.join(KEYDIR, "last_payload.json")
REPO, TAG, ASSET = "eyesmystery/library-keeper", "manar-channel", "manar-channel.json"
KEYFILE = os.path.join(ROOT, "backend", "channel_key.py")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gh(*args, check=True):
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit("gh " + " ".join(args[:3]) + " failed: " + (p.stderr or p.stdout).strip()[:400])
    return p


def secret():
    try:
        with open(SIGNING, encoding="utf-8") as f:
            return bytes.fromhex(f.read().strip())
    except (OSError, ValueError):
        raise SystemExit("no signing key: run `channel_publish.py keygen` first")


def keygen(force=False):
    if os.path.exists(SIGNING) and not force:
        raise SystemExit("a signing key exists already (" + SIGNING + "); --force makes a new one, and then every "
                         "library must get the new public key in a manual update first")
    os.makedirs(KEYDIR, mode=0o700, exist_ok=True)
    sk = os.urandom(32)
    fd = os.open(SIGNING, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(sk.hex() + "\n")
    pk = ed25519.public_key(sk).hex()
    with open(KEYFILE, encoding="utf-8") as f:
        text = f.read()
    import re
    text = re.sub(r'PUBLIC_KEY = ".*"', f'PUBLIC_KEY = "{pk}"', text)
    with open(KEYFILE, "w", encoding="utf-8") as f:
        f.write(text)
    print("signing key saved in", SIGNING, "(back it up somewhere safe)")
    print("public key written to backend/channel_key.py:", pk)


def save_token():
    tok = getpass.getpass("read-only GitHub token (not shown): ").strip()
    if not tok:
        raise SystemExit("nothing saved")
    os.makedirs(KEYDIR, mode=0o700, exist_ok=True)
    fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    print("saved; the next package built by make_release.py carries it to the library (data/channel.json)")


def current():
    """The payload now published (checked against our key), else the last one we published, else an empty one."""
    with tempfile.TemporaryDirectory() as d:
        p = gh("release", "download", TAG, "-R", REPO, "-p", ASSET, "-D", d, check=False)
        path = os.path.join(d, ASSET)
        if p.returncode == 0 and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            pk = ed25519.public_key(secret())
            if not ed25519.verify(pk, doc["payload"].encode("utf-8"), bytes.fromhex(doc["signature"])):
                raise SystemExit("the published channel file is NOT signed with our key — stop and check the repository")
            return json.loads(doc["payload"])
    if os.path.exists(LAST):
        with open(LAST, encoding="utf-8") as f:
            return json.load(f)
    return {"seq": 0, "messages": [], "update": None}


def signed(payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return {"format": "manar-channel-1", "payload": text, "signature": ed25519.sign(secret(), text.encode("utf-8")).hex()}


def publish(payload, dry_run=False, upload=None):
    payload = dict(payload, seq=int(payload.get("seq") or 0) + 1, issued=now())
    doc = signed(payload)
    assert ed25519.verify(ed25519.public_key(secret()), doc["payload"].encode("utf-8"), bytes.fromhex(doc["signature"]))
    if dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        print("(dry run: nothing published)")
        return
    if gh("release", "view", TAG, "-R", REPO, check=False).returncode != 0:
        gh("release", "create", TAG, "-R", REPO, "--prerelease", "--latest=false", "--title", "قناة منار — لا تحذف",
           "--notes", "قناة التحديثات والرسائل الموقّعة لتطبيق «منار» في المكتبة. يقرؤها التطبيق كل ساعة. لا تحذف هذا الإصدار.")
    if upload:
        gh("release", "upload", TAG, upload, "-R", REPO, "--clobber")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, ASSET)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        gh("release", "upload", TAG, path, "-R", REPO, "--clobber")
    os.makedirs(KEYDIR, mode=0o700, exist_ok=True)
    with open(LAST, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"published seq {payload['seq']}: {len(payload.get('messages') or [])} message(s), update "
          + (payload["update"]["version"] if payload.get("update") else "none"))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen"); k.add_argument("--force", action="store_true")
    sub.add_parser("token")
    sub.add_parser("show")
    m = sub.add_parser("message")
    m.add_argument("--ref", required=True); m.add_argument("--title", required=True); m.add_argument("--body", default="")
    m.add_argument("--publisher", action="append", default=[]); m.add_argument("--label", default=None)
    m.add_argument("--dry-run", action="store_true")
    u = sub.add_parser("unmessage"); u.add_argument("--ref", required=True); u.add_argument("--dry-run", action="store_true")
    ri = sub.add_parser("request-inbox"); ri.add_argument("--scope", choices=("open", "all"), default="open")
    ri.add_argument("--dry-run", action="store_true")
    up = sub.add_parser("update"); up.add_argument("zip"); up.add_argument("--notes", default="")
    up.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "keygen":
        return keygen(a.force)
    if a.cmd == "token":
        return save_token()
    if a.cmd == "show":
        return print(json.dumps(current(), ensure_ascii=False, indent=1))
    payload = current()
    if a.cmd == "message":
        note = {"ref": a.ref[:80], "title": a.title[:200], "body": a.body[:3000], "created": now()}
        if a.publisher:
            note["action"] = {"type": "books", "label": a.label or ("عرض كتب " + a.publisher[0]), "publisher_any": a.publisher}
        payload["messages"] = [x for x in payload.get("messages") or [] if x.get("ref") != note["ref"]] + [note]
        return publish(payload, a.dry_run)
    if a.cmd == "request-inbox":   # INBOXSEND (Ibrahim 2026-10-23 «وقت ما انا ابغى»): one new id per request
        payload["inbox_request"] = {"id": "REQ-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
                                    "scope": a.scope, "at": now()}
        return publish(payload, a.dry_run)
    if a.cmd == "unmessage":
        payload["messages"] = [x for x in payload.get("messages") or [] if x.get("ref") != a.ref]
        return publish(payload, a.dry_run)
    if a.cmd == "update":
        z = os.path.abspath(a.zip)
        name = os.path.basename(z)
        if not name.startswith("ThalathSmart-update-") or not name.endswith(".zip"):
            raise SystemExit("give an update package made by make_release.py (ThalathSmart-update-*.zip)")
        import zipfile
        with zipfile.ZipFile(z) as zf:
            ver = next((zf.read(n).decode("utf-8").strip() for n in zf.namelist() if n.endswith("payload/VERSION")), None)
        if not ver:
            raise SystemExit("no payload/VERSION in the package")
        payload["update"] = {"version": ver, "asset": name, "sha256": sha256(z), "size": os.path.getsize(z),
                             "notes": a.notes[:2000], "published": now()}
        return publish(payload, a.dry_run, upload=None if a.dry_run else z)


if __name__ == "__main__":
    main()
