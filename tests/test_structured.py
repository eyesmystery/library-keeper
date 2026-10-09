"""Structured output: schema enforcement, fallback when a provider refuses it,
per-call diagnostics (llm.last_call) and failed-stage reporting in chat."""
import os, sys, io, json
sys.path.insert(0, os.getcwd())
for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "LIBRARY_KEEPER_STRUCTURED"]:
    os.environ.pop(k, None)
import llm, chat, app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
calls = []
script = []  # queue of responses: dict (JSON body), "400" (HTTPError), or str (message content)
def fake_post(url, body, headers, timeout):
    calls.append(json.loads(json.dumps(body)))
    item = script.pop(0) if script else '{"x": 1}'
    if item == "400":
        raise llm.LLMError("HTTP 400: response_format json_schema not supported")
    return {"choices": [{"message": {"content": item}}], "usage": {"prompt_tokens": 11, "completion_tokens": 3}}
llm._post = fake_post
os.environ.update(OPENAI_API_KEY="k-o", GEMINI_API_KEY="k-g", OPENROUTER_API_KEY="k-r")
msgs = [{"role": "user", "content": "hi"}]

for pid, model in [("openai", "m1"), ("gemini", "m2"), ("openrouter", "m3"), ("ollama", "m4")]:
    calls.clear()
    r = llm.complete_json("sys", msgs, schema, active=(pid, model))
    rf = calls[0].get("response_format") or {}
    ok(r == {"x": 1} and rf.get("type") == "json_schema" and rf["json_schema"]["schema"] == schema,
       f"{pid}: schema enforced by default")
ok(llm.last_call["ok"] and llm.last_call["format"] == "json_schema" and llm.last_call["usage"]["prompt_tokens"] == 11
   and isinstance(llm.last_call["ms"], int) and not llm.last_call["retried"], "last_call records format, usage, time")

os.environ["LIBRARY_KEEPER_STRUCTURED"] = "json"
calls.clear(); llm.complete_json("sys", msgs, schema, active=("ollama", "m5"))
ok(calls[0]["response_format"] == {"type": "json_object"}, "json mode: ollama gets json_object (old behaviour)")
calls.clear(); llm.complete_json("sys", msgs, schema, active=("gemini", "m6"))
ok("response_format" not in calls[0], "json mode: gemini gets no response_format (old behaviour)")
del os.environ["LIBRARY_KEEPER_STRUCTURED"]

# provider refuses json_schema -> json_object, remembered for that model
calls.clear(); script[:] = ["400", '{"x": 7}']
r = llm.complete_json("sys", msgs, schema, active=("ollama", "old-model"))
ok(r == {"x": 7} and calls[1]["response_format"] == {"type": "json_object"} and "refused" in llm.last_call["format"],
   "HTTP 400 on json_schema -> retried with json_object")
calls.clear(); llm.complete_json("sys", msgs, schema, active=("ollama", "old-model"))
ok(len(calls) == 1 and calls[0]["response_format"] == {"type": "json_object"}, "refusal remembered: next call skips json_schema")
calls.clear(); script[:] = ["400", '{"x": 8}']
llm.complete_json("sys", msgs, schema, active=("gemini", "g-old"))
ok("response_format" not in calls[1], "gemini refusal -> plain prompt JSON")

# invalid first reply -> one corrective retry, recorded
calls.clear(); script[:] = ["<think>hmm</think> sure!", '{"x": 3}']
r = llm.complete_json("sys", msgs, schema, active=("openai", "m1"))
ok(r == {"x": 3} and llm.last_call["retried"] and len(calls) == 2 and "not valid JSON" in llm.last_call["first_error"],
   "corrective retry used and recorded with the first error")
calls.clear(); script[:] = ["nope", "still nope"]
try:
    llm.complete_json("sys", msgs, schema, active=("openai", "m1")); ok(False, "two bad replies raise")
except llm.LLMError:
    ok(not llm.last_call["ok"] and llm.last_call["error"] and len(calls) == 2, "two bad replies -> error recorded, no third call")

# other HTTP errors are not treated as a schema refusal
calls.clear(); script[:] = []
def fail500(url, body, headers, timeout):
    calls.append(body); raise llm.LLMError("HTTP 500: boom")
llm._post = fail500
try:
    llm.complete_json("sys", msgs, schema, active=("openai", "m9")); ok(False, "500 raises")
except llm.LLMError:
    ok(len(calls) == 1 and ("openai", "m9") not in llm._schema_unsupported, "HTTP 500 is not a schema refusal")
llm._post = fake_post

# chat reports which stage failed
llm.get_active = lambda: ("openai", "m1")
script[:] = ["bad", "bad"]
out = A.run_chat([{"role": "user", "content": "ابغى رواية"}], active=("openai", "m1"))
ok(out["understanding"] == "keywords" and out["_debug"]["failed_stage"] == "interpret", "interpret failure reported as stage 'interpret'")
good_interpret = json.dumps({"intent": "recommend", "preferences": {"genres": ["رواية"]}, "mentioned_titles": [], "wants_new_options": False})
script[:] = [good_interpret, "bad", "bad"]
out = A.run_chat([{"role": "user", "content": "ابغى رواية"}], active=("openai", "m1"))
ok(out["_debug"]["failed_stage"] == "respond" and out["_debug"]["call"]["retried"], "respond failure reported as stage 'respond'")
script[:] = [good_interpret, json.dumps({"reply": "تفضل", "show_book_ids": [], "suggestions": []})]
out = A.run_chat([{"role": "user", "content": "ابغى رواية"}], active=("openai", "m1"))
ok(out["understanding"] == "ai" and set(out["_debug"]["calls"]) == {"interpret", "respond"}
   and "ms" in out["_debug"]["calls"]["respond"], "successful turn records both calls")
ok("ابغى" not in json.dumps(out["_debug"]["calls"], ensure_ascii=False), "call diagnostics hold no visitor text")
print(f"TOTAL PASS={P} FAIL={F}")
