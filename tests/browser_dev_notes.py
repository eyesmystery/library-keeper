"""[QADFIX4] Browser check of the admin «التنبيهات» tab and «ملاحظات من المطور» (Ibrahim 2026-10-16).

  ~/.library_keeper/pw-venv/bin/python tests/browser_dev_notes.py [port]

Not in tests/run_all.py (needs Playwright's Chromium in the separate pw-venv). A throwaway copy of the project
(run_all.fresh_copy: frozen test catalog, model off) with two books put under «دار اسفار» and a staff account,
served on 127.0.0.1:<port> (default 8834; never 8802). A staff member signs in on a phone-sized screen, opens
«التنبيهات», sees the developer note, opens its list of Asfar books, opens a book card from it, marks the note
done with a comment, and sees it under «تمّت». No page errors on the way.
"""
import os, subprocess, sys, tempfile, time, urllib.request, sqlite3, re
ROOT = os.environ.get("LK_TEST_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import run_all  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8834
assert PORT != 8802, "never the live server's port"
BASE = f"http://127.0.0.1:{PORT}"
# Browser: $LK_BROWSER_EXE, else a Playwright headless shell in the cache (newest), else the installed Google Chrome,
# else Playwright's own default. The cache can be empty (2026-10-19: both 1243 and the pw-venv's 1223 were gone,
# Codex audit 2026-10-19 ran the same checks on the installed Chrome: 13/13 and 38/38).
def _browser_exe():
    import glob
    cands = [os.environ.get("LK_BROWSER_EXE") or ""]
    cands += sorted(glob.glob(os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell")),
        key=lambda p: int(re.search(r"-(\d+)/", p).group(1)), reverse=True)
    cands.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return next((c for c in cands if c and os.path.exists(c)), "")
EXE = _browser_exe()
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)


def main():
    tmp = tempfile.mkdtemp(prefix="lk-qadfix4-alerts-")
    run_all.fresh_copy(tmp)
    db = os.path.join(tmp, "data", "library.db")
    con = sqlite3.connect(db)
    ids = [r[0] for r in con.execute("SELECT book_id FROM books WHERE active=1 AND quantity>0 ORDER BY book_id LIMIT 2")]
    con.execute(f"UPDATE books SET publisher='دار اسفار' WHERE book_id IN ({ids[0]}, {ids[1]})")
    con.commit(); con.close()
    # AUTOUPD: an update waiting for approval (a test key, so the channel counts as set up)
    import json as _j
    open(os.path.join(tmp, "backend", "channel_key.py"), "w").write('PUBLIC_KEY = "' + "11" * 32 + '"\n')
    _j.dump({"update": {"status": "available", "version": "0.9.99 beta", "size": 84100000, "notes": "إصلاحات الشات\nالتنبيهات"},
             "last_check": "2026-10-16T20:00:00+00:00"}, open(os.path.join(tmp, "data", "channel_state.json"), "w"))
    env = {k: v for k, v in os.environ.items() if k not in run_all.KEY_VARS}
    env.update(LIBRARY_KEEPER_LLM="off", LIBRARY_KEEPER_OUTSIDE="0", PYTHONDONTWRITEBYTECODE="1",
               LIBRARY_KEEPER_SECRETS=os.path.join(tmp, "no-keys", "keys.json"),
               LIBRARY_KEEPER_SECRET_FILE=os.path.join(tmp, "no-keys", "secret_key"))
    python = os.path.join(ROOT, "backend", "venv", "bin", "python")
    subprocess.run([python, "-c", "import models; models.init_db(); import auth; auth.create_user('boss', 'المدير', 'manager', 'Manager-pass-1'); "
                    "auth.create_user('staff1', 'موظف تجربة', 'staff', 'Staff-pass-1')"], cwd=os.path.join(tmp, "backend"), env=env, check=True)
    log = open(os.path.join(tmp, "server.log"), "w")
    server = subprocess.Popen([python, "-c", f"import app; app.app.run(host='127.0.0.1', port={PORT})"],
                              cwd=os.path.join(tmp, "backend"), env=env, stdout=log, stderr=log)
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(BASE + "/", timeout=1); break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=EXE) if os.path.exists(EXE) else p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, has_touch=True, is_mobile=True, locale="ar-SA")
            pg = ctx.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(BASE + "/admin")
            pg.fill("#aUser", "staff1"); pg.fill("#aPass", "Staff-pass-1"); pg.press("#aPass", "Enter")
            pg.wait_for_function("() => !document.querySelector('#alertsN').hidden", timeout=8000)
            ok(pg.inner_text("#alertsN").strip() not in ("", "0"), "after sign-in the «التنبيهات» tab shows a count")
            if pg.is_visible("#navToggle"):
                pg.click("#navToggle")
            pg.click("#tabAlerts")
            pg.wait_for_selector("#devList .dn-card", timeout=8000)
            rows = pg.inner_text("#alertsList")
            ok("ملاحظات من المطور" in rows and "جديد" in rows, "the alerts list names the developer notes as new")
            card = pg.inner_text("#devList")
            ok("أرجو مراجعة كل كتب دار أسفار" in card, "the note «أرجو مراجعة كل كتب دار أسفار» is there")
            # [AUDFIX] audit 2026-10-17: more than one note may have a books button; scope to the Asfar note
            asfar = pg.locator(".dn-card", has_text="أرجو مراجعة كل كتب دار أسفار")
            asfar.locator("[data-dn-books]").click()
            pg.wait_for_selector(".dn-book", timeout=8000)
            ok(pg.locator(".dn-book").count() >= 2 and "دار اسفار" in asfar.locator(".dn-books").inner_text(), "a click opens the list of the Asfar books")
            pg.locator("[data-open-book]").first.click()
            pg.wait_for_timeout(800)
            ok(not errors, "a book card opens from the list without a page error")
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(300)
            # [AUDFIX] audit 2026-10-18: the comment box and «تم» were first-match and closed another note (4198)
            # while this passed. Scope both to the Asfar card, then check by ref which note changed.
            def notes_by_ref():
                return {n["ref"]: n for n in ctx.request.get(BASE + "/api/admin/inbox/dev-notes?status=all").json()["notes"]}
            before = notes_by_ref()
            ok("DN-2026-10-16-asfar" in before and len(before) >= 2,
               f"several developer notes are listed, the Asfar one among them ({len(before)})")
            asfar.locator("textarea[id^=dnc]").fill("راجعنا الكتب على الرف")
            asfar.locator("[data-dn][data-to=done]").click()
            pg.wait_for_function("() => document.querySelector('#devState').textContent.includes('تم تسجيلها')", timeout=8000)
            after = notes_by_ref()
            a = after.get("DN-2026-10-16-asfar") or {}
            others_same = all((after.get(r) or {}).get("status") == n["status"] and (after.get(r) or {}).get("handled_note") == n.get("handled_note")
                              for r, n in before.items() if r != "DN-2026-10-16-asfar")
            ok(a.get("status") == "done" and a.get("handled_note") == "راجعنا الكتب على الرف" and others_same,
               "«تم» closes the Asfar note itself with the comment; every other note keeps its status")
            pg.click("#devStatus [data-status=done]")
            pg.wait_for_selector("#devList .dn-card.done", timeout=8000)
            done_asfar = pg.locator("#devList .dn-card.done", has_text="أرجو مراجعة كل كتب دار أسفار")
            ok(done_asfar.count() == 1 and "راجعنا الكتب على الرف" in done_asfar.inner_text() and "موظف تجربة" in done_asfar.inner_text(),
               "marked done: the Asfar note shows under «تمّت» with who did it and the comment")
            big = pg.evaluate("() => [...document.querySelectorAll('#alertsView button')].filter(b => b.offsetParent).every(b => b.getBoundingClientRect().height >= 43.5)")
            wide = pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
            ok(big and wide, "touch targets at least 44px high, no sideways scrolling at phone width")
            pg.wait_for_selector("#updBox:not([hidden])", timeout=8000)
            ok("0.9.99 beta" in pg.inner_text("#updBox") and "بانتظار موافقة مدير المكتبة" in pg.inner_text("#updBox")
               and pg.locator("[data-upd=ask]").count() == 0, "staff see the waiting update, without the approval button")
            ctx2 = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, has_touch=True, is_mobile=True, locale="ar-SA")
            pm = ctx2.new_page(); pm.on("pageerror", lambda e: errors.append(str(e)))
            pm.goto(BASE + "/admin"); pm.fill("#aUser", "boss"); pm.fill("#aPass", "Manager-pass-1"); pm.press("#aPass", "Enter")
            pm.wait_for_function("() => !document.querySelector('#alertsN').hidden", timeout=8000)
            if pm.is_visible("#navToggle"):
                pm.click("#navToggle")
            pm.click("#tabAlerts")
            pm.wait_for_selector("[data-upd=ask]", timeout=8000)
            ok("تحديث جديد لـ«منار»" in pm.inner_text("#alertsList"), "the manager's alerts list the update")
            pm.click("[data-upd=ask]")
            pm.wait_for_selector("[data-upd=go]", timeout=5000)
            ok("نسخة احتياطية" in pm.inner_text("#updBox") and pm.locator("[data-upd=cancel]").count() == 1,
               "«موافقة وتحديث» asks once more, saying what will happen (no browser dialog)")
            pm.click("[data-upd=cancel]")
            pm.wait_for_selector("[data-upd=ask]", timeout=5000)
            pm.screenshot(path=os.path.join(ROOT, "research", "qadfix4_2026-10-16", "update_phone.png"), full_page=True)
            ok(not errors, "no page errors" + (f": {errors[:2]}" if errors else ""))
            pg.screenshot(path=os.path.join(ROOT, "research", "qadfix4_2026-10-16", "alerts_phone.png"), full_page=True)
            browser.close()
    finally:
        server.terminate()
    print(f"\n{P} passed, {F} failed")
    return 1 if F else 0


if __name__ == "__main__":
    sys.exit(main())
