"""Restore data/library.db from a backup.

Usage:
  backend/venv/bin/python scripts/restore_db.py --list
  backend/venv/bin/python scripts/restore_db.py --latest
  backend/venv/bin/python scripts/restore_db.py --file backups/library-YYYYMMDD-HHMMSS.db

Stop the server first (the script refuses while port 8802 is in use).
The chosen backup must pass PRAGMA integrity_check. The current database is kept as
data/library.db.before-restore-<time> before it is replaced, so a restore can be undone.
"""
import argparse
import datetime
import glob
import os
import socket
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "library.db")
LOCAL = os.path.join(ROOT, "backups")
MIRROR = os.environ.get("LIBRARY_KEEPER_BACKUP_MIRROR", os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/Projects/Apps/Library Keeper Backups"))
PORT = int(os.environ.get("LIBRARY_KEEPER_PORT", "8802"))


def backups():
    found = {}
    for folder in (LOCAL, MIRROR):
        for p in glob.glob(os.path.join(folder, "library-*.db")):
            found.setdefault(os.path.basename(p), p)  # local copy preferred
    return [found[k] for k in sorted(found, reverse=True)]


def check(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        books = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    finally:
        con.close()
    return ok, books


def server_running():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def copy_db(src, dest):
    a = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    b = sqlite3.connect(dest)
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--latest", action="store_true")
    g.add_argument("--file")
    args = ap.parse_args()
    items = backups()
    if args.list:
        for p in items:
            ok, books = check(p)
            print(f"{os.path.basename(p)}  {books} books  integrity={ok}  ({os.path.dirname(p)})")
        if not items:
            print("no backups found")
        return 0
    if server_running():
        print(f"Stop the server first (something is listening on port {PORT}).")
        return 1
    src = items[0] if args.latest and items else args.file
    if not src or not os.path.exists(src):
        print("backup not found")
        return 1
    ok, books = check(src)
    if ok != "ok":
        print(f"refusing: backup failed integrity check ({ok})")
        return 1
    if os.path.exists(DB):
        keep = DB + ".before-restore-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        copy_db(DB, keep)
        print(f"current database kept as {os.path.relpath(keep, ROOT)}")
    tmp = DB + ".restoring"
    copy_db(src, tmp)
    os.replace(tmp, DB)
    ok2, books2 = check(DB)
    print(f"restored {os.path.basename(src)} -> data/library.db ({books2} books, integrity={ok2})")
    return 0 if ok2 == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
