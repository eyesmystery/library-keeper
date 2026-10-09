"""Model dropdown data: static fallback plus live provider-account model lists."""
import json, os, sys
sys.path.insert(0, os.getcwd())
for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"]:
    os.environ.pop(k, None)
import llm, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

llm.ollama_models = lambda timeout=1.5: ["qwen3.8:27b", "my-own-model:latest"]
llm.codex_login_status = lambda max_age=60: (False, "x")
st = {p["id"]: p for p in login(A.app.test_client()).get("/api/admin/llm").get_json()["providers"]}
ok(all("model_options" in p for p in st.values()), "every provider has model options")
ok([o["value"] for o in st["anthropic"]["model_options"]] == llm.PROVIDERS["anthropic"]["suggested_models"], "API providers list their suggested models")
ol = st["ollama"]["model_options"]
installed = [o for o in ol if o["group"] == "مثبتة على جهازك"]
suggested = [o for o in ol if o["group"] == "مقترحة للعربية"]
ok([o["value"] for o in installed] == ["qwen3.8:27b", "my-own-model:latest"], "Ollama: installed models first")
ok(len(suggested) == 10 and len({o["value"] for o in suggested}) == 10, "Ollama: exactly 10 Arabic suggestions")
q = next(o for o in suggested if o["value"] == "qwen3.8:27b")
k = next(o for o in suggested if o["value"] == "kimi-k3:cloud")
ok(q["installed"] and q["hint"] is None, "suggested + installed -> no install hint")
ok(not k["installed"] and "ollama pull kimi-k3:cloud" in k["hint"], "not installed -> exact pull command")
ok(all(o["label"] == o["value"] and o["note"] for o in suggested), "option shows the exact model name; description in a note")
ok(st["custom"]["model_options"] == [], "custom provider: free text only")

class Reply:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.payload).encode()

real_open = llm.urllib.request.urlopen
llm.urllib.request.urlopen = lambda req, **kwargs: Reply({"models": [
    {"name": "local-one"}, {"name": "local-two"}, {"name": "local-three"}, {"name": "local-four"}
]})
llm._model_list_cache.clear()
ollama_live = llm.list_provider_models("ollama", refresh=True)
ok([m["value"] for m in ollama_live["models"][:4]] == ["local-one", "local-two", "local-three", "local-four"]
   and len(ollama_live["models"]) >= 8,
   "Ollama refresh keeps installed models first and adds enough suggestions for the expanded picker")
llm.urllib.request.urlopen = real_open

seen = []
def fake_open(req, **kwargs):
    seen.append((req.full_url, dict(req.header_items()), kwargs))
    return Reply({"data": [
        {"id": "gpt-chat-new", "created": 20},
        {"id": "text-embedding-3-small", "created": 30},
        {"id": "gpt-chat-old", "created": 10},
    ]})

real_open, real_key = llm.urllib.request.urlopen, llm.get_key
llm.urllib.request.urlopen = fake_open
llm.get_key = lambda pid: "test-provider-key"
llm._model_list_cache.clear()
live = llm.list_provider_models("openai")
ok([m["value"] for m in live["models"]] == ["gpt-chat-new", "gpt-chat-old"],
   "OpenAI live list is newest first and hides embedding models")
ok(seen[-1][0] == "https://api.openai.com/v1/models" and
   dict((k.lower(), v) for k, v in seen[-1][1].items())["authorization"] == "Bearer test-provider-key",
   "OpenAI models request uses the official endpoint and server-side key")
cached = llm.list_provider_models("openai")
ok(cached["cached"] and len(seen) == 1, "successful model list is cached")
llm.list_provider_models("openai", refresh=True)
ok(len(seen) == 2, "manual refresh bypasses the cache")

def anthropic_open(req, **kwargs):
    seen.append((req.full_url, dict(req.header_items()), kwargs))
    return Reply({"data": [{"id": "claude-new", "display_name": "Claude New"}]})
llm.urllib.request.urlopen = anthropic_open
llm._model_list_cache.clear()
anthropic = llm.list_provider_models("anthropic")
headers = dict((k.lower(), v) for k, v in seen[-1][1].items())
ok(anthropic["models"][0]["value"] == "claude-new" and "Claude New" in anthropic["models"][0]["label"],
   "Anthropic display name and exact model id are both kept")
ok("/v1/models?limit=1000" in seen[-1][0] and headers["anthropic-version"] == "2023-06-01" and headers["x-api-key"] == "test-provider-key",
   "Anthropic list request uses its required headers")

llm.urllib.request.urlopen, llm.get_key = real_open, real_key
llm._model_list_cache.clear()
real_list = llm.list_provider_models
llm.list_provider_models = lambda pid, refresh=False: {"models": [{"value": "fresh-model", "label": "fresh-model",
    "group": "متاحة من حسابك", "installed": True}], "total": 1, "truncated": False, "cached": False}
r = login(A.app.test_client()).get("/api/admin/llm/models?provider=openai&refresh=1")
ok(r.status_code == 200 and r.get_json()["models"][0]["value"] == "fresh-model", "manager API returns live model list")
ok(A.app.test_client().get("/api/admin/llm/models?provider=openai").status_code == 401, "live model list requires sign-in")
llm.list_provider_models = real_list
print(f"TOTAL PASS={P} FAIL={F}")
