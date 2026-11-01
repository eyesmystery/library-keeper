"""[CODEXWIN] Ibrahim 2026-10-29: «طلع انه مبرمج على ماك. وصلحته على ويندوز وصار يشتغل» — choosing Codex at the
library's Windows PC never connected because the places searched for the `codex` command were Mac-only. Checks the
Windows places (npm's codex.cmd), that a plain extensionless `codex` script is not taken on Windows, and that
MANAR_CODEX_PATH names the command by hand. No real codex is run."""
import os, sys, tempfile
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import llm
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

home = r"C:\Users\lib"
env = {"APPDATA": r"C:\Users\lib\AppData\Roaming", "LOCALAPPDATA": r"C:\Users\lib\AppData\Local"}
win = llm._codex_candidates(windows=True, env=env, home=home)
ok(any(c.endswith("codex.cmd") and "Roaming" in c and "npm" in c for c in win), "Windows: %APPDATA%\\npm\\codex.cmd is searched")
ok(all(c.lower().endswith((".cmd", ".exe")) for c in win), "Windows: only runnable names (.cmd/.exe) are searched")
ok(not any(c.startswith("/opt/homebrew") for c in win), "Windows: no Mac paths")
mac = llm._codex_candidates(windows=False, env={}, home="/Users/x")
ok("/opt/homebrew/bin/codex" in mac and "/Users/x/.local/bin/codex" in mac, "Mac: the old places are kept")
ok(llm._codex_candidates(windows=True, env={"MANAR_CODEX_PATH": r"D:\tools\codex.exe"}, home=home)[0] == r"D:\tools\codex.exe",
   "MANAR_CODEX_PATH comes first")

with tempfile.TemporaryDirectory() as d:
    bare, cmd = os.path.join(d, "codex"), os.path.join(d, "codex.cmd")
    for p in (bare, cmd):
        open(p, "w").write("x")
    os.chmod(bare, 0o755)
    ok(not llm._runnable(bare, windows=True), "Windows: the extensionless npm script is not taken")
    ok(llm._runnable(cmd, windows=True), "Windows: codex.cmd is taken")
    ok(llm._runnable(bare, windows=False), "Mac: an executable codex is taken")
    ok(not llm._runnable(os.path.join(d, "none.cmd"), windows=True), "a missing file is not taken")
    old = os.environ.get("MANAR_CODEX_PATH")
    os.environ["MANAR_CODEX_PATH"] = bare
    ok(llm.codex_path() == bare, "codex_path() honours MANAR_CODEX_PATH")
    if old is None:
        os.environ.pop("MANAR_CODEX_PATH")
    else:
        os.environ["MANAR_CODEX_PATH"] = old

# CODEXWIN2 (the library's Codex note 2026-11-01): the desktop app's own codex.exe, newest version first
with tempfile.TemporaryDirectory() as d:
    b = os.path.join(d, "OpenAI", "Codex", "bin")
    for v in ("0.9.2", "0.10.1", "0.10.0"):
        os.makedirs(os.path.join(b, v))
        open(os.path.join(b, v, "codex.exe"), "w").write("x")
    c = llm._codex_candidates(windows=True, env={"APPDATA": os.path.join(d, "Roaming"), "LOCALAPPDATA": d}, home=d)
    app = [x for x in c if os.path.join("OpenAI", "Codex", "bin") in x]
    ok(app[:3] == [os.path.join(b, v, "codex.exe") for v in ("0.10.1", "0.10.0", "0.9.2")],
       "Windows: %LOCALAPPDATA%\\OpenAI\\Codex\\bin\\<version>\\codex.exe is searched, newest version first")
    ok(app[-1] == os.path.join(b, "codex.exe"), "and a plain bin\\codex.exe after the versions")
    ok(c.index(app[0]) > max(i for i, x in enumerate(c) if "npm" in x), "npm's codex.cmd still comes before the app's copy")
ok(llm._codex_candidates(windows=True, env={"LOCALAPPDATA": r"Z:\none"}, home=home)[-1].endswith("codex.exe"),
   "a missing app folder only adds the plain path (no error)")

# the login check: the exit code decides; a warning that merely contains «not» is not a logout
R = llm._CODEX_LOGGED_OUT
ok(not R.search("WARNING: proceeding, even though we could not update PATH\nLogged in using ChatGPT"),
   "«could not update PATH … Logged in» counts as logged in")
ok(bool(R.search("Not logged in")) and bool(R.search("You are not signed in")) and bool(R.search("Logged out")),
   "«Not logged in» / «not signed in» / «Logged out» count as logged out")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
