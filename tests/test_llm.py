import os, sys, json, io, sqlite3
sys.path.insert(0, os.getcwd())
os.environ.pop("ANTHROPIC_API_KEY", None)
import models, llm, app as A
from auth_helper import login
c = login(A.app.test_client())
ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg)
con = sqlite3.connect(models.DB_PATH)
EMPTY = {k: [] for k in ["genres","moods_include","moods_exclude","pace","weight_include","weight_exclude","use_cases","reader_level","authors","title_keywords","unsupported_requests"]}
EMPTY["length"] = "any"
def resp(**kw): d = dict(EMPTY); d.update(kw); return d
CANNED = {
 "ما أبغى شي حزين": resp(moods_exclude=["sad"]),
 "ابي روايه تونسني": resp(genres=["رواية"], moods_include=["funny"], weight_include=["light"]),
 "رواية عن الغربة والحنين للوطن": resp(genres=["رواية"], moods_include=["sad"], unsupported_requests=["الغربة والحنين للوطن"]),
 "كتب غازي القصيبي": resp(authors=["غازي القصيبي"]),
 "شي يخليني ما انام من الحماس": resp(moods_include=["suspenseful"], pace=["fast"]),
 "قيم غلط": resp(moods_include=["happy", "sad"], genres=["روايات"], length="medium"),
}
calls = []
def fake_call(q):
    calls.append(q)
    if q == "خطأ شبكة": raise llm.LLMError("down")
    return CANNED[q]
# 1. no key -> keyword fallback
r = c.post("/api/recommend", json={"query": "أبغى رواية حزينة"}).get_json()
ok(r["understanding"] == "keywords" and 3 <= len(r["results"]) <= 4, "no API key -> keyword mode")
# Updated 2026-09-26 for the multi-provider llm.py: a configured provider + mocked complete_json.
os.environ["ANTHROPIC_API_KEY"] = "test-key"
llm.set_active("anthropic", "claude-haiku-4-5-20251001")
def fake_complete(system, messages, schema, max_tokens=700, active=None):
    q = messages[-1]["content"].replace("<visitor_request>", "").replace("</visitor_request>", "")
    return fake_call(q)
llm.complete_json = fake_complete
def rec(q): return c.post("/api/recommend", json={"query": q}).get_json()
def has_mood(bid, mood):
    return con.execute("select 1 from attribute_evidence where book_id=? and attribute_name='moods' and attribute_value=? and confidence>=0.65 and conflict_seen=0", (bid, mood)).fetchone() is not None
r = rec("ما أبغى شي حزين")
ok(r["understanding"] == "ai", "AI mode when key present")
ok(not any(has_mood(b["book_id"], "sad") for b in r["results"]) and not any("أجواؤه حزينة" in b["reason"] for b in r["results"]), "negation: no sad books, no reason claiming sad")
for _ in range(10):
    r = rec("ما أبغى شي حزين")
    assert not any(has_mood(b["book_id"], "sad") for b in r["results"])
ok(True, "negation holds across 10 shuffled runs")
r = rec("ابي روايه تونسني"); print("   ", [(b["title"], b["reason"]) for b in r["results"]])
ok(all(b["genre"] == "رواية" for b in r["results"] if b["match"] == "direct") and any("مرحة" in b["reason"] for b in r["results"]), "dialect 'تونسني' -> funny novels")
r = rec("رواية عن الغربة والحنين للوطن"); print("   ", r["message"])
ok("الغربة" in (r["message"] or ""), "unsupported topic disclosed to visitor")
r = rec("كتب غازي القصيبي")
ok(all(b["author"] == "غازي القصيبي" for b in r["results"][:3]), "author via AI tokens")
r = rec("شي يخليني ما انام من الحماس"); print("   ", [(b["title"], b["reason"]) for b in r["results"]])
ok(not any("ملهمة" in b["reason"] for b in r["results"]), "'ما انام من الحماس' not treated as inspiring")
cleaned = llm.clean_prefs(CANNED["قيم غلط"])
ok(cleaned["moods_include"] == ["sad"] and cleaned["genres"] == [] and cleaned["length"] is None, "invalid enum values dropped")
ok(llm.clean_prefs(resp(moods_include=["sad"], moods_exclude=["sad"]))["moods_include"] == [], "exclude wins over include")
n = len(calls); rec("كتب غازي القصيبي")
ok(len(calls) == n, "repeat query served from cache (no second API call)")
r = c.post("/api/recommend", json={"query": "خطأ شبكة"}).get_json()
ok(r["understanding"] == "keywords" and len(r["results"]) >= 3, "API error -> keyword fallback")
os.environ["LIBRARY_KEEPER_LLM"] = "off"
ok(rec("ما أبغى شي حزين")["understanding"] == "keywords", "LIBRARY_KEEPER_LLM=off forces keywords")
del os.environ["LIBRARY_KEEPER_LLM"]
# real request path: provider request format and parsing via a fake HTTP layer
import importlib; importlib.reload(llm)
captured = {}
def fake_post(url, body, headers, timeout):
    captured.update(url=url, body=body, timeout=timeout)
    return {"content": [{"type": "tool_use", "name": "respond", "input": resp(moods_include=["warm"])}]}
llm._post = fake_post
out = llm.understand("أبغى شي دافئ")
ok(out and out["moods_include"] == ["warm"], "anthropic tool_use response parsed")
b = captured["body"]
ok(b["tool_choice"]["type"] == "tool" and b["model"] == "claude-haiku-4-5-20251001" and captured["timeout"] > 0, "request uses forced tool, chosen model, a timeout")
ok("<visitor_request>" in json.dumps(b["messages"], ensure_ascii=False), "visitor text wrapped as data")
ok("الحرافيش" not in json.dumps(b, ensure_ascii=False), "inventory never sent to the model")
