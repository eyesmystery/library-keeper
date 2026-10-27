"""Give or take back the developer role — the explicit, trusted way (AUDFIX 2026-10-24, Codex audit P2).

The developer role (ADMSTF 2026-10-24) is Ibrahim's own account. The admin page never gives it and the
first-run setup always makes a manager. An OLD database (two-role staff_users table) gets it once, when the
server first starts after the upgrade: backend/models.py moves the existing account 'ibrahim' to developer.
A fresh install, a package published with --no-user, or the restore of a recent backup whose account is a
manager does NOT get it — on purpose: a name typed on the page never earns the role by itself.

This script is the way for those cases. It runs on the computer itself, by whoever has the project folder
(the same trust as scripts/restore_db.py and the secret key in ~/.library_keeper), and it must name the account.

Usage (Mac; on Windows: backend\\venv-win\\Scripts\\python.exe scripts\\set_developer.py ...):
  backend/venv/bin/python scripts/set_developer.py --list
  backend/venv/bin/python scripts/set_developer.py --grant ibrahim
  backend/venv/bin/python scripts/set_developer.py --revoke ibrahim     # back to manager

The account must already exist and be active (create it with the first-run page or the users page first).
The change works at once, also while the server runs (every request re-reads the account), and is written
to the audit log («سجل التعديلات») as user.developer_granted / user.developer_revoked.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import auth  # noqa: E402
import models  # noqa: E402

ACTOR = {"user_id": None, "username": "set_developer.py"}


def _accounts(con):
    return [dict(r) for r in con.execute("SELECT user_id, username, display_name, role, active FROM staff_users ORDER BY user_id")]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Give or take back the developer role (run on this computer).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="show the accounts and their roles")
    g.add_argument("--grant", metavar="USERNAME", help="give this existing, active account the developer role")
    g.add_argument("--revoke", metavar="USERNAME", help="move this developer account back to manager")
    args = ap.parse_args(argv)

    if not os.path.exists(models.DB_PATH):
        print("no database at data/library.db — start the server once first")
        return 1
    con = models.get_connection()
    try:
        row = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'staff_users'").fetchone()
        if not row:
            print("no accounts yet — start the server and create the first account on the admin page first")
            return 1
        if args.list:
            for a in _accounts(con):
                print(f"{a['username']:<24} {a['role']:<10} {'active' if a['active'] else 'disabled'}  ({a['display_name']})")
            return 0
        if "'developer'" not in (row[0] or ""):
            print("this database is from before the developer role — start the server once (it updates the database), then run again")
            return 1
        name = (args.grant or args.revoke).strip()
        acc = con.execute("SELECT user_id, username, role, active FROM staff_users WHERE username = ?", (name,)).fetchone()
        if not acc:
            print(f"no account named {name!r} — existing: " + ", ".join(a["username"] for a in _accounts(con)))
            return 1
        before = acc["role"]
        if args.grant:
            if not acc["active"]:
                print(f"{name!r} is disabled — enable it first")
                return 1
            if before == "developer":
                print(f"{name!r} already has the developer role")
                return 0
            after, action = "developer", "user.developer_granted"
        else:
            if before != "developer":
                print(f"{name!r} is not a developer ({before}) — nothing to take back")
                return 0
            after, action = "manager", "user.developer_revoked"
        con.execute("UPDATE staff_users SET role = ? WHERE user_id = ?", (after, acc["user_id"]))
        con.commit()
    finally:
        con.close()
    auth.audit(ACTOR, action, name, {"before": {"role": before}, "after": {"role": after}})
    print(f"{name}: {before} -> {after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
