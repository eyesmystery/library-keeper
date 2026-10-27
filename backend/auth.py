"""Staff accounts, sessions, roles, CSRF, login throttling and the audit log (approved 2026-09-27).

- Roles: manager (everything), staff (catalog, cards, review, inventory edits) and developer (ADMSTF 2026-10-24:
  Ibrahim's own account — everything a manager has plus the developer-only views; never assignable from the page).
- Passwords: hashlib.scrypt with a random salt (standard library; no new dependency).
- Sessions: Flask signed cookie (HttpOnly, SameSite=Strict, Secure in production), 12 h.
  The signing key lives OUTSIDE the project: ~/.library_keeper/secret_key (600), or
  LIBRARY_KEEPER_SECRET_KEY. Every request re-reads the account, so disabling a user or
  changing a role takes effect immediately.
- CSRF: every state-changing request to a protected route must send X-CSRF-Token equal to
  the session token (the admin page does this automatically).
- Login throttling: 5 failures for the same user or address -> 5 minute lock.
- Audit log: who did what and when (never passwords, keys or visitor text).
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone

from models import get_connection

ROLES = ("manager", "staff", "developer")
ROLE_LABELS = {"manager": "مدير", "staff": "موظف", "developer": "مطوّر"}
# «دور المطوّر» (Ibrahim 2026-10-24, ADMSTF): given only by the one-time migration of an old database (his own
# account) or explicitly on the computer by scripts/set_developer.py (fresh install / restore, AUDFIX 2026-10-24);
# the users page and the first-run setup can only give manager or staff.
ASSIGNABLE_ROLES = ("manager", "staff")
MANAGER_ROLES = ("manager", "developer")


def is_manager(role):
    return role in MANAGER_ROLES


def is_developer(role):
    return role == "developer"
SESSION_HOURS = 12
MIN_PASSWORD = 8
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
SECRET_PATH = os.environ.get("LIBRARY_KEEPER_SECRET_FILE",
                             os.path.join(os.path.expanduser("~"), ".library_keeper", "secret_key"))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def secret_key():
    env = os.environ.get("LIBRARY_KEEPER_SECRET_KEY")
    if env:
        return env
    try:
        with open(SECRET_PATH, encoding="utf-8") as f:
            key = f.read().strip()
            if len(key) >= 32:
                return key
    except OSError:
        pass
    os.makedirs(os.path.dirname(SECRET_PATH), mode=0o700, exist_ok=True)
    key = secrets.token_urlsafe(48)
    fd = os.open(SECRET_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key)
    return key


# ---------------------------------------------------------------- passwords

def hash_password(password):
    salt = os.urandom(16)
    n, r, p = 2 ** 14, 8, 1
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password, stored):
    try:
        _, n, r, p, salt, digest = stored.split("$")
        calc = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r),
                              p=int(p), dklen=len(base64.b64decode(digest)))
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def check_password_rules(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return f"كلمة المرور لازم تكون {MIN_PASSWORD} أحرف أو أكثر"
    if password.isdigit() or password.isalpha():
        return "كلمة المرور لازم تجمع حروف وأرقام"
    return None


# ---------------------------------------------------------------- users

def _row(r):
    return None if r is None else {k: r[k] for k in r.keys() if k != "password_hash"}


def count_users():
    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM staff_users").fetchone()[0]
    finally:
        conn.close()


def list_users():
    conn = get_connection()
    try:
        return [_row(r) for r in conn.execute("SELECT * FROM staff_users ORDER BY user_id")]
    finally:
        conn.close()


def get_user(user_id):
    conn = get_connection()
    try:
        return _row(conn.execute("SELECT * FROM staff_users WHERE user_id = ?", (user_id,)).fetchone())
    finally:
        conn.close()


def create_user(username, display_name, role, password):
    username = (username or "").strip().lower()
    display_name = (display_name or "").strip()[:60]
    if not _USER_RE.match(username):
        raise ValueError("اسم المستخدم: 3–32 حرف إنجليزي أو رقم أو . _ -")
    if role not in ASSIGNABLE_ROLES:
        raise ValueError("دور غير معروف")
    if not display_name:
        raise ValueError("اكتب الاسم الظاهر")
    problem = check_password_rules(password)
    if problem:
        raise ValueError(problem)
    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM staff_users WHERE username = ?", (username,)).fetchone():
            raise ValueError("اسم المستخدم مستخدم من قبل")
        cur = conn.execute("""INSERT INTO staff_users (username, display_name, role, password_hash, active, created_at)
                              VALUES (?, ?, ?, ?, 1, ?)""", (username, display_name, role, hash_password(password), _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_user(user_id, *, role=None, active=None, password=None, display_name=None):
    user = get_user(user_id)
    if user is None:
        raise LookupError("user not found")
    sets, args = [], []
    if role is not None:
        if role not in ASSIGNABLE_ROLES:
            raise ValueError("دور غير معروف")
        sets.append("role = ?"); args.append(role)
    if active is not None:
        sets.append("active = ?"); args.append(1 if active else 0)
        sets.append("disabled_at = ?"); args.append(None if active else _now())
    if display_name is not None:
        if not display_name.strip():
            raise ValueError("اكتب الاسم الظاهر")
        sets.append("display_name = ?"); args.append(display_name.strip()[:60])
    if password is not None:
        problem = check_password_rules(password)
        if problem:
            raise ValueError(problem)
        sets.append("password_hash = ?"); args.append(hash_password(password))
        sets.append("password_changed_at = ?"); args.append(_now())
    if not sets:
        return
    conn = get_connection()
    try:
        # never leave the system without an active manager
        if (role == "staff" or active is False) and user["role"] == "manager":
            others = conn.execute("SELECT COUNT(*) FROM staff_users WHERE role = 'manager' AND active = 1 AND user_id != ?",
                                  (user_id,)).fetchone()[0]
            if others == 0:
                raise ValueError("لازم يبقى مدير واحد فعّال على الأقل")
        conn.execute(f"UPDATE staff_users SET {', '.join(sets)} WHERE user_id = ?", (*args, user_id))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- login throttling

_failures = {}  # key -> [count, first_at, locked_until]
LOCK_AFTER, LOCK_SECONDS, WINDOW = 5, 300, 900


def locked(keys):
    now = time.time()
    return any(_failures.get(k, [0, 0, 0])[2] > now for k in keys)


def note_failure(keys):
    now = time.time()
    for k in keys:
        count, first, until = _failures.get(k, [0, now, 0])
        if now - first > WINDOW:
            count, first = 0, now
        count += 1
        _failures[k] = [count, first, now + LOCK_SECONDS if count >= LOCK_AFTER else until]


def clear_failures(keys):
    for k in keys:
        _failures.pop(k, None)


def authenticate(username, password, address):
    """-> (user or None, error message)."""
    username = (username or "").strip().lower()
    keys = ("u:" + username, "a:" + str(address))
    if locked(keys):
        return None, "محاولات كثيرة. انتظر 5 دقائق ثم حاول مرة ثانية."
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM staff_users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()
    ok = r is not None and verify_password(password or "", r["password_hash"])
    if not ok or not r["active"]:
        if r is None:  # same work as a real check, so timing does not reveal which names exist
            verify_password(password or "", hash_password("x" * 12))
        note_failure(keys)
        return None, "اسم المستخدم أو كلمة المرور غير صحيحة" if not (r is not None and ok) else "الحساب موقوف"
    clear_failures(keys)
    conn = get_connection()
    try:
        conn.execute("UPDATE staff_users SET last_login_at = ? WHERE user_id = ?", (_now(), r["user_id"]))
        conn.commit()
    finally:
        conn.close()
    return _row(r), None


# ---------------------------------------------------------------- audit log

def audit(user, action, target=None, details=None):
    conn = get_connection()
    try:
        conn.execute("""INSERT INTO audit_log (at, user_id, username, action, target, details)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                     (_now(), user.get("user_id") if user else None, user.get("username") if user else None,
                      action, None if target is None else str(target)[:120],
                      json.dumps(details, ensure_ascii=False)[:6000] if details else None))  # room for a whole summary before/after (2026-10-09)
        conn.commit()
    finally:
        conn.close()


def recent_audit(limit=100):
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY log_id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()
