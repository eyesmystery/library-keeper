"""[MGR, AGY-20261029-02] Browser check: moving back inside «منار» never leaves the app.

  ~/.library_keeper/pw-venv/bin/python tests/browser_history.py [port]

Not in tests/run_all.py: it needs Playwright's Chromium (the separate pw-venv). Same setup as browser_context_chips.py:
a throwaway copy (tests/run_all.fresh_copy), served on 127.0.0.1:<port> (default 8834; never 8802), phone 390×844.

The page is opened after an outside page (about:blank), so a history.go() that counts too many steps lands there.
Paths: start page → book (two levels, one history step) → logo; start → «ماذا تقرأ الآن؟» → «العودة للبداية»;
start → chat → book → Back → Back; book → review form → Back → logo; and the phone's Back after each.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.environ.get("LK_TEST_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import run_all  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8834
assert PORT != 8802, "never the live server's port"
BASE = f"http://127.0.0.1:{PORT}"


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
    print(("PASS " if cond else "FAIL ") + msg)
    P += bool(cond)
    F += (not cond)


BOOK = """async () => {
  try { const r = await fetch('/api/picks?lang=ar'); const j = await r.json();
        const list = Array.isArray(j) ? j : (j.books || j.picks || []); if (list.length) return list[0]; } catch (e) {}
  return {book_id: 1, title: 'كتاب للتجربة', author: 'مؤلف', available: true};
}"""


def where(pg):
    """(still inside «منار», the screen shown)"""
    pg.wait_for_timeout(700)
    inside = pg.url.startswith(BASE)
    return inside, (pg.evaluate("() => currentSection") if inside else None)


def fresh(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, locale="ar-SA")
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto("about:blank")
    pg.goto(BASE + "/")
    pg.wait_for_function("() => typeof showSection === 'function'")
    return ctx, pg, errors


def scenarios(browser):
    # 1. start page → a book of «اختياراتنا» (level 0 → 2, one history step) → the logo → phone Back
    ctx, pg, errors = fresh(browser)
    book = pg.evaluate(BOOK)
    pg.evaluate("b => showBook(b)", book)
    ok(where(pg) == (True, "bookDetail"), "start → book page")
    pg.locator("#bookDetail .brand-home").first.click()
    ok(where(pg) == (True, "welcome"), f"book page → logo: back on the start page, still in «منار» ({pg.url})")
    pg.evaluate("b => showBook(b)", book)
    pg.go_back()
    ok(where(pg) == (True, "welcome"), "start → book page → phone Back: the start page")
    ok(not errors, f"path 1: no JavaScript error {errors[:2]}")
    ctx.close()

    # 2. start page → «ماذا تقرأ الآن؟» → «العودة للبداية» (the audit's go(-3))
    ctx, pg, errors = fresh(browser)
    pg.evaluate("() => openReading()")
    ok(where(pg) == (True, "reading"), "start → «ماذا تقرأ الآن؟»")
    pg.evaluate("() => goHome()")
    ok(where(pg) == (True, "welcome"), f"reading → «العودة للبداية»: start page, still in «منار» ({pg.url})")
    pg.evaluate("() => openReading()")
    pg.go_back()
    ok(where(pg) == (True, "welcome"), "start → reading → phone Back: the start page")
    ok(not errors, f"path 2: no JavaScript error {errors[:2]}")
    ctx.close()

    # 3. start → chat → book → Back → Back
    ctx, pg, errors = fresh(browser)
    book = pg.evaluate(BOOK)
    pg.click("#startBtn")
    ok(where(pg) == (True, "chat"), "start → chat")
    pg.evaluate("b => showBook(b)", book)
    pg.evaluate("() => goBack()")
    ok(where(pg) == (True, "chat"), "chat → book → Back: the chat")
    pg.evaluate("() => goBack()")
    ok(where(pg) == (True, "welcome"), "… → Back: the start page, still in «منار»")
    ok(not errors, f"path 3: no JavaScript error {errors[:2]}")
    ctx.close()

    # 4. book → «قرأته؟ شاركنا رأيك» → Back → logo
    ctx, pg, errors = fresh(browser)
    book = pg.evaluate(BOOK)
    pg.evaluate("b => showBook(b)", book)
    pg.evaluate("b => openReading(b)", book)
    ok(where(pg) == (True, "reading"), "book page → review form")
    pg.evaluate("() => readingBack()")
    ok(where(pg) == (True, "bookDetail"), "review form → Back: the book page")
    pg.evaluate("() => goHome()")
    ok(where(pg) == (True, "welcome"), f"→ logo: start page, still in «منار» ({pg.url})")
    pg.evaluate("b => showBook(b)", book)
    pg.evaluate("() => goHome()")
    pg.go_back()
    inside, _sec = where(pg)
    ok(not inside, "at the start page the phone's Back leaves «منار» as usual (one step: the page before it)")
    ok(not errors, f"path 4: no JavaScript error {errors[:2]}")
    ctx.close()

    # 5. (Ibrahim 2026-10-29) start page → «اقترح كتابًا لرفوف مكتبة ثلاث» → the suggestion form at once → «رجوع» / phone Back
    ctx, pg, errors = fresh(browser)
    btn = pg.locator("#shelfStartBtn")
    ok(btn.is_visible() and "اقترح كتابًا" in btn.inner_text(), "the start page shows the shelf-suggestion button")
    if os.environ.get("LK_SHOT"):
        pg.screenshot(path=os.environ["LK_SHOT"], full_page=False)
    btn.click()
    ok(where(pg) == (True, "reading") and pg.locator("#sForm").is_visible() and not pg.locator("#rFind").is_visible(),
       "→ the suggestion form straight away, not the review search")
    ok("اقترح كتابًا" in pg.locator("#rTitle").inner_text(), "its title says it is a suggestion")
    pg.click("#sBack")
    ok(where(pg) == (True, "welcome"), "its «رجوع» → the start page")
    btn.click()
    pg.evaluate("() => readingBack()")
    ok(where(pg) == (True, "welcome"), "the header «رجوع» → the start page")
    btn.click()
    pg.go_back()
    ok(where(pg) == (True, "welcome"), "phone Back → the start page")
    pg.evaluate("() => openReading()")
    ok(pg.locator("#rFind").is_visible() and "ماذا تقرأ" in pg.locator("#rTitle").inner_text(),
       "«ماذا تقرأ الآن؟» still opens on the review search")
    pg.click("#rShelfBtn")
    pg.click("#sBack")
    ok(where(pg) == (True, "reading") and pg.locator("#rFind").is_visible(), "its own shelf button: «رجوع» back to the search")
    ok(not errors, f"path 5: no JavaScript error {errors[:2]}")
    ctx.close()


def main():
    tmp = tempfile.mkdtemp(prefix="lk-history-")
    run_all.fresh_copy(tmp)
    env = {k: v for k, v in os.environ.items() if k not in run_all.KEY_VARS}
    env.update(LIBRARY_KEEPER_LLM="off", LIBRARY_KEEPER_OUTSIDE="0", PYTHONDONTWRITEBYTECODE="1",
               LIBRARY_KEEPER_SECRETS=os.path.join(tmp, "no-keys", "keys.json"),
               LIBRARY_KEEPER_SECRET_FILE=os.path.join(tmp, "no-keys", "secret_key"))
    python = os.path.join(ROOT, "backend", "venv", "bin", "python")
    log = open(os.path.join(tmp, "server.log"), "w")
    server = subprocess.Popen([python, "-c", f"import app; app.app.run(host='127.0.0.1', port={PORT})"],
                              cwd=os.path.join(tmp, "backend"), env=env, stdout=log, stderr=log)
    try:
        for _ in range(300):
            try:
                urllib.request.urlopen(BASE + "/", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        else:
            raise RuntimeError("the throwaway server did not start")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=EXE) if os.path.exists(EXE) else p.chromium.launch()
            scenarios(browser)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nTOTAL pass={P} fail={F}")
    return 1 if F else 0


if __name__ == "__main__":
    sys.exit(main())
