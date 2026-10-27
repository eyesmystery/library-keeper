"""Restore data/library.db from a backup.

Usage:
  backend/venv/bin/python scripts/restore_db.py --list
  backend/venv/bin/python scripts/restore_db.py --latest
  backend/venv/bin/python scripts/restore_db.py --file backups/library-YYYYMMDD-HHMMSS.db
  backend/venv/bin/python scripts/restore_db.py --assets-only      # only the picture files
  (add --no-assets to --latest / --file to restore the database alone)

Stop the server first (the script refuses while port 8802 is in use).
The chosen backup must pass PRAGMA integrity_check. The current database is kept as
data/library.db.before-restore-<time> before it is replaced, so a restore can be undone.

Picture files (since 2026-10-09): after the database, the files that scripts/backup_db.py copied are
put back into data/covers and data/event_images — only files that are MISSING there; a file that
exists is never replaced. Event pictures come from backups/event_images or the mirror, covers from
the mirror. The script then says how many events and books still point at a picture that is not there.
"""
import argparse
import datetime
import glob
import os
import shutil
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


ASSETS = ("covers", "event_images")
_URL = {"covers": "/covers/", "event_images": "/event-images/"}


def restore_assets():
    """Copy back the picture files missing from data/<folder>. -> {folder: number restored}."""
    done = {}
    for folder in ASSETS:
        dest = os.path.join(ROOT, "data", folder)
        n = 0
        for src_dir in (os.path.join(LOCAL, folder), os.path.join(MIRROR, folder) if MIRROR else ""):
            if not src_dir or not os.path.isdir(src_dir):
                continue
            os.makedirs(dest, exist_ok=True)
            for fname in os.listdir(src_dir):
                src, dst = os.path.join(src_dir, fname), os.path.join(dest, fname)
                if os.path.isfile(src) and not fname.endswith(".part") and not os.path.exists(dst):
                    shutil.copy2(src, dst)
                    n += 1
        done[folder] = n
    return done


def missing_pictures():
    """{folder: [file names the database points at that are not in data/<folder>]}."""
    out = {f: [] for f in ASSETS}
    if not os.path.exists(DB):
        return out
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        for folder, sql in (("covers", "SELECT cover_url FROM books WHERE cover_url LIKE '/covers/%'"),
                            ("event_images", "SELECT image_url FROM events WHERE image_url LIKE '/event-images/%'")):
            try:
                rows = con.execute(sql).fetchall()
            except sqlite3.OperationalError:  # an older database without that table or column
                continue
            for (url,) in rows:
                name = url.split(_URL[folder], 1)[1].split("?", 1)[0]
                if not os.path.isfile(os.path.join(ROOT, "data", folder, name)):
                    out[folder].append(name)
    finally:
        con.close()
    return out


def assets_step():
    done = restore_assets()
    print("pictures restored: " + ", ".join(f"{k} {v}" for k, v in done.items()))
    gone = missing_pictures()
    for folder, names in gone.items():
        if names:
            print(f"WARNING {len(names)} picture(s) named by the database are still missing from data/{folder}: "
                  + ", ".join(sorted(names)[:10]) + (" ..." if len(names) > 10 else ""))
    return not any(gone.values())


def developer_note():
    """After a restore (AUDFIX 2026-10-24): the developer role is kept as the backup has it and is never given by
    name. When the restored database has accounts but none is a developer, say how to give it explicitly."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'staff_users'").fetchone()
        if not row or "'developer'" not in (row[0] or ""):
            return  # no accounts yet, or an older database: the server's first start handles it (models.init_db)
        users = con.execute("SELECT COUNT(*) FROM staff_users").fetchone()[0]
        devs = con.execute("SELECT COUNT(*) FROM staff_users WHERE role = 'developer' AND active = 1").fetchone()[0]
    finally:
        con.close()
    if users and not devs:
        print("note: no active account has the developer role in this backup. To give it to an account: "
              "scripts/set_developer.py --grant <username>")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--latest", action="store_true")
    g.add_argument("--file")
    g.add_argument("--assets-only", action="store_true")
    ap.add_argument("--no-assets", action="store_true")
    args = ap.parse_args()
    if args.assets_only:
        assets_step()
        return 0
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
    developer_note()
    if not args.no_assets:
        assets_step()
    return 0 if ok2 == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
