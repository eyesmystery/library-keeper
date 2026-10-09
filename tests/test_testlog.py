import os, sys, json, shutil
for k in ["ANTHROPIC_API_KEY","OPENAI_API_KEY","GEMINI_API_KEY","LIBRARY_KEEPER_PRODUCTION","LIBRARY_KEEPER_LLM"]:
    os.environ.pop(k, None)
sys.path.insert(0, os.getcwd())
import llm, testlog, app as A
from auth_helper import login
c = login(A.app.test_client()); P = F = 0
def ok(cond, msg):
    global P, F; print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)
shutil.rmtree(testlog.LOG_DIR, ignore_errors=True)
os.environ["GEMINI_API_KEY"] = "secret-gem"
c.post("/api/admin/llm", json={"provider": "gemini", "model": "gemini-3.5-flash-lite"})
EMPTY = llm.clean_prefs({}); EMPTY["length"] = "any"
def fake(system, messages, schema, max_tokens=700, active=None):
    if "intent" in schema["properties"]:
        p = dict(EMPTY); p["genres"] = ["رواية"]
        return {"intent": "recommend", "preferences": p, "mentioned_titles": [], "wants_new_options": False}
    facts = json.loads(system.split("<facts>\n")[1].split("\n</facts>")[0])
    return {"reply": "اقتراحات", "show_book_ids": [facts[0]["id"], 9999], "suggestions": ["أقصر"]}
llm.complete_json = fake
cid = "test-conv-12345678"
r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "أبغى رواية"}], "conversation_id": cid}).get_json()
ok(r["test_mode"] is False and "_debug" not in r and not os.path.isdir(testlog.LOG_DIR), "off by default: nothing written, no debug leak")
ok(c.get("/api/test-mode").get_json() == {"enabled": False}, "visitor banner endpoint off")
ok(c.post("/api/admin/testlog", json={"enabled": True}).status_code == 200 and testlog.enabled(), "admin enables test mode")
s = json.load(open(llm.SETTINGS_PATH))
ok(s.get("provider") == "gemini" and s.get("test_log") is True, "settings merged (model choice kept)")
r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "أبغى رواية"}], "conversation_id": cid}).get_json()
ok(r["test_mode"] is True and "_debug" not in r, "visitor sees test_mode flag, no debug in response")
_ch = c.post("/api/choice", json={"book_id": r["books"][0]["book_id"], "conversation_id": cid}).get_json()
c.post("/api/feedback", json={"rating": "close", "choice_id": _ch["choice_id"], "choice_token": _ch["choice_token"], "conversation_id": cid})
c.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}], "conversation_id": "bad id!!"})
files = os.listdir(testlog.LOG_DIR); rows = [json.loads(l) for l in open(os.path.join(testlog.LOG_DIR, files[0]), encoding="utf-8")]
turn = rows[0]
ok(len(files) == 1 and files[0].startswith("chat-") and files[0].endswith(".jsonl"), "daily JSONL file")
ok(turn["kind"] == "turn" and turn["conversation_id"] == cid and turn["visitor"] == "أبغى رواية" and turn["reply"] == "اقتراحات", "turn logged with visitor text and reply")
ok(turn["provider"] == "gemini" and turn["understanding"] == "ai" and "seconds" in turn, "provider/mode/timing logged")
ok(turn["debug"]["intent"] == "recommend" and turn["debug"]["dropped_ids"] == [9999] and turn["debug"]["candidates"], "debug: intent, candidates, dropped invented id")
ok(any(r["kind"] == "feedback" and r["rating"] == "close" and r["conversation_id"] == cid for r in rows), "feedback linked to conversation")
ok(rows[-1]["conversation_id"] is None, "invalid conversation id not stored")
ok("secret-gem" not in open(os.path.join(testlog.LOG_DIR, files[0]), encoding="utf-8").read() and "secret-gem" not in open(llm.SETTINGS_PATH).read(), "no API key in logs or settings")
st = c.get("/api/admin/testlog").get_json()
ok(st["turns"] == 2 and st["conversations"] == 1, "admin summary counts")
t = c.post("/api/admin/llm/test", json={"message": "رواية"}).get_json()
ok("debug" in t and t["debug"]["mode"] == "ai", "admin quick test shows debug")
os.environ["LIBRARY_KEEPER_PRODUCTION"] = "1"
n = len(open(os.path.join(testlog.LOG_DIR, files[0]), encoding="utf-8").readlines())
c.post("/api/chat", json={"messages": [{"role": "user", "content": "رواية"}], "conversation_id": cid})
ok(not testlog.enabled() and len(open(os.path.join(testlog.LOG_DIR, files[0]), encoding="utf-8").readlines()) == n, "production flag hard-disables logging")
ok(c.post("/api/admin/testlog", json={"enabled": True}).status_code == 400, "cannot enable in production")
del os.environ["LIBRARY_KEEPER_PRODUCTION"]
c.post("/api/admin/testlog", json={"enabled": False})
ok(not testlog.enabled(), "admin disables test mode")
def failing(*a, **k):
    llm._record_error("gemini", "HTTP 404: model not found")  # real complete_json records before raising
    raise llm.LLMError("HTTP 404: model not found")
llm.complete_json = failing
c.post("/api/admin/testlog", json={"enabled": True})
c.post("/api/chat", json={"messages": [{"role": "user", "content": "رواية"}], "conversation_id": cid})
last = json.loads(open(os.path.join(testlog.LOG_DIR, files[0]), encoding="utf-8").readlines()[-1])
ok(last["understanding"] == "keywords" and "404" in last["debug"]["why"], "model failure reason logged")
print(f"TOTAL PASS={P} FAIL={F}")
