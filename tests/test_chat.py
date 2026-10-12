import os, sys, json, io, sqlite3
for k in ["ANTHROPIC_API_KEY","OPENAI_API_KEY","GEMINI_API_KEY","LIBRARY_KEEPER_PROVIDER","LIBRARY_KEEPER_MODEL","LIBRARY_KEEPER_LLM"]:
    os.environ.pop(k, None)
sys.path.insert(0, os.getcwd())
import models, llm, chat, app as A
from auth_helper import login
c = login(A.app.test_client())
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)
if os.path.exists(llm.SETTINGS_PATH): os.remove(llm.SETTINGS_PATH)
con = sqlite3.connect(models.DB_PATH)
avail = {r[0] for r in con.execute("select book_id from books where active=1 and quantity>0")}

# ---------- provider transport (fake HTTP)
class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass
cap = {}
def fake_urlopen(req, timeout=None, **kw):
    url = req.full_url if hasattr(req, "full_url") else req
    if isinstance(url, str) and url.endswith("/api/tags"):
        return FakeResp(json.dumps({"models": [{"name": "qwen-test:7b"}]}).encode())
    cap.update(url=req.full_url, headers={k.lower(): v for k, v in req.header_items()}, body=json.loads(req.data), timeout=timeout)
    if "anthropic" in req.full_url:
        return FakeResp(json.dumps({"content": [{"type": "tool_use", "name": "respond", "input": {"x": 1}}]}).encode())
    return FakeResp(json.dumps({"choices": [{"message": {"content": "```json\n{\"x\": 2}\n```"}}]}).encode())
llm.urllib.request.urlopen = fake_urlopen
schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
os.environ.update(ANTHROPIC_API_KEY="k-a", OPENAI_API_KEY="k-o", GEMINI_API_KEY="k-g")
r = llm.complete_json("sys", [{"role": "user", "content": "hi"}], schema, active=("anthropic", "claude-haiku-4-5-20251001"))
ok(r == {"x": 1} and cap["url"].startswith("https://api.anthropic.com") and cap["headers"]["x-api-key"] == "k-a" and cap["body"]["tool_choice"]["name"] == "respond", "anthropic: tool-forced JSON, key header")
r = llm.complete_json("sys", [{"role": "user", "content": "hi"}], schema, active=("openai", "gpt-6-luna"))
ok(r == {"x": 2} and cap["url"] == "https://api.openai.com/v1/chat/completions" and cap["headers"]["authorization"] == "Bearer k-o" and cap["body"]["response_format"]["type"] == "json_schema" and cap["body"]["response_format"]["json_schema"]["schema"] == schema and "max_completion_tokens" in cap["body"] and "temperature" not in cap["body"], "openai: schema-enforced JSON, max_completion_tokens, bearer key, fenced JSON parsed")
r = llm.complete_json("sys", [{"role": "user", "content": "hi"}], schema, active=("gemini", "gemini-3.5-flash-lite"))
ok(r == {"x": 2} and "generativelanguage.googleapis.com/v1beta/openai/chat/completions" in cap["url"] and cap["headers"]["authorization"] == "Bearer k-g", "gemini: OpenAI-compatible endpoint")
r = llm.complete_json("sys", [{"role": "user", "content": "hi"}], schema, active=("ollama", "qwen-test:7b"))
ok(r == {"x": 2} and cap["url"] == "http://localhost:11434/v1/chat/completions" and cap["timeout"] == 30.0, "ollama: local endpoint, longer timeout")
ok(llm.ollama_models() == ["qwen-test:7b"], "ollama installed models listed")
ok(llm._normalize_messages([{"role": "assistant", "content": "a"}, {"role": "user", "content": "b"}, {"role": "user", "content": "c"}]) == [{"role": "user", "content": "b\nc"}], "messages normalized to start with user and alternate")
try:
    llm.parse_json_text("not json"); ok(False, "bad JSON raises")
except llm.LLMError: ok(True, "bad JSON raises LLMError")

# ---------- settings + admin API
st = c.get("/api/admin/llm").get_json()
ok(st["active"]["provider"] == "anthropic", "default active = first provider with a key")
ok("k-a" not in json.dumps(st) and "k-o" not in json.dumps(st), "status never exposes keys")
ok(c.post("/api/admin/llm", json={"provider": "gemini", "model": "gemini-3.8-flash"}).status_code == 200 and llm.get_active() == ("gemini", "gemini-3.8-flash"), "admin switches provider/model")
ok("k-g" not in open(llm.SETTINGS_PATH).read(), "settings file holds no secrets")
ok(c.post("/api/admin/llm", json={"provider": "evil", "model": "x"}).status_code == 400, "unknown provider rejected")
ok(c.post("/api/admin/llm", json={"provider": "openai", "model": "x; rm -rf"}).status_code == 400, "invalid model name rejected")
c.post("/api/admin/llm", json={"provider": "none", "model": ""})
ok(llm.get_active() is None, "'none' turns AI off")

# ---------- chat with mocked model
c.post("/api/admin/llm", json={"provider": "openai", "model": "gpt-6-luna"})
EMPTY = llm.clean_prefs({})
def prefs(**kw): d = {k: (v if v is not None else "any") for k, v in EMPTY.items()}; d.update(kw); return d
script = {}
calls = []
def fake_complete(system, messages, schema, max_tokens=700, active=None):
    kind = "interpret" if "intent" in schema["properties"] else "respond"
    calls.append({"kind": kind, "system": system, "messages": messages})
    step = script[kind]
    if isinstance(step, Exception): raise step
    return step(system, messages) if callable(step) else step
llm.complete_json = fake_complete
def say(msgs): return c.post("/api/chat", json={"messages": msgs}).get_json()

script["interpret"] = {"intent": "smalltalk", "preferences": prefs(), "mentioned_titles": [], "wants_new_options": False}
script["respond"] = {"reply": "هلا والله! وش مزاجك اليوم؟", "show_book_ids": [], "suggestions": ["شي خفيف", "رواية تشد"]}
r = say([{"role": "user", "content": "السلام عليكم"}])
ok(r["understanding"] == "ai" and r["books"] == [] and r["suggestions"] == ["شي خفيف", "رواية تشد"], "smalltalk: reply, no books, chips")

script["interpret"] = {"intent": "recommend", "preferences": prefs(genres=["رواية"], moods_include=["funny"]), "mentioned_titles": [], "wants_new_options": False}
def pick_with_bad_ids(system, messages):
    facts = json.loads(system.split("<facts>\n")[1].split("\n</facts>")[0])
    ids = [f["id"] for f in facts][:2]
    return {"reply": "هذي روايات مرحة", "show_book_ids": ids + [9999, "abc", ids[0]], "suggestions": []}
script["respond"] = pick_with_bad_ids
r = say([{"role": "user", "content": "ابي روايه تونسني"}])
ids = [b["book_id"] for b in r["books"]]
ok(len(ids) == 2 and 9999 not in ids and len(set(ids)) == len(ids) and set(ids) <= avail, "invented/duplicate ids stripped; only in-stock books shown")
facts = json.loads(calls[-1]["system"].split("<facts>\n")[1].split("\n</facts>")[0])
ok(len(facts) <= 10 and all(set(f) >= {"confirmed", "impression", "available"} for f in facts), "facts limited (≤10) with card tiers")
ok(all(f["available"] for f in facts), "recommend facts only in-stock")
# every 'confirmed' label backed by High non-conflict evidence
bad = 0
for f in facts:
    for label in f["confirmed"]:
        if label.startswith("أجواء "):
            mood = next(k for k, v in A.MOOD_LABELS_FEM.items() if f"أجواء {v}" == label)
            row = con.execute("select max(confidence) from attribute_evidence where book_id=? and attribute_name='moods' and attribute_value=? and conflict_seen=0", (f["id"], mood)).fetchone()[0]
            bad += not (row and row >= 0.85)
ok(bad == 0, "confirmed mood labels backed by High-tier evidence")
ok("الحرافيش" not in calls[-1]["system"] or any(f["title"] == "الحرافيش" for f in facts), "no book text outside the facts is sent")

script["interpret"] = {"intent": "recommend", "preferences": prefs(moods_exclude=["sad"]), "mentioned_titles": [], "wants_new_options": False}
def show_all(system, messages):
    facts = json.loads(system.split("<facts>\n")[1].split("\n</facts>")[0])
    return {"reply": "تفضل", "show_book_ids": [f["id"] for f in facts][:4], "suggestions": []}
script["respond"] = show_all
sad_ok = True
for _ in range(8):
    for f in json.loads(say([{"role": "user", "content": "ما أبغى شي حزين"}]) and calls[-1]["system"].split("<facts>\n")[1].split("\n</facts>")[0]):
        if con.execute("select 1 from attribute_evidence where book_id=? and attribute_name='moods' and attribute_value='sad' and confidence>=0.65 and conflict_seen=0", (f["id"],)).fetchone():
            sad_ok = False
ok(sad_ok, "negation: sad books never reach the model's candidates (8 runs)")

r1 = say([{"role": "user", "content": "رواية"}])
shown = [b["book_id"] for b in r1["books"]]
script["interpret"] = {"intent": "recommend", "preferences": prefs(genres=["رواية"]), "mentioned_titles": [], "wants_new_options": True}
r2 = say([{"role": "user", "content": "رواية"}, {"role": "assistant", "content": "تفضل", "book_ids": shown}, {"role": "user", "content": "ورّني غيرها"}])
ok(shown and not set(shown) & {b["book_id"] for b in r2["books"]}, "'ورّني غيرها' gives new books")
ok("الكتب التي عرضتُها" in calls[-1]["messages"][1]["content"], "model sees which books were shown")

book3 = con.execute("select title from books where book_id=3").fetchone()[0]
script["interpret"] = {"intent": "about_book", "preferences": prefs(), "mentioned_titles": [book3], "wants_new_options": False}
script["respond"] = lambda s, m: {"reply": "عنه كذا", "show_book_ids": [3], "suggestions": []}
r = say([{"role": "user", "content": f"وش رايك في {book3}؟"}])
facts = json.loads(calls[-1]["system"].split("<facts>\n")[1].split("\n</facts>")[0])
ok(facts[0]["id"] == 3 and "سأل عنه" in facts[0]["why_it_matches"] and r["books"][0]["book_id"] == 3, "about_book: asked book is first fact and can be shown")

script["interpret"] = {"intent": "off_topic", "preferences": prefs(), "mentioned_titles": [], "wants_new_options": False}
script["respond"] = {"reply": "أعتذر، أنا هنا للكتب فقط.", "show_book_ids": [], "suggestions": ["اقترح لي رواية"]}
r = say([{"role": "user", "content": "مين بيفوز بالدوري؟"}])
ok(r["books"] == [] and "لا تعرض كتبًا" in calls[-1]["system"], "off-topic: guided back, no books")

script["interpret"] = {"intent": "clarify", "preferences": prefs(), "mentioned_titles": [], "wants_new_options": False}
script["respond"] = {"reply": "سؤال؟", "show_book_ids": [], "suggestions": []}
hist = []
for i in range(3):
    hist += [{"role": "user", "content": f"ما أدري {i}"}, {"role": "assistant", "content": "سؤال؟", "book_ids": []}]
say(hist + [{"role": "user", "content": "أي شي"}])
ok("اقترح الآن" in calls[-1]["system"], "after 3 questions without books the model must recommend")

# start buttons and asking before suggesting (Ibrahim 2026-10-11)
n = len(calls)
r = say([{"role": "user", "content": "رشّحي لي كتاب يناسب ذوقي أو مزاجي"}])
ok(len(calls) == n and r["books"] == [] and "؟" in r["reply"] and "ورّني كتب على طول" in r["suggestions"], "a start button: a fixed first question, no model call, no books")
r = say([{"role": "user", "content": "رشّح لي كتاب يناسب ذوقي أو مزاجي"}])
ok(len(calls) == n and r["books"] == [] and "؟" in r["reply"], "the button's earlier wording («رشّح») still opens its path")
ok(chat.to_manar("رشّح لي حسب مزاجي") == "رشّحي لي حسب مزاجي" and chat.to_manar("رشح لي غيره") == "رشّحي لي غيره"
   and chat.to_manar("قول لي أكثر") == "قولي لي أكثر" and chat.to_manar("أبغى رواية") == "أبغى رواية",
   "the visitor's taps speak to Manar as a woman: «رشّحي لي»")
ok("أبشر" in chat.RESPOND_SYSTEM_AR and "رشّحي لي" in chat.RESPOND_SYSTEM_AR, "reply rules: the visitor in the masculine («أبشر»), Manar asked in the feminine")
r = say([{"role": "user", "content": "أدوّر كتاب معيّن"}])
ok(r["books"] == [] and r["suggestions"] == [] and "اسم الكتاب" in r["reply"], "«a specific book» asks for the title and offers no taps")
ok(say([{"role": "user", "content": "أبغى كتاب هدية"}])["books"] == [], "gift path asks first")
script["interpret"] = {"intent": "clarify", "preferences": prefs(), "mentioned_titles": [], "wants_new_options": False}
script["respond"] = show_all
r = say([{"role": "user", "content": "أبغى أبدأ قراءة"}, {"role": "assistant", "content": "سؤال؟", "book_ids": []},
         {"role": "user", "content": "ما أدري"}])
ok(r["books"] == [] and "لا تقترح أي كتاب" in calls[-1]["system"], "a vague answer after one question: one more question, no book even if the model picked some")
script["interpret"] = {"intent": "recommend", "preferences": prefs(use_cases=["gift"]), "mentioned_titles": [], "wants_new_options": False}
r = say([{"role": "user", "content": "أبغى كتاب هدية"}, {"role": "assistant", "content": "سؤال؟", "book_ids": []},
         {"role": "user", "content": "ما أدري وش يحب"}])
ok(r["books"] == [] and "لا تقترح أي كتاب" in calls[-1]["system"], "«ما أدري وش يحب» gets one more question even when read as a recommendation")
script["interpret"] = {"intent": "clarify", "preferences": prefs(), "mentioned_titles": [], "wants_new_options": False}
r = say([{"role": "user", "content": "أبغى أبدأ قراءة"}, {"role": "assistant", "content": "سؤال؟", "book_ids": []},
         {"role": "user", "content": "ورّني كتب على طول"}])
ok("اقترح الآن" in calls[-1]["system"], "«ورّني كتب على طول» skips the remaining questions")
r = say([{"role": "user", "content": "ما أدري"}, {"role": "assistant", "content": "سؤال؟", "book_ids": []},
         {"role": "user", "content": "ما زلت ما أدري"}, {"role": "assistant", "content": "سؤال؟", "book_ids": []},
         {"role": "user", "content": "أي شي"}])
ok("اقترح الآن" in calls[-1]["system"], "after two questions a vague request gets books")

script["interpret"] = {"intent": "recommend", "preferences": prefs(genres=["رواية"], unsupported_requests=["حبكة غير خطية"]), "mentioned_titles": [], "wants_new_options": False}
script["respond"] = show_all
say([{"role": "user", "content": "رواية حبكتها غير خطية"}])
ok("حبكة غير خطية" in calls[-1]["system"] and "البحث لا يستطيع" in calls[-1]["system"] and "لا تقل إن بياناتها غير موجودة" in calls[-1]["system"], "unsupported wish describes search limitations without claiming missing data")

script["interpret"] = llm.LLMError("down")
r = say([{"role": "user", "content": "أبغى رواية حزينة"}])
ok(r["understanding"] == "keywords" and 3 <= len(r["books"]) <= 4, "model failure -> keyword fallback with books")
script["interpret"] = {"intent": "recommend", "preferences": prefs(genres=["رواية"]), "mentioned_titles": [], "wants_new_options": False}
script["respond"] = {"reply": "", "show_book_ids": [], "suggestions": []}
ok(say([{"role": "user", "content": "رواية"}])["understanding"] == "keywords", "empty model reply -> fallback")

ok(c.post("/api/chat", json={"messages": []}).status_code == 400 and c.post("/api/chat", json={"messages": [{"role": "assistant", "content": "x"}]}).status_code == 400, "malformed chat input -> 400")
ok(len(say([{"role": "user", "content": "x" * 5000}])["reply"]) > 0, "very long message handled (truncated)")

c.post("/api/admin/llm", json={"provider": "none", "model": ""})
r = say([{"role": "user", "content": "أبغى رواية حزينة"}])
ok(r["understanding"] == "keywords" and r["books"], "AI off -> keyword chat still works")
t = c.post("/api/admin/llm/test", json={"message": "رواية"}).get_json()
ok("seconds" in t and "reply" in t, "admin quick test endpoint")
ok(c.get("/").status_code == 200 and b"chatLog" in c.get("/").data and c.get("/admin").status_code == 200, "pages served")
os.remove(llm.SETTINGS_PATH)
print(f"TOTAL PASS={P} FAIL={F}")
