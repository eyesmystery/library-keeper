"""ChatGPT sign-in through the Codex CLI provider, against tests/fake_codex/codex."""
import os, sys, json, tempfile
sys.path.insert(0, os.getcwd())
FAKE = os.path.join(os.path.dirname(os.getcwd()), "tests", "fake_codex")
os.environ["PATH"] = FAKE + os.pathsep + os.environ.get("PATH", "")
LOG = os.path.join(tempfile.mkdtemp(), "codex.log")
os.environ["FAKE_CODEX_LOG"] = LOG
CODEX_TEST_HOME = tempfile.mkdtemp()
os.environ["CODEX_HOME"] = CODEX_TEST_HOME
with open(os.path.join(CODEX_TEST_HOME, "models_cache.json"), "w", encoding="utf-8") as f:
    json.dump({"models": [
        {"slug": "gpt-current", "display_name": "GPT Current", "visibility": "list", "priority": 2},
        {"slug": "gpt-new", "display_name": "GPT New", "visibility": "list", "priority": 1},
        {"slug": "internal-model", "display_name": "Internal", "visibility": "hide", "priority": 0},
    ]}, f)
for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"]:
    os.environ.pop(k, None)
import llm, app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)
def calls():
    return [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []

ok(llm.codex_path() and llm.codex_path().startswith(FAKE), "codex binary found on PATH")
ready, note = llm.codex_login_status(max_age=0)
ok(ready and "ChatGPT" in note, "login status: ready")
st = {p["id"]: p for p in llm.status()["providers"]}
ok(st["codex"]["configured"] and not st["codex"]["uses_key"] and "للتجربة الشخصية" in st["codex"]["note"],
   "admin status: configured, no key field, personal-use note")
codex_options = st["codex"]["model_options"]
ok([o["value"] for o in codex_options] == ["default", "gpt-new", "gpt-current"],
   "Codex picker follows the visible account model cache and priority")
ok("internal-model" not in [o["value"] for o in codex_options], "hidden Codex models stay hidden")
live = llm.list_provider_models("codex", refresh=True)
ok(live["total"] == 3 and live["models"] == codex_options, "Codex refresh returns the current local picker list")
ok(llm.get_active() is None, "codex is never chosen automatically")
llm.set_active("codex", "default")
ok(llm.get_active() == ("codex", "default"), "chosen explicitly in settings")

out = llm.complete_json("sys", [{"role": "user", "content": "مرحبا سري"}],
                        {"type": "object", "properties": {"ok": {"type": "boolean"}}}, active=("codex", "default"))
c0 = calls()[-1]
ok(out == {"ok": True} and llm.last_call["ok"] and llm.last_call["format"] == "codex_exec_schema", "JSON answer parsed from -o file")
argv = c0["argv"]
ok(argv[-1] == "-" and "مرحبا" not in " ".join(argv), "visitor text goes through stdin, never argv")
ok("--ephemeral" in argv and "--ignore-user-config" in argv and argv[argv.index("--sandbox") + 1] == "read-only"
   and "--skip-git-repo-check" in argv and "-m" not in argv, "safe flags; default model omits -m")
ok(c0["schema"]["additionalProperties"] is False and c0["schema"]["required"] == ["ok"], "schema made strict for Codex")
ok(os.path.basename(c0["cwd"]).startswith("lk-codex-") and not os.path.exists(c0["cwd"]), "runs in a temp folder that is removed")
llm.complete_json("sys", [{"role": "user", "content": "x"}], {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                  active=("codex", "gpt-test"))
a = calls()[-1]["argv"]
ok(a[a.index("-m") + 1] == "gpt-test", "a named model is passed with -m")
nested = llm._strict_schema({"type": "object", "properties": {"p": {"type": "object", "properties": {"a": {"type": "string"}}}}})
ok(nested["properties"]["p"]["additionalProperties"] is False and nested["properties"]["p"]["required"] == ["a"], "nested objects made strict")

r = A.run_chat([{"role": "user", "content": "ابغى رواية"}], active=("codex", "default"))
ok(r["understanding"] == "ai" and r["provider"] == "codex" and r["books"], "full chat turn through Codex")

os.environ["FAKE_CODEX_FAIL"] = "1"
try:
    llm.complete_json("sys", [{"role": "user", "content": "x"}], {"type": "object", "properties": {}}, active=("codex", "default"))
    ok(False, "failure raises")
except llm.LLMError as e:
    ok("codex exit 3" in str(e), "non-zero exit -> LLMError with reason")
r = A.run_chat([{"role": "user", "content": "ابغى رواية"}], active=("codex", "default"))
ok(r["understanding"] == "keywords" and r["_debug"]["failed_stage"] == "interpret", "Codex failure -> safe keyword fallback")
del os.environ["FAKE_CODEX_FAIL"]

# Codex command older than the model list (2026-10-09: CLI 0.154.0 refused the GPT-6 models)
cache_path = os.path.join(CODEX_TEST_HOME, "models_cache.json")
cache = json.load(open(cache_path)); cache["client_version"] = "0.162.0"; json.dump(cache, open(cache_path, "w"))
llm._codex_version.update(at=0.0, value=None)
ok(llm.codex_cli_version() == "0.162.0" and llm.codex_update_needed() is None, "same version -> no update warning")
os.environ["FAKE_CODEX_VERSION"] = "0.154.0"; llm._codex_version.update(at=0.0, value=None)
ok(llm.codex_update_needed() == ("0.154.0", "0.162.0"), "older command than the model list is detected")
cx = {p["id"]: p for p in llm.status()["providers"]}["codex"]
ok("0.154.0" in cx.get("warning", "") and cx.get("update_command") == llm.CODEX_UPDATE_CMD and cx["version"] == "0.154.0",
   "settings status carries the warning and the update command")
os.environ["FAKE_CODEX_UNSUPPORTED"] = "1"
try:
    llm.complete_json("sys", [{"role": "user", "content": "x"}], {"type": "object", "properties": {}}, active=("codex", "gpt-new"))
    ok(False, "unsupported model raises")
except llm.LLMError as e:
    ok("gpt-new" in str(e) and llm.CODEX_UPDATE_CMD in str(e), "refused model -> Arabic reason with the update command")
del os.environ["FAKE_CODEX_UNSUPPORTED"]; del os.environ["FAKE_CODEX_VERSION"]; llm._codex_version.update(at=0.0, value=None)
ok("warning" not in {p["id"]: p for p in llm.status()["providers"]}["codex"], "after updating the warning goes away")

os.environ["FAKE_CODEX_LOGGED_OUT"] = "1"
ok(llm.codex_login_status(max_age=0)[0] is False and llm.get_active() is None, "logged out -> not ready, not active")
del os.environ["FAKE_CODEX_LOGGED_OUT"]
os.environ["PATH"] = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if p != FAKE)
if llm.codex_path() is None:
    ok("ما لقينا" in llm.codex_login_status(max_age=0)[1], "codex missing -> clear note")
else:
    ok(True, "real codex present on this machine; missing-binary note not checked")
print(f"TOTAL PASS={P} FAIL={F}")
