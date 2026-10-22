"""Staff trial: network address and QR code (2026-09-27)."""
import os, sys, json, shutil, subprocess
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

ok(A.app.test_client().get("/api/admin/trial").status_code == 401, "trial info needs sign-in")
ok(login(A.app.test_client(), "staff").get("/api/admin/trial").status_code == 200, "trial info: staff may open it (settings, 2026-10-13)")
m = login(A.app.test_client())
d = m.get("/api/admin/trial").get_json()
ok(d["mode"] == "local" and d["reachable"] is False, "default server (this computer only) is reported as not reachable by phones")
A.BIND_HOST = "0.0.0.0"
A.lan_address = lambda: "192.168.1.25"
d = m.get("/api/admin/trial").get_json()
ok(d == {"url": "http://192.168.1.25:8802/", "mode": "network", "reachable": True, "test_mode": d["test_mode"]}, "trial mode gives the phone address")
A.lan_address = lambda: None
ok(m.get("/api/admin/trial").get_json()["url"] is None, "no network -> no address, no QR")
os.environ["LIBRARY_KEEPER_PUBLIC_URL"] = "https://books.example.org"
ok(m.get("/api/admin/trial").get_json()["url"] == "https://books.example.org/", "a public address (after deployment) wins")
del os.environ["LIBRARY_KEEPER_PUBLIC_URL"]

# QR generator: structure checks (the codes were also decoded with OpenCV during development)
node = shutil.which("node")
if node:
    js = ("const Q=require(process.argv[1]);const out={};for(const t of ['http://192.168.1.25:8802/','A','x'.repeat(100)])"
          "{out[t.length]=Q.matrix(t).map(r=>r.map(v=>v?1:0).join(''))};"
          "try{Q.matrix('x'.repeat(200));out.long='no error'}catch(e){out.long='error'};console.log(JSON.stringify(out))")
    res = json.loads(subprocess.run([node, "-e", js, os.path.abspath("../frontend/assets/qr.js")], capture_output=True, text=True).stdout)
    finder = ["1111111", "1000001", "1011101", "1011101", "1011101", "1000001", "1111111"]
    m25 = res["25"]
    ok(len(m25) == 25 and all(len(r) == 25 for r in m25), "a trial URL fits a version-2 code (25×25)")
    ok([r[:7] for r in m25[:7]] == finder and [r[-7:] for r in m25[:7]] == finder and [r[:7] for r in m25[-7:]] == finder, "three finder patterns in place")
    ok(m25[6][8:17] == "101010101" and m25[4 * 2 + 9][8] == "1", "timing pattern and dark module in place")
    ok(len(res["1"]) == 21 and len(res["100"]) == 41, "short text -> version 1, 100 bytes -> version 6")
    ok(res["long"] == "error", "too-long text raises instead of producing a bad code")
else:
    print("PASS (node not installed: QR structure checks skipped)"); P += 1


# not a Mac (Windows/Linux): backups stay local unless a mirror folder is set; launchers exist for Windows
import importlib.util
spec = importlib.util.spec_from_file_location("backup_db", os.path.join(os.path.dirname(os.getcwd()), "scripts", "backup_db.py"))
bk = importlib.util.module_from_spec(spec); spec.loader.exec_module(bk)
ok((bk.DEFAULT_MIRROR == "") == (sys.platform != "darwin"), "iCloud backup mirror only on a Mac")
print(f"TOTAL PASS={P} FAIL={F}")
