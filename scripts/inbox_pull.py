"""The library's inbox files on Ibrahim's side (INBOXSEND, Ibrahim 2026-10-23 «رفع تلقائي مشفّر»). Run on Ibrahim's Mac
only, from the project folder, with the project's Python:

  backend/venv/bin/python scripts/inbox_pull.py keygen      once: the key pair that opens the files. The private key is
                                                            kept in ~/.library_keeper/inbox/ (never in git; back it up);
                                                            the public key is written to backend/inbox_key.py and
                                                            reaches the library with the next update
  backend/venv/bin/python scripts/inbox_pull.py setup-repo  once: the private repository the library writes to
  backend/venv/bin/python scripts/inbox_pull.py pull        downloads the files not seen yet, opens them and writes
                                                            data/inbox_received/<file>.md (+ .json: when, why, counts).
                                                            data/inbox_received/ is not in git (visitor conversations)
  backend/venv/bin/python scripts/inbox_pull.py list        what is in the repository and what was already opened

To ask the library for a file now: backend/venv/bin/python scripts/channel_publish.py request-inbox [--scope all]
(the library sends it at its next hourly check, once its manager has turned sending on).
GitHub is reached with the gh CLI and Ibrahim's own login; the library's upload token is never needed here.
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import sealed  # noqa: E402

REPO, FOLDER = "eyesmystery/manar-inbox", "inbox"
KEYDIR = os.path.expanduser("~/.library_keeper/inbox")
PRIVATE = os.path.join(KEYDIR, "x25519_private")
SEEN = os.path.join(KEYDIR, "seen.json")
OUT = os.path.join(ROOT, "data", "inbox_received")
KEYFILE = os.path.join(ROOT, "backend", "inbox_key.py")


def gh(*args, check=True, binary=False):
    p = subprocess.run(["gh", *args], capture_output=True, text=not binary)
    if check and p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace") if binary else p.stderr
        raise SystemExit("gh " + " ".join(args[:3]) + " failed: " + (err or "").strip()[:400])
    return p


def private_key():
    try:
        with open(PRIVATE, encoding="utf-8") as f:
            return bytes.fromhex(f.read().strip())
    except (OSError, ValueError):
        raise SystemExit("no private key: run `inbox_pull.py keygen` first")


def keygen(force=False):
    if os.path.exists(PRIVATE) and not force:
        raise SystemExit("a key exists already (" + PRIVATE + "); --force makes a new one, and files sealed for the old "
                         "key can then no longer be opened")
    os.makedirs(KEYDIR, mode=0o700, exist_ok=True)
    sk = sealed.new_private()
    fd = os.open(PRIVATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(sk.hex() + "\n")
    pk = sealed.public_key(sk).hex()
    with open(KEYFILE, encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r'PUBLIC_KEY = ".*"', f'PUBLIC_KEY = "{pk}"', text)
    with open(KEYFILE, "w", encoding="utf-8") as f:
        f.write(text)
    print("private key saved in", PRIVATE, "(back it up somewhere safe)")
    print("public key written to backend/inbox_key.py:", pk)


def setup_repo():
    if gh("repo", "view", REPO, "--json", "visibility", check=False).returncode == 0:
        vis = json.loads(gh("repo", "view", REPO, "--json", "visibility").stdout)["visibility"]
        print(f"{REPO} exists ({vis})")
        if vis != "PRIVATE":
            raise SystemExit("the inbox repository must be PRIVATE")
        return
    gh("repo", "create", REPO, "--private", "--add-readme",
       "--description", "ملفات البلاغات المختومة من تطبيق «منار» في المكتبة — لا يفتحها إلا مفتاح إبراهيم")
    print("created", REPO, "(private)")


def listing():
    p = gh("api", f"repos/{REPO}/contents/{FOLDER}", check=False)
    if p.returncode != 0:
        if "404" in (p.stderr or "") or "Not Found" in (p.stderr or ""):
            return []
        raise SystemExit("gh api failed: " + (p.stderr or "").strip()[:400])
    return sorted((x for x in json.loads(p.stdout) if x.get("type") == "file" and x["name"].endswith(".manar")),
                  key=lambda x: x["name"])


def seen():
    try:
        with open(SEEN, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_seen(d):
    os.makedirs(KEYDIR, mode=0o700, exist_ok=True)
    with open(SEEN, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def pull(again=False):
    sk, done, got = private_key(), seen(), []
    os.makedirs(OUT, exist_ok=True)
    for item in listing():
        name = item["name"]
        if name in done and not again:
            continue
        blob = gh("api", "-H", "Accept: application/vnd.github.raw", f"repos/{REPO}/contents/{FOLDER}/{name}",
                  binary=True).stdout
        try:
            doc = json.loads(sealed.open_sealed(sk, blob).decode("utf-8"))
        except (sealed.SealedError, ValueError) as e:
            print(f"✗ {name}: {e}")
            continue
        stem = name[:-len(".manar")]
        with open(os.path.join(OUT, stem + ".md"), "w", encoding="utf-8") as f:
            f.write(doc.get("markdown") or "")
        meta = {k: v for k, v in doc.items() if k != "markdown"}
        with open(os.path.join(OUT, stem + ".json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        done[name] = {"opened": stem + ".md", "created": doc.get("created"), "reason": doc.get("reason"),
                      "request_id": doc.get("request_id"), "counts": doc.get("counts")}
        got.append(name)
        print(f"✓ {name} → data/inbox_received/{stem}.md  ({doc.get('reason')}, {doc.get('created')}, "
              f"version {doc.get('version')}) counts={json.dumps(doc.get('counts'), ensure_ascii=False)}")
    save_seen(done)
    print(f"{len(got)} new file(s)")
    return got


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen"); k.add_argument("--force", action="store_true")
    sub.add_parser("setup-repo")
    p = sub.add_parser("pull"); p.add_argument("--again", action="store_true", help="open files already opened too")
    sub.add_parser("list")
    a = ap.parse_args()
    if a.cmd == "keygen":
        return keygen(a.force)
    if a.cmd == "setup-repo":
        return setup_repo()
    if a.cmd == "pull":
        return pull(a.again)
    done = seen()
    for item in listing():
        print(("opened  " if item["name"] in done else "new     ") + item["name"], item.get("size"))


if __name__ == "__main__":
    main()
