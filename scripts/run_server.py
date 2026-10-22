"""Start «منار» and keep it running (AUTOUPD, 2026-10-16). The start files run this instead of app.py.

  - an update the manager approved in «التنبيهات» (data/update_pending.json): installed by scripts/auto_update.py
    while the server is stopped, then the server starts again on its own;
  - the server stopping because of an error: started again after a few seconds (at most 5 times in 10 minutes);
  - closing the window or Ctrl+C stops everything, as before.
The script is read into memory when it starts, so an update may replace it (and the start files) safely.
Usage: <app python> scripts/run_server.py      (the app's own Python, from the start file)
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
PENDING = os.path.join(ROOT, "data", "update_pending.json")
RESTART = os.path.join(ROOT, "data", "restart.flag")


def main():
    py = sys.executable
    env = dict(os.environ, LIBRARY_KEEPER_LAUNCHER="1", PYTHONUTF8="1")
    crashes = []
    while True:
        if os.path.exists(PENDING):
            print("\n=== تثبيت التحديث الذي وافق عليه المدير… ===", flush=True)
            subprocess.call([py, os.path.join(ROOT, "scripts", "auto_update.py")], cwd=ROOT, env=env)
        started = time.time()
        rc = subprocess.call([py, "app.py"], cwd=BACKEND, env=env)
        if os.path.exists(PENDING):
            continue                      # the server stopped itself to install an approved update
        if os.path.exists(RESTART):
            try:
                os.remove(RESTART)
            except OSError:
                pass
            continue
        if rc == 0:
            return 0
        crashes = [t for t in crashes if time.time() - t < 600] + [time.time()]
        if len(crashes) > 5:
            print("السيرفر توقف أكثر من ٥ مرات خلال ١٠ دقائق. اقرأ الأسطر أعلاه وأرسلها لإبراهيم.", flush=True)
            return rc
        print(f"السيرفر توقف (الرمز {rc}). يُعاد تشغيله بعد ٥ ثوانٍ…", flush=True)
        time.sleep(5 if time.time() - started > 5 else 10)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
