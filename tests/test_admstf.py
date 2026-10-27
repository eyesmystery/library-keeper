"""ADMSTF (Ibrahim 2026-10-24): the developer role, the access changes and the reorganised admin page."""
import os, sys, re
sys.path.insert(0, os.getcwd())
import models, auth, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

con = models.get_connection()
# 1. migration: an old two-role table is rebuilt once, rows kept, ibrahim becomes the developer
con.execute("DROP TABLE staff_users")
con.execute("""CREATE TABLE staff_users (user_id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('manager', 'staff')), password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_login_at TEXT, password_changed_at TEXT, disabled_at TEXT)""")
con.execute("INSERT INTO staff_users (username, display_name, role, password_hash, created_at) VALUES ('ibrahim','إبراهيم','manager','x','2026-10-09')")
con.execute("INSERT INTO staff_users (username, display_name, role, password_hash, created_at) VALUES ('lib','مدير المكتبة','manager','y','2026-10-09')")
con.commit(); con.close()
models.init_db()
con = models.get_connection()
rows = {r["username"]: dict(r) for r in con.execute("SELECT * FROM staff_users")}
sql = con.execute("SELECT sql FROM sqlite_master WHERE name='staff_users'").fetchone()[0]
ok("'developer'" in sql, "role CHECK widened to developer")
ok(rows["ibrahim"]["role"] == "developer" and rows["lib"]["role"] == "manager", "ibrahim → developer, other managers untouched")
ok(rows["lib"]["password_hash"] == "y" and rows["ibrahim"]["created_at"] == "2026-10-09", "rows copied intact")
models.init_db()
ok(con.execute("SELECT COUNT(*) FROM staff_users").fetchone()[0] == 2, "migration runs once (second start changes nothing)")
con.execute("DELETE FROM staff_users"); con.commit()

# 2. roles offered by the page; developer never assignable
ok(auth.ROLE_LABELS["developer"] == "مطوّر" and "developer" not in auth.ASSIGNABLE_ROLES, "developer label, not assignable")
try:
    auth.create_user("x-dev", "x", "developer", "Strong-pass-9"); ok(False, "create developer refused")
except ValueError:
    ok(True, "create developer refused")
mgr, dev, st = login(A.app.test_client()), login(A.app.test_client(), "developer"), login(A.app.test_client(), "staff")
ok([r["value"] for r in mgr.get("/api/admin/users").get_json()["roles"]] == ["manager", "staff"], "users page offers manager and staff only")
dev_id = next(u["user_id"] for u in auth.list_users() if u["username"] == "test-developer")
ok(mgr.post(f"/api/admin/users/{dev_id}", json={"active": False}).status_code == 403, "a manager cannot disable the developer account")
ok(mgr.post(f"/api/admin/users/{dev_id}", json={"role": "staff"}).status_code == 403, "a manager cannot change the developer's role")
st_id = next(u["user_id"] for u in auth.list_users() if u["username"] == "test-staff")
ok(mgr.post(f"/api/admin/users/{st_id}", json={"role": "developer"}).status_code == 400, "nobody is promoted to developer from the page")

# 3. access: developer = manager + developer-only writes; staff lose accounts and AI settings
for path in ["/api/admin/stats", "/api/admin/import/history", "/api/admin/users", "/api/admin/llm", "/api/admin/usage"]:
    ok(dev.get(path).status_code == 200 and mgr.get(path).status_code == 200, f"manager and developer open {path}")
for path in ["/api/admin/users", "/api/admin/llm", "/api/admin/usage", "/api/admin/testlog"]:
    ok(st.get(path).status_code == 403, f"staff blocked from {path}")
for path in ["/api/admin/picks", "/api/admin/events", "/api/admin/inbox/requested", "/api/admin/catalog", "/api/admin/audit"]:
    ok(st.get(path).status_code == 200, f"staff still open {path}")
ok(st.post("/api/admin/authors/merge", json={}).status_code == 403 and mgr.post("/api/admin/authors/merge", json={}).status_code == 403,
   "spelling merge refused for staff and manager")
ok(dev.post("/api/admin/authors/merge", json={}).status_code == 400, "developer reaches the merge route (bad request only)")
ok(mgr.get("/api/admin/books/deleted").status_code == 200, "the deleted list stays readable")
s = dev.get("/api/admin/session").get_json()
ok(s["user"]["role"] == "developer" and s["user"]["role_label"] == "مطوّر", "session reports the developer role")
# 4. the reorganised page (ADMSTF 2026-10-24)
html = open(os.path.join(os.getcwd(), "..", "admin", "index.html"), encoding="utf-8").read()
import re as _re
rail = html[html.index('<nav id="adminNav"'):html.index('</nav>')]
ok(_re.findall(r'role="tab" id="(\w+)"', rail) == ["tabBooks", "tabAlerts", "tabReaders", "tabOrganize", "tabImport", "tabSettings"], "six main sections in order")
ok(rail.index('id="navQr"') < rail.index('id="tabBooks"') and rail.count('data-tip=') == 7, "QR first and prominent; every entry explains itself")
for gone in ['id="libTabConfirm"', 'id="libTabOrigin"', 'id="cxFold"', 'id="tabInbox"', 'id="tabStats"', 'id="tabPicks"', 'id="tabEvents"', 'id="tabRequested"', 'id="phGear"', "books:confirm"]:
    ok(gone not in html, f"removed: {gone}")
for dev in ['class="settings-tab dev-only dev-tab" role="tab" id="libTabDeleted"', 'dev-only dev-tab" role="tab" id="subQuestions"',
            '<th class="review-cell dev-only dev-col"', 'id="attrsFold"', 'الأسلوب في كتالوجنا <span class="dev-tag">']:
    ok(dev in html, f"developer-only, in its colour: {dev[:40]}")
ok("body:not(.is-dev) .dev-only{display:none!important}" in html and "--dev:" in html, "developer parts hidden from others, own colour")
for sub in ["subVisitors", "subStaff", "subDevNotes", "subStats", "subRequests", "subPicks", "subEvents", "subRewaa", "subSheet", "subHist", "setTabUpdates"]:
    ok(f'id="{sub}"' in html, f"sub-section {sub}")
ok(html.index('id="updBox"') > html.index('id="settingsUpdates"') and html.index('id="sendBox"') > html.index('id="settingsUpdates"'), "updates and sending live in «الإعدادات»")
card = html[html.index("<!-- ADMSTF order (Ibrahim 2026-10-24) -->"):]
order = [card.index(x) for x in ["<h3>المخزون والظهور</h3>", 'id="basicBlock"', "${confirmBlock(d)}", "<h3>الغلاف</h3>", "<h3>نبذة عن الكتاب</h3>", "${staffNotesBlock(d)}", 'aria-labelledby="nbTitle"']]
ok(order == sorted(order), "book card: stock first, «ليس كتابًا» last")
ok('<h4 class="sub-h">اللغة والترجمة</h4>' in html and "<h3>لغة الكتاب</h3>" not in html, "language joined the basic data")
ok("$('sortBy').value = store.get('sortBy', 'title')" in html, "books sorted by title by default")

# 5. fresh install and restore (Codex audit 2026-10-24 P2, AUDFIX): the first-run setup makes a manager even for the
#    name «ibrahim», a restart does not change it, and the role is given only on the computer by scripts/set_developer.py
import subprocess, socket, sqlite3, tempfile, shutil
from auth_helper import SignedIn
ROOT = os.path.dirname(os.getcwd())
SCRIPT = os.path.join(ROOT, "scripts", "set_developer.py")
def sd(*a):
    p = subprocess.run([sys.executable, SCRIPT, *a], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr
def role_of(u):
    c = models.get_connection()
    try:
        r = c.execute("SELECT role FROM staff_users WHERE username = ?", (u,)).fetchone()
        return r[0] if r else None
    finally:
        c.close()
def set_col(sql):
    c = models.get_connection(); c.execute(sql); c.commit(); c.close()
set_col("DELETE FROM staff_users")
c0 = A.app.test_client()
r = c0.post("/api/admin/setup", json={"username": "ibrahim", "name": "إبراهيم", "password": "Ibrahim-pass-1"})
ok(r.status_code == 200 and role_of("ibrahim") == "manager", "fresh install: setup as «ibrahim» makes a manager, not a developer")
me = SignedIn(c0, r.get_json()["csrf"])
models.init_db()
ok(role_of("ibrahim") == "manager", "fresh install: a restart (init_db) gives no developer role from the name")
ok(me.post("/api/admin/authors/merge", json={}).status_code == 403, "fresh install: before the script the merge route is refused (403)")
rc, out = sd("--grant", "nobody-here")
ok(rc == 1 and "no account named" in out and role_of("ibrahim") == "manager", "script refuses an account that does not exist")
rc, out = sd("--grant", "ibrahim")
ok(rc == 0 and "manager -> developer" in out and role_of("ibrahim") == "developer", "fresh install: set_developer.py --grant ibrahim gives the role")
ok(me.post("/api/admin/authors/merge", json={}).status_code == 400, "same session now reaches the developer-only merge route (bad request only)")
ok(me.get("/api/admin/session").get_json()["user"]["role"] == "developer", "session reports developer at once (no restart)")
con = models.get_connection()
aud = con.execute("SELECT username, target, details FROM audit_log WHERE action = 'user.developer_granted'").fetchall(); con.close()
ok(len(aud) == 1 and aud[0]["username"] == "set_developer.py" and aud[0]["target"] == "ibrahim" and '"developer"' in (aud[0]["details"] or ""),
   "the grant is in the audit log (who, account, before/after)")
rc, out = sd("--grant", "ibrahim")
ok(rc == 0 and "already" in out, "granting twice changes nothing")
models.init_db()
ok(role_of("ibrahim") == "developer", "a restart keeps the granted role")
rc, out = sd("--list")
ok(rc == 0 and "ibrahim" in out and "developer" in out, "--list shows accounts and roles")
mgr2 = login(A.app.test_client())
ib_id = next(u["user_id"] for u in auth.list_users() if u["username"] == "ibrahim")
ok(mgr2.post(f"/api/admin/users/{ib_id}", json={"role": "staff"}).status_code == 403, "a manager still cannot change the granted developer")
rc, out = sd("--revoke", "ibrahim")
ok(rc == 0 and role_of("ibrahim") == "manager" and me.post("/api/admin/authors/merge", json={}).status_code == 403,
   "--revoke moves the developer back to manager, effective at once")
set_col("UPDATE staff_users SET active = 0 WHERE username = 'ibrahim'")
rc, out = sd("--grant", "ibrahim")
ok(rc == 1 and role_of("ibrahim") == "manager", "a disabled account is refused")
set_col("UPDATE staff_users SET active = 1 WHERE username = 'ibrahim'")

# restore of a recent backup (new table) whose ibrahim is a manager: stays a manager, restore says how, the script gives it
def backup_to(path):
    a = sqlite3.connect(models.DB_PATH); b = sqlite3.connect(path); a.backup(b); b.close(); a.close()
bk_dir = os.path.join(ROOT, "backups"); os.makedirs(bk_dir, exist_ok=True)
bk = os.path.join(bk_dir, "library-20261024-230000.db")
backup_to(bk)
sd("--grant", "ibrahim")
with socket.socket() as s_:
    s_.bind(("127.0.0.1", 0)); free_port = s_.getsockname()[1]
mirror = tempfile.mkdtemp(prefix="lk-mirror-")
env = dict(os.environ, LIBRARY_KEEPER_PORT=str(free_port), LIBRARY_KEEPER_BACKUP_MIRROR=mirror)
def restore():
    return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "restore_db.py"), "--file", bk, "--no-assets"],
                          capture_output=True, text=True, env=env)
p = restore()
ok(p.returncode == 0 and role_of("ibrahim") == "manager", "restore of a recent backup: ibrahim is a manager as in the backup")
ok("set_developer.py --grant" in p.stdout, "restore says how to give the developer role when the backup has none")
models.init_db()
ok(role_of("ibrahim") == "manager", "restore: a restart gives no developer role from the name")
rc, out = sd("--grant", "ibrahim")
ok(rc == 0 and role_of("ibrahim") == "developer", "restore: set_developer.py --grant ibrahim gives the role")
backup_to(bk)
p = restore()
shutil.rmtree(mirror, ignore_errors=True)
ok(p.returncode == 0 and role_of("ibrahim") == "developer" and "set_developer.py" not in p.stdout,
   "restore of a backup that has the developer keeps it, with no note")
mr = open(os.path.join(ROOT, "scripts", "make_release.py"), encoding="utf-8").read()
ok('"set_developer.py"]' in mr[mr.index("SCRIPTS = ["):mr.index("CHANNEL_DIR")], "packages carry scripts/set_developer.py")
print(f"TOTAL PASS={P} FAIL={F}")
