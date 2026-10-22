"""[AUDFIX] Browser check: a tap on the ✕ of a «نبحث عن» pill removes that pill and nothing else (daily audit 2026-10-12 #2).

  ~/.library_keeper/pw-venv/bin/python tests/browser_context_chips.py [port]

Not in tests/run_all.py: it needs Playwright's Chromium (the separate pw-venv), which the app itself does not use.
It makes its own throwaway copy of the project (tests/run_all.fresh_copy: frozen test catalog, no keys, model off,
no outside lookups) and serves it on 127.0.0.1:<port> (default 8833; never 8802). Phone context: 390×844, touch.

For two page states (a real conversation first, so the page is scrolled — the state where the audit saw the wrong
pill go — and an empty chat) it renders eight pills on several rows with the page's own renderContext(), then
removes them one by one with taps at the edges, corners and centre of each 44px target. After every tap it checks
which pill went (exactly the tapped one), that the others are all still there, and re-measures the wrapped rows:
44×44 targets, 26px visible circle, same pill height, no two targets overlapping, every corner hitting its own ✕.
A tap in the gap between two rows removes nothing; Enter on a focused ✕ removes that pill.
"""
import os, re
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

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8833
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
    print(("PASS " if cond else "FAIL ") + msg)
    P += bool(cond)
    F += (not cond)


PILLS = """() => { contextItems = [
    {key: 'audience', label: 'للأطفال'}, {key: 'age', label: 'عمر ٨ سنوات'}, {key: 'genre', label: 'رواية'},
    {key: 'origin', label: 'مترجمة عن الإنجليزية'}, {key: 'award', label: 'حاصلة على جائزة'}, {key: 'length', label: 'قصيرة'},
    {key: 'lang', label: 'بالعربية'}, {key: 'mood', label: 'مزاج هادئ'}]; renderContext(); }"""
SHOW = "() => document.querySelector('#contextBar').scrollIntoView({block: 'center'})"
LABELS = "() => contextItems.map(x => x.label)"
FACTS = """() => [...document.querySelectorAll('.context-clear')].map(b => {
  const r = b.getBoundingClientRect(), c = getComputedStyle(b, '::before'), chip = b.parentElement.getBoundingClientRect();
  const own = (x, y) => document.elementFromPoint(x, y) === b;
  return {label: b.getAttribute('aria-label').replace(/^[^:]*:\\s*/, ''), x: r.left, y: r.top, w: r.width, h: r.height,
          circle: [parseFloat(c.width), parseFloat(c.height)], chipH: chip.height,
          corners: [own(r.left + 2, r.top + 2), own(r.right - 2, r.top + 2), own(r.left + 2, r.bottom - 2), own(r.right - 2, r.bottom - 2)]};
})"""
# tap points inside a 44px target, from its centre: corners and edges outside the 26px circle, and the centre
OFFSETS = [(17, -17), (-17, -17), (17, 17), (-17, 17), (0, -20), (20, 0), (0, 20), (-20, 0), (0, 0)]


def overlap(a, b):
    return a["x"] < b["x"] + b["w"] - 0.5 and b["x"] < a["x"] + a["w"] - 0.5 and a["y"] < b["y"] + b["h"] - 0.5 and b["y"] < a["y"] + a["h"] - 0.5


def check_layout(pg, where, chip_h):
    facts = pg.evaluate(FACTS)
    bad_size = [f["label"] for f in facts if abs(f["w"] - 44) > 0.6 or abs(f["h"] - 44) > 0.6]
    bad_look = [f["label"] for f in facts if abs(f["circle"][0] - 26) > 0.6 or abs(f["chipH"] - chip_h) > 0.6]
    pairs = [(a["label"], b["label"]) for i, a in enumerate(facts) for b in facts[i + 1:] if overlap(a, b)]
    corners = [f["label"] for f in facts if f["corners"] != [True] * 4]
    ok(not bad_size and not bad_look and not pairs and not corners,
       f"{where}: {len(facts)} targets 44×44, circle 26px, pill height kept, none overlapping, every corner hits its own ✕"
       + (f" — size {bad_size} look {bad_look} overlap {pairs} corners {corners}" if (bad_size or bad_look or pairs or corners) else ""))
    return facts


def pick(facts, step):
    """The most error-prone pill: one on a lower row with a target right under another row's target."""
    rows = sorted({round(f["y"]) for f in facts})
    for f in facts:
        if round(f["y"]) != rows[0] and any(round(g["y"]) < round(f["y"]) and g["x"] < f["x"] + f["w"] and f["x"] < g["x"] + g["w"] for g in facts):
            return f
    return facts[step % len(facts)]


def tap_and_check(pg, label, dx, dy, where):
    before = pg.evaluate(LABELS)
    pg.evaluate(SHOW)
    pg.wait_for_timeout(500)
    box = pg.locator(".context-chip", has_text=label).locator(".context-clear").bounding_box()
    x, y = box["x"] + box["width"] / 2 + dx, box["y"] + box["height"] / 2 + dy
    pg.touchscreen.tap(x, y)
    pg.wait_for_timeout(350)
    after = pg.evaluate(LABELS)
    gone = [x for x in before if x not in after]
    ok(gone == [label] and after == [x for x in before if x != label],
       f"{where}: tap at ({dx:+d},{dy:+d}) from the centre of «{label}»'s ✕ removes «{label}» only (removed {gone}, left {len(after)})")
    pg.wait_for_function("() => !busy", timeout=30000)


def run_state(pg, where, first=()):
    pg.evaluate(PILLS)
    pg.evaluate(SHOW)
    pg.wait_for_timeout(500)
    facts = pg.evaluate(FACTS)
    chip_h = facts[0]["chipH"]
    ok(len(facts) == 8 and len({round(f["y"]) for f in facts}) >= 3 and chip_h < 36,
       f"{where}: eight pills wrap onto {len({round(f['y']) for f in facts})} rows, pill height {chip_h:.1f}px")
    facts = check_layout(pg, where + " (start)", chip_h)
    # a tap in the gap between two rows' targets removes nothing
    gap = None
    for a in facts:
        for b in facts:
            if b["y"] > a["y"] + a["h"] and b["y"] - (a["y"] + a["h"]) >= 1 and a["x"] < b["x"] + b["w"] - 4 and b["x"] < a["x"] + a["w"] - 4:
                gap = (max(a["x"], b["x"]) + 2, (a["y"] + a["h"] + b["y"]) / 2)
                break
        if gap:
            break
    if gap:
        before = pg.evaluate(LABELS)
        pg.touchscreen.tap(*gap)
        pg.wait_for_timeout(350)
        ok(pg.evaluate(LABELS) == before, f"{where}: a tap in the gap between two rows' targets removes nothing")
    step = 0
    for label in first:   # the audit's own sequence first
        tap_and_check(pg, label, 17, -17, where)
        facts = check_layout(pg, where + f" (after removing «{label}»)", chip_h)
        step += 1
    while len(pg.evaluate(LABELS)) > 1:
        f = pick(facts, step)
        dx, dy = OFFSETS[step % len(OFFSETS)]
        tap_and_check(pg, f["label"], dx, dy, where)
        facts = check_layout(pg, where + f" (after removing «{f['label']}»)", chip_h)
        step += 1
    # keyboard: Enter on a focused ✕ removes that pill
    pg.evaluate(PILLS)
    pg.locator(".context-chip", has_text="قصيرة").locator(".context-clear").focus()
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(300)
    after = pg.evaluate(LABELS)
    ok("قصيرة" not in after and len(after) == 7, f"{where}: Enter on the focused ✕ of «قصيرة» removes it only")
    pg.wait_for_function("() => !busy", timeout=30000)


def main():
    tmp = tempfile.mkdtemp(prefix="lk-audfix-chips-")
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
        for _ in range(150):
            try:
                urllib.request.urlopen(BASE + "/", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        else:
            raise RuntimeError("the throwaway server did not start")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=EXE) if os.path.exists(EXE) else p.chromium.launch()
            for where, talk in (("after a conversation", True), ("empty chat", False)):
                ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                          has_touch=True, is_mobile=True, locale="ar-SA")
                pg = ctx.new_page()
                errors = []
                pg.on("pageerror", lambda e: errors.append(str(e)))
                pg.goto(BASE + "/")
                pg.click("#startBtn")
                pg.wait_for_selector("#chatInput", state="visible")
                if talk:
                    pg.fill("#chatInput", "أبغى رواية قصيرة مترجمة حاصلة على جائزة")
                    pg.click("#sendBtn")
                    pg.wait_for_selector(".msg-bot", timeout=30000)
                    pg.wait_for_function("() => !busy", timeout=30000)
                    pg.wait_for_timeout(1500)
                run_state(pg, where, ("للأطفال", "مترجمة عن الإنجليزية", "حاصلة على جائزة") if talk else ())
                ok(not errors, f"{where}: no JavaScript error on the page {errors[:2]}")
                ctx.close()
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
