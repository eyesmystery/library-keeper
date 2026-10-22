"""Install an update the manager approved in «التنبيهات» (AUTOUPD, 2026-10-16). Run by scripts/run_server.py while
the server is stopped; never by hand.

data/update_pending.json (written by backend/channel.py after the download, the checksum and a dry run passed) names
the unpacked update folder. Its own apply_update.py installs it — backup first, catalog merge, code swap, and the
whole installation put back as it was if any step fails. The outcome goes to data/update_result.json, which the app
reads when it starts again («تم التحديث» / «فشل… ورجعنا للنسخة السابقة»); the full output to updates/auto_update_*.log.
"""
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PENDING = os.path.join(ROOT, "data", "update_pending.json")
RESULT = os.path.join(ROOT, "data", "update_result.json")
UPDATES = os.path.join(ROOT, "updates")


def port_busy(port):
    s = socket.socket()
    s.settimeout(1.0)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    try:
        with open(PENDING, encoding="utf-8") as f:
            job = json.load(f)
    except (OSError, ValueError):
        return 0
    os.makedirs(UPDATES, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    log = os.path.join(UPDATES, f"auto_update_{ts}.log")
    port = int(os.environ.get("LIBRARY_KEEPER_PORT", "8802"))
    res = {"version": job.get("version"), "from_version": job.get("from_version"), "by": job.get("by"),
           "approved_at": job.get("at"), "log": os.path.relpath(log, ROOT), "ok": False}
    try:
        upd = job.get("update_dir") or ""
        script = os.path.join(upd, "apply_update.py")
        if not os.path.isfile(script):
            raise RuntimeError("مجلد التحديث غير موجود")
        for _ in range(60):               # the server is closing
            if not port_busy(port):
                break
            time.sleep(1)
        else:
            raise RuntimeError("السيرفر ما زال يعمل؛ لم يُثبَّت التحديث")
        with open(log, "w", encoding="utf-8") as out:
            rc = subprocess.call([sys.executable, script, "--app", ROOT, "--update", upd, "--port", str(port)],
                                 cwd=upd, stdout=out, stderr=subprocess.STDOUT)
        with open(log, encoding="utf-8", errors="replace") as f:
            text = f.read()
        print(text[-3000:], flush=True)
        res["ok"] = rc == 0
        if rc != 0:
            res["error"] = " ".join(text.strip().splitlines()[-3:])[-500:]
        rep = [ln.strip() for ln in text.splitlines() if ln.strip().endswith(".md")]
        res["report_file"] = rep[-1] if rep else None
    except Exception as e:  # noqa: BLE001 - always leave a result and never loop on the same job
        res["error"] = str(e)[:500]
    res["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_json(RESULT, res)
    try:
        os.replace(PENDING, os.path.join(UPDATES, f"update_pending_{ts}.json"))
    except OSError:
        pass
    print(("✓ تم التحديث إلى " + str(res["version"])) if res["ok"] else ("✗ لم يتم التحديث: " + str(res.get("error"))), flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
