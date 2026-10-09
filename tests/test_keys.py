import os, sys, json, io, stat, tempfile
tmp = tempfile.mkdtemp(); os.environ["LIBRARY_KEEPER_SECRETS"] = os.path.join(tmp, "keys", "keys.json")
for k in ["ANTHROPIC_API_KEY","OPENAI_API_KEY","GEMINI_API_KEY","OPENROUTER_API_KEY","CUSTOM_LLM_API_KEY","OLLAMA_BASE_URL"]: os.environ.pop(k, None)
sys.path.insert(0, os.getcwd())
import llm, app as A
from auth_helper import login
if os.path.exists(llm.SETTINGS_PATH): os.remove(llm.SETTINGS_PATH)
c = login(A.app.test_client()); P = F = 0
def ok(cond, msg):
    global P, F; print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)
KEY = "sk-or-test-1234567890abcdWXYZ"
r = c.post("/api/admin/llm/key", json={"provider": "openrouter", "key": KEY})
ok(r.status_code == 200, "save key from admin")
path = llm.SECRETS_PATH
ok(os.path.exists(path) and stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700, "key file owner-only (600, dir 700)")
proj = os.path.realpath(os.path.join(os.path.dirname(llm.__file__), ".."))
ok(not os.path.realpath(path).startswith(proj), "key stored outside the project folder")
st = c.get("/api/admin/llm").get_json(); dump = json.dumps(st)
orow = next(p for p in st["providers"] if p["id"] == "openrouter")
ok(KEY not in dump and orow["masked_key"] == "••••WXYZ" and orow["key_source"] == "saved" and orow["configured"], "status masked, never full key")
ok(KEY not in open(llm.SETTINGS_PATH).read() if os.path.exists(llm.SETTINGS_PATH) else True, "settings file has no key")
ok(c.post("/api/admin/llm/key", json={"provider": "openrouter", "key": "bad key with spaces"}).status_code == 400, "invalid key rejected")
ok(c.post("/api/admin/llm/key", json={"provider": "ollama", "key": "whatever123"}).status_code == 400, "ollama takes no key")
ok(c.post("/api/admin/llm/key", json={"provider": "openrouter", "key": KEY}, environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403, "key save refused from another device")
ok(c.post("/api/admin/llm/endpoint", json={"provider": "custom", "url": "https://x.y/v1"}, environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403, "address change refused from another device")
os.environ["OPENROUTER_API_KEY"] = "sk-env-000000000000ENVK"
ok(llm.get_key("openrouter").endswith("ENVK") and llm.key_source("openrouter") == "terminal", "Terminal key takes precedence")
del os.environ["OPENROUTER_API_KEY"]
# transport
class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass
cap = {}
def fake_urlopen(req, timeout=None, **kw):
    url = req.full_url if hasattr(req, "full_url") else req
    if isinstance(url, str) and url.endswith("/api/tags"):
        cap["tags"] = url; return FakeResp(json.dumps({"models": [{"name": "m1"}]}).encode())
    cap.update(url=req.full_url, headers={k.lower(): v for k, v in req.header_items()}, body=json.loads(req.data))
    return FakeResp(json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}]}).encode())
llm.urllib.request.urlopen = fake_urlopen
r = c.post("/api/admin/llm/ping", json={"provider": "openrouter", "model": "google/gemini-3.8-flash"}).get_json()
ok(r["ok"] and cap["url"] == "https://openrouter.ai/api/v1/chat/completions" and cap["headers"]["authorization"] == "Bearer " + KEY and cap["headers"]["x-openrouter-title"] == "Library Keeper" and cap["body"]["model"] == "google/gemini-3.8-flash", "OpenRouter: endpoint, saved key used, title header, model")
ok(c.post("/api/admin/llm/endpoint", json={"provider": "custom", "url": "ftp://bad"}).status_code == 400, "bad custom URL rejected")
ok(c.post("/api/admin/llm/endpoint", json={"provider": "custom", "url": "https://api.example.com/v1"}).status_code == 200, "custom address saved")
r = c.post("/api/admin/llm/ping", json={"provider": "custom", "model": "my-model"}).get_json()
ok(r["ok"] and cap["url"] == "https://api.example.com/v1/chat/completions" and "authorization" not in cap["headers"], "custom provider works without key")
c.post("/api/admin/llm/key", json={"provider": "custom", "key": "custom-key-12345678"})
c.post("/api/admin/llm/ping", json={"provider": "custom", "model": "my-model"})
ok(cap["headers"].get("authorization") == "Bearer custom-key-12345678", "custom provider sends optional key")
c.post("/api/admin/llm/endpoint", json={"provider": "ollama", "url": "http://192.168.1.50:11434"})
st = c.get("/api/admin/llm").get_json()
ok(cap["tags"].startswith("http://192.168.1.50:11434") and next(p for p in st["providers"] if p["id"] == "ollama")["configured"], "Ollama address editable (e.g. another machine)")
c.post("/api/admin/llm/ping", json={"provider": "ollama", "model": "m1"})
ok(cap["url"] == "http://192.168.1.50:11434/v1/chat/completions", "Ollama requests go to the saved address")
ok(c.post("/api/admin/llm", json={"provider": "openrouter", "model": "deepseek/deepseek-v4.1-flash"}).status_code == 200 and llm.get_active() == ("openrouter", "deepseek/deepseek-v4.1-flash"), "OpenRouter selectable with slash model id")
r = c.post("/api/admin/llm/key", json={"provider": "openrouter", "key": ""})
ok(r.status_code == 200 and llm.get_key("openrouter") is None and llm.get_active() is None, "delete key -> provider not ready")
ok(c.post("/api/admin/llm/ping", json={"provider": "openrouter", "model": "x/y"}).get_json()["ok"] is False, "ping without key reports error")
ok(c.post("/api/admin/llm/ping", json={"provider": "nope"}).status_code == 400, "unknown provider ping 400")
os.remove(llm.SETTINGS_PATH)
print(f"TOTAL PASS={P} FAIL={F}")
