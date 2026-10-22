"""[AUTOUPD] The whole chain with real servers (not in run_all.py: it starts a fake GitHub and the app twice).

  backend/venv/bin/python tests/e2e_autoupdate.py [port]

A throwaway copy of the project (run_all.fresh_copy) is started the way the library starts it, through
scripts/run_server.py, on 127.0.0.1:<port> (default 8836; never 8802). A local fake of GitHub's release API serves a
signed channel file (a test key, not Ibrahim's) and a real update package built here from the same code with
VERSION «0.9.99 beta» and a marker file. The manager signs in, «تحقق الآن», «موافقة وتحديث»: the app downloads and
checks the file, runs the dry run, stops itself; run_server.py installs it with apply_update.py and starts the server
again; the page then shows «تم التحديث». Then a second, broken update is approved and must stop at the dry run with
nothing changed.
"""
import hashlib, http.cookiejar, http.server, json, os, shutil, signal, socket, sqlite3, subprocess, sys, tempfile, threading, time, urllib.request, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests")); sys.path.insert(0, os.path.join(ROOT, "backend")); sys.path.insert(0, os.path.join(ROOT, "scripts"))
import run_all, ed25519, apply_update  # noqa: E402
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8836
assert PORT != 8802
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg, flush=True); P += bool(cond); F += (not cond)

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def build_update(app, work, version, broken=False):
    pkg = os.path.join(work, f"ThalathSmart-update-{apply_update_tag(version)}")
    payload = os.path.join(pkg, "payload")
    for d in ("backend", "frontend", "admin", "scripts"):
        shutil.copytree(os.path.join(app, d), os.path.join(payload, d), ignore=shutil.ignore_patterns("__pycache__", "venv", "venv-win"))
    open(os.path.join(payload, "VERSION"), "w").write(version + "\n")
    open(os.path.join(payload, "BUILD.json"), "w").write("{}\n")
    open(os.path.join(payload, "backend", "E2E_MARKER.txt"), "w").write(version)
    if broken:
        open(os.path.join(pkg, "catalog.db"), "w").write("not a database")
    else:
        apply_update.build_catalog(os.path.join(app, "data", "library.db"), os.path.join(pkg, "catalog.db"), {"version": version})
    os.makedirs(os.path.join(pkg, "bases")); os.makedirs(os.path.join(pkg, "covers"))
    shutil.copy2(os.path.join(ROOT, "scripts", "apply_update.py"), os.path.join(pkg, "apply_update.py"))
    zp = pkg + ".zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _d, files in os.walk(pkg):
            for f in files:
                full = os.path.join(base, f); z.write(full, os.path.relpath(full, work))
    return zp

def apply_update_tag(v): return v.replace(" beta", "b").replace(" ", "")

def main():
    tmp = tempfile.mkdtemp(prefix="lk-e2e-autoupd-"); app = os.path.join(tmp, "app"); work = os.path.join(tmp, "work")
    os.makedirs(app); os.makedirs(work)
    run_all.fresh_copy(app)
    sk = os.urandom(32); pk = ed25519.public_key(sk).hex()
    open(os.path.join(app, "backend", "channel_key.py"), "w").write(f'PUBLIC_KEY = "{pk}"\n')
    fake = free_port(); base = f"http://127.0.0.1:{fake}"
    json.dump({"repo": "t/r", "tag": "manar-channel", "api": base, "enabled": True}, open(os.path.join(app, "data", "channel.json"), "w"))
    files = {}
    seq = [0]
    def publish(update):
        seq[0] += 1
        text = json.dumps({"seq": seq[0], "messages": [{"ref": "DN-e2e", "title": "رسالة تجربة من القناة", "body": "x"}], "update": update}, ensure_ascii=False, sort_keys=True)
        files["manar-channel.json"] = json.dumps({"format": "manar-channel-1", "payload": text, "signature": ed25519.sign(sk, text.encode()).hex()}).encode()
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path == "/repos/t/r/releases/tags/manar-channel":
                body = json.dumps({"assets": [{"name": n, "url": f"{base}/dl/{n}", "browser_download_url": f"{base}/dl/{n}", "size": len(b)} for n, b in files.items()]}).encode()
            elif self.path.startswith("/dl/") and self.path[4:] in files:
                body = files[self.path[4:]]
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", fake), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    v1 = "0.9.99 beta"
    zp = build_update(app, work, v1)
    data = open(zp, "rb").read(); files[os.path.basename(zp)] = data
    publish({"version": v1, "asset": os.path.basename(zp), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "notes": "تحديث تجربة"})
    env = {k: v for k, v in os.environ.items() if k not in run_all.KEY_VARS}
    env.update(LIBRARY_KEEPER_LLM="off", LIBRARY_KEEPER_OUTSIDE="0", PYTHONDONTWRITEBYTECODE="1", LIBRARY_KEEPER_PORT=str(PORT),
               LIBRARY_KEEPER_SECRETS=os.path.join(tmp, "no-keys", "keys.json"), LIBRARY_KEEPER_SECRET_FILE=os.path.join(tmp, "no-keys", "secret_key"),
               LIBRARY_KEEPER_BACKUP_MIRROR="")
    py = os.path.join(ROOT, "backend", "venv", "bin", "python")
    subprocess.run([py, "-c", "import models; models.init_db(); import auth; auth.create_user('boss', 'المدير', 'manager', 'Manager-pass-1')"],
                   cwd=os.path.join(app, "backend"), env=env, check=True)
    log = open(os.path.join(tmp, "launcher.log"), "w")
    launcher = subprocess.Popen([py, os.path.join(app, "scripts", "run_server.py")], cwd=app, env=env, stdout=log, stderr=log, start_new_session=True)
    B = f"http://127.0.0.1:{PORT}"
    jar = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    csrf = {}
    def call(path, body=None, timeout=60):
        h = {"Content-Type": "application/json"}
        if body is not None and csrf.get("t"): h["X-CSRF-Token"] = csrf["t"]
        req = urllib.request.Request(B + path, data=None if body is None else json.dumps(body).encode(), headers=h, method="GET" if body is None else "POST")
        try:
            with op.open(req, timeout=timeout) as r: return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    def wait_up(limit=60):
        for _ in range(limit * 4):
            try:
                urllib.request.urlopen(B + "/", timeout=1); return True
            except Exception: time.sleep(0.25)
        return False
    try:
        ok(wait_up(), "the app starts through run_server.py")
        st, r = call("/api/admin/login", {"username": "boss", "password": "Manager-pass-1"}); csrf["t"] = r.get("csrf")
        ok(st == 200, "the manager signs in")
        st, s = call("/api/admin/inbox/channel/check", {})
        ok(s.get("update", {}).get("status") == "available" and s["update"]["version"] == v1, "«تحقق الآن» finds the signed update")
        st, r = call("/api/admin/inbox/dev-notes?status=all")
        ok(any(n["ref"] == "DN-e2e" for n in r.get("notes", [])), "and the channel's message is in «ملاحظات من المطور»")
        st, r = call("/api/admin/channel/install", {"version": v1})
        ok(st == 200, "«موافقة وتحديث» is accepted")
        seen, final, t0 = set(), None, time.time()
        while time.time() - t0 < 300:
            try:
                st, s = call("/api/admin/inbox/channel", timeout=5)
                seen.add(s.get("update", {}).get("status"))
                if s.get("update", {}).get("status") in ("installed", "failed") and "restarting" in seen:
                    final = s; break
            except Exception:
                seen.add("down")
            time.sleep(1)
        ok(final and final["update"]["status"] == "installed", f"installed after a restart (states seen: {sorted(x for x in seen if x)})"
           + ("" if final and final["update"]["status"] == "installed" else f" — {final and final['update'].get('message')}"))
        log.flush()
        _lt = open(os.path.join(tmp, "launcher.log"), encoding="utf-8", errors="replace").read()
        ok(_lt.count("Running on") >= 2 and "Update installed: " + v1 in _lt, "the server really stopped, the update was installed, and it started again")
        ok(open(os.path.join(app, "VERSION")).read().strip() == v1 and os.path.exists(os.path.join(app, "backend", "E2E_MARKER.txt"))
           and final and final.get("installed_version") == v1, "the new code and version are in place and running")
        con = sqlite3.connect(os.path.join(app, "data", "library.db"))
        acts = [r[0] for r in con.execute("SELECT action FROM audit_log WHERE action LIKE 'update.%' ORDER BY rowid")]
        con.close()
        ok(acts[-2:] == ["update.approved", "update.installed"], f"the audit log has the approval and the install ({acts})")
        ok(any(n.startswith("before_update_") for n in os.listdir(os.path.join(app, "backups"))), "a backup was taken before the install")
        # a broken update: stops at the dry run, nothing changes, no restart
        v2 = "0.9.100 beta"
        zp2 = build_update(app, work, v2, broken=True); d2 = open(zp2, "rb").read(); files[os.path.basename(zp2)] = d2
        publish({"version": v2, "asset": os.path.basename(zp2), "sha256": hashlib.sha256(d2).hexdigest(), "size": len(d2), "notes": "تحديث مكسور"})
        st, s = call("/api/admin/inbox/channel/check", {})
        st, r = call("/api/admin/channel/install", {"version": v2})
        t0, s = time.time(), None
        while time.time() - t0 < 120:
            st, s = call("/api/admin/inbox/channel", timeout=5)
            if s["update"]["status"] in ("failed", "restarting", "installed"): break
            time.sleep(1)
        ok(s and s["update"]["status"] == "failed" and open(os.path.join(app, "VERSION")).read().strip() == v1
           and not os.path.exists(os.path.join(app, "data", "update_pending.json")), "a broken update stops at the dry run; nothing changed")
        # a file signed with another key is refused
        files["manar-channel.json"] = json.dumps({"format": "manar-channel-1", "payload": json.dumps({"seq": 99, "update": None}),
                                                  "signature": ed25519.sign(os.urandom(32), json.dumps({"seq": 99, "update": None}).encode()).hex()}).encode()
        st, s = call("/api/admin/inbox/channel/check", {})
        ok("توقيع" in (s.get("last_error") or ""), "a channel file signed with another key is refused")
    finally:
        try:
            os.killpg(launcher.pid, signal.SIGTERM)
        except Exception: pass
        time.sleep(1); srv.shutdown()
    print(f"\n{P} passed, {F} failed  (log: {tmp}/launcher.log)")
    return 1 if F else 0

if __name__ == "__main__":
    sys.exit(main())
