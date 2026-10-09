"""Ollama Cloud through its API with a key (Ibrahim 2026-10-09, AISET)."""
import os, sys, json, tempfile
sys.path.insert(0, os.getcwd())
for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "OLLAMA_API_KEY", "LIBRARY_KEEPER_STRUCTURED"]:
    os.environ.pop(k, None)
os.environ["LIBRARY_KEEPER_SECRETS"] = os.path.join(tempfile.mkdtemp(), "keys.json")
import llm, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

llm.SECRETS_PATH = os.environ["LIBRARY_KEEPER_SECRETS"]
c = login(A.app.test_client())
st = {p["id"]: p for p in c.get("/api/admin/llm").get_json()["providers"]}
oc = st.get("ollama_cloud")
ok(oc and oc["uses_key"] and not oc["configured"] and "ollama.com/settings/keys" in oc["note"],
   "Ollama Cloud listed, needs a key, says where to make one")
ok([o["value"] for o in oc["model_options"]][0] == "deepseek-v4.1-flash", "suggested cloud models without the :cloud suffix")
ok(llm.ORDER.index("ollama_cloud") == llm.ORDER.index("ollama") + 1, "shown next to local Ollama")

seen = []
def fake_post(url, body, headers, timeout):
    seen.append((url, body, headers, timeout))
    return {"choices": [{"message": {"content": '{"x": 3}'}}]}
real_post = llm._post
llm._post = fake_post
llm.set_key("ollama_cloud", "oc-test-key-123")
ok(llm.has_key("ollama_cloud") and llm.masked_key("ollama_cloud") == "••••-123", "key saved like the other providers")
schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
r = llm.complete_json("sys", [{"role": "user", "content": "hi"}], schema, active=("ollama_cloud", "deepseek-v4.1-flash"))
url, body, headers, timeout = seen[-1]
ok(r == {"x": 3} and url == "https://ollama.com/v1/chat/completions", "chat goes to ollama.com's OpenAI-compatible endpoint")
ok(headers.get("authorization") == "Bearer oc-test-key-123" and body["model"] == "deepseek-v4.1-flash", "key sent as Bearer, model name as chosen")
ok(timeout == 30 and body["response_format"]["type"] == "json_schema", "30 s timeout and schema enforcement like local Ollama")
os.environ["LIBRARY_KEEPER_STRUCTURED"] = "json"
llm.complete_json("sys", [{"role": "user", "content": "hi"}], schema, active=("ollama_cloud", "m"))
ok(seen[-1][1]["response_format"] == {"type": "json_object"}, "json mode -> json_object, as for Ollama")
del os.environ["LIBRARY_KEEPER_STRUCTURED"]
llm._post = real_post

class Reply:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return json.dumps(self.payload).encode()
got = []
real_open = llm.urllib.request.urlopen
def fake_open(req, **kw):
    got.append(req.full_url)
    return Reply({"data": [{"id": "deepseek-v4.1-flash"}, {"id": "gpt-oss:120b"}, {"id": "new-model"}]})
llm.urllib.request.urlopen = fake_open
llm._model_list_cache.clear()
lst = llm.list_provider_models("ollama_cloud", refresh=True)["models"]
llm.urllib.request.urlopen = real_open
ok(got[-1] == "https://ollama.com/v1/models", "model list from ollama.com/v1/models")
by = {m["value"]: m for m in lst}
ok(by["deepseek-v4.1-flash"].get("note") and by["gpt-oss:120b"].get("note") and not by["new-model"].get("note")
   and all(m["group"] == "متاحة في Ollama Cloud" for m in lst), "known Arabic notes carried over to the cloud names")

r = c.post("/api/admin/llm", json={"provider": "ollama_cloud", "model": "deepseek-v4.1-flash"})
ok(r.status_code == 200 and llm.get_active() == ("ollama_cloud", "deepseek-v4.1-flash"), "chosen and saved from the admin page")

llm.set_key("ollama_cloud", "")
print(f"TOTAL PASS={P} FAIL={F}")
