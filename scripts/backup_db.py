"""Back up the live database (and a Git bundle of the code).

Usage:  backend/venv/bin/python scripts/backup_db.py [--reason TEXT]

1. A consistent copy of data/library.db is made with SQLite's backup API (safe while the
   server is running), checked with PRAGMA integrity_check, and saved as
   backups/library-YYYYMMDD-HHMMSS.db inside the project (ignored by Git).
2. The same file is mirrored to an off-device folder (iCloud Drive by default):
   ~/Library/Mobile Documents/com~apple~CloudDocs/Projects/Apps/Library Keeper Backups
   Override with LIBRARY_KEEPER_BACKUP_MIRROR (empty string = no mirror).
3. A Git bundle of all commits is written next to the mirror copy (code backup).
4. Picture files are copied (new or changed files only, nothing is ever deleted from a copy):
   data/covers -> <mirror>/covers; data/event_images -> <mirror>/event_images AND backups/event_images
   (event pictures are few and exist nowhere else, so they also get a copy on this computer, which is
   the only copy when there is no mirror). scripts/restore_db.py puts them back.
5. Keeps the newest 30 database backups in each folder; older ones are removed.
A line is appended to backups/backup_log.txt for every run. Exit code 0 = local backup OK.
"""
import argparse
import datetime
import glob
import os
import shutil
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "library.db")
LOCAL = os.path.join(ROOT, "backups")
# iCloud Drive mirror on a Mac only; on Windows/Linux backups stay in backups/ unless
# LIBRARY_KEEPER_BACKUP_MIRROR points at another folder (e.g. a OneDrive folder).
DEFAULT_MIRROR = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/Projects/Apps/Library Keeper Backups") if sys.platform == "darwin" else ""
KEEP = 30


def log(msg):
    os.makedirs(LOCAL, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(LOCAL, "backup_log.txt"), "a", encoding="utf-8") as f:
        f.write(f"{stamp}  {msg}\n")
    print(msg)


def snapshot(dest):
    # A leftover journal from an interrupted write (a "hot journal") must be rolled back before
    # the file can be read; a read-only connection cannot do that, so open read-write then.
    hot = os.path.exists(DB + "-journal") and os.path.getsize(DB + "-journal") > 0
    src = sqlite3.connect(DB) if hot else sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
        ok = out.execute("PRAGMA integrity_check").fetchone()[0]
        books = out.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    finally:
        out.close()
        src.close()
    if ok != "ok":
        os.remove(dest)
        raise RuntimeError(f"integrity check failed: {ok}")
    return books


def prune(folder, pattern):
    files = sorted(glob.glob(os.path.join(folder, pattern)))
    for old in files[:-KEEP]:
        os.remove(old)


ASSETS = ("covers", "event_images")   # folders under data/ that hold picture files named by the database


def copy_assets(src_dir, dst_dir):
    """Copy new or changed files (same rule as always for covers). -> number copied."""
    if not os.path.isdir(src_dir):
        return None
    os.makedirs(dst_dir, exist_ok=True)
    copied = 0
    for fname in os.listdir(src_dir):
        src = os.path.join(src_dir, fname)
        dst = os.path.join(dst_dir, fname)
        if not os.path.isfile(src) or fname.endswith(".part"):
            continue
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src) \
                or os.path.getmtime(dst) < os.path.getmtime(src):
            shutil.copy2(src, dst)
            copied += 1
    return copied


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", default="manual")
    args = ap.parse_args()
    if not os.path.exists(DB):
        log("SKIP no database at data/library.db")
        return 0
    os.makedirs(LOCAL, exist_ok=True)
    name = "library-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".db"
    local_path = os.path.join(LOCAL, name)
    try:
        books = snapshot(local_path)
    except Exception as e:  # noqa: BLE001 - report every failure plainly
        log(f"FAIL local backup ({args.reason}): {e}")
        return 1
    prune(LOCAL, "library-*.db")
    log(f"OK local {name} ({books} books, {os.path.getsize(local_path)} bytes, reason: {args.reason})")
    try:  # event pictures: a copy on this computer too (audit 2026-10-08)
        n = copy_assets(os.path.join(ROOT, "data", "event_images"), os.path.join(LOCAL, "event_images"))
        if n is not None:
            log(f"OK local event_images ({n} new or changed)")
    except Exception as e:  # noqa: BLE001 - the database backup itself is fine
        log(f"WARN local event_images copy failed: {e}")

    mirror = os.environ.get("LIBRARY_KEEPER_BACKUP_MIRROR", DEFAULT_MIRROR)
    if mirror:
        try:
            os.makedirs(mirror, exist_ok=True)
            shutil.copy2(local_path, os.path.join(mirror, name))
            prune(mirror, "library-*.db")
            log(f"OK mirror {mirror}")
        except Exception as e:  # noqa: BLE001 - the local copy still exists
            log(f"WARN mirror failed ({mirror}): {e}")
            return 0
        for folder in ASSETS:  # picture files: copy new or changed files only; nothing is deleted in the mirror
            try:
                n = copy_assets(os.path.join(ROOT, "data", folder), os.path.join(mirror, folder))
                if n is not None:
                    log(f"OK {folder} ({n} new or changed)")
            except Exception as e:  # noqa: BLE001
                log(f"WARN {folder} mirror failed: {e}")
        try:
            has_commits = shutil.which("git") and subprocess.run(
                ["git", "-C", ROOT, "rev-parse", "--verify", "HEAD"], capture_output=True).returncode == 0
            if has_commits:
                bundle = os.path.join(mirror, "code.bundle")
                subprocess.run(["git", "-C", ROOT, "bundle", "create", bundle + ".tmp", "--all"],
                               check=True, capture_output=True)
                os.replace(bundle + ".tmp", bundle)
                log("OK code bundle")
        except Exception as e:  # noqa: BLE001
            log(f"WARN code bundle failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
