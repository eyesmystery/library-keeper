import os, sys, json, io
sys.path.insert(0, os.getcwd())
import llm
P = F = 0
def ok(c, m):
    global P, F; print(("PASS " if c else "FAIL ") + m); P += bool(c); F += (not c)
pj = llm.parse_json_text
ok(pj('<think>نفكر… {"x": 1} ممكن</think>\n{"intent": "recommend"}') == {"intent": "recommend"}, "think block with braces ignored")
ok(pj('Sure! Here is the JSON:\n```json\n{"a": {"b": "}"}}\n```\nHope it helps {not json}') == {"a": {"b": "}"}}, "prose + fence + brace inside string")
ok(pj('{"reply": "قال \\"هلا\\" {ok}", "n": 2}') == {"reply": 'قال "هلا" {ok}', "n": 2}, "escaped quotes and braces in strings")
ok(pj('{"small": 1} and then {"intent": "clarify", "preferences": {"genres": []}}')["intent"] == "clarify", "largest object chosen")
for bad in ["", "   ", "<think>only thinking, never answered", "no json here"]:
    try:
        pj(bad); ok(False, f"raises on {bad!r}")
    except llm.LLMError as e:
        ok(True, f"raises on {bad[:20]!r}: {str(e)[:60]}")
# retry path
class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass
replies = []; bodies = []
def fake_urlopen(req, timeout=None, **kw):
    bodies.append(json.loads(req.data))
    return FakeResp(json.dumps({"choices": [{"message": {"content": replies.pop(0)}}]}).encode())
llm.urllib.request.urlopen = fake_urlopen
schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
replies[:] = ["أكيد! الإجابة: x تساوي 1", '{"x": 1}']
out = llm.complete_json("sys", [{"role": "user", "content": "hi"}], schema, active=("ollama", "deepseek-v4.1-flash:cloud"))
ok(out == {"x": 1} and len(bodies) == 2 and "not a valid JSON" in bodies[1]["messages"][-1]["content"], "one corrective retry succeeds")
ok(bodies[0]["max_tokens"] >= 1500, "token budget raised for reasoning models")
bodies.clear(); replies[:] = ["nope", "still nope"]
try:
    llm.complete_json("sys", [{"role": "user", "content": "hi"}], schema, active=("ollama", "m")); ok(False, "fails after retry")
except llm.LLMError as e:
    ok(len(bodies) == 2 and "still nope" in llm.last_error["message"], "gives up after one retry; error shows snippet")
bodies.clear(); replies[:] = ['<think>hmm</think>{"x": 3}']
ok(llm.complete_json("sys", [{"role": "user", "content": "hi"}], schema, active=("ollama", "m")) == {"x": 3} and len(bodies) == 1, "no retry when first reply parses")
print(f"TOTAL PASS={P} FAIL={F}")
