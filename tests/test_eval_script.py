"""scripts/eval_model.py works end to end against a fake OpenAI-compatible model (no network)."""
import os, sys, json
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.path.dirname(os.getcwd()), "scripts"))
for k in ["OPENAI_API_KEY", "LIBRARY_KEEPER_STRUCTURED"]:
    os.environ.pop(k, None)
os.environ["OPENAI_API_KEY"] = "k-test"
import eval_model as E
import llm
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

state = {"n": 0}
def fake_post(url, body, headers, timeout):
    state["n"] += 1
    system = body["messages"][0]["content"]
    last = " ".join(m["content"] for m in body["messages"][1:] if m["role"] == "user")
    if "intent" in system and "preferences" in system and "منار" not in system:
        prefs = {"genres": [], "moods_exclude": []}
        if "حزين" in last:
            prefs["moods_exclude"] = ["sad"]
        if "عمرها" in last:
            prefs["audience"] = "children"
        content = json.dumps({"intent": "recommend", "preferences": prefs, "mentioned_titles": [], "wants_new_options": False})
        if state["n"] == 1:
            content = "not json at first"  # exercises the corrective retry
    else:
        ids = [int(x) for x in __import__("re").findall(r'"id": (\d+)', system)][:2]
        content = json.dumps({"reply": "تفضل", "show_book_ids": ids, "suggestions": []})
    return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
llm._post = fake_post

cases = [c for c in json.load(open("../tests/eval/cases.json", encoding="utf-8")) if c["id"] in ("neg-sad", "kid-age", "multi-neg-keep")]
cards = E.models.get_profile_cards()
res = [E.run_case(c, ("openai", "fake"), cards) for c in cases]
by = {r["id"]: r for r in res}
ok(len(by["multi-neg-keep"]["turns"]) == 2, "multi-turn case runs every visitor message")
ok(by["neg-sad"]["fails"] == [], "negation expectation checked and met")
ok(by["kid-age"]["fails"] == [] and by["kid-age"]["books"], "age expectation checked on returned books")
s = E.summarize(res)
ok(s["turns"] == 4 and s["structured_success"] == 1.0 and s["stages"]["interpret"]["retried"] == 1, "summary: success rate and retries counted")
ok(s["stages"]["interpret"]["tokens"] > 0 and s["stages"]["respond"]["median_ms"] is not None, "summary: tokens and latency")
# a failing model is reported as fallback with the stage
llm._post = lambda url, body, headers, timeout: {"choices": [{"message": {"content": "no"}}]}
r = E.run_case(cases[0], ("openai", "fake"), cards)
ok(r["turns"][0]["failed_stage"] == "interpret" and not r["turns"][0]["ai"], "fallback turn records failed stage")
ok(E.summarize([r])["structured_success"] == 0.0, "fallback lowers structured success")
print(f"TOTAL PASS={P} FAIL={F}")
