"""Visitor wording: the bot never mentions the internal "book card" (Ibrahim, 2026-09-27)."""
import os, sys
for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LIBRARY_KEEPER_PROVIDER", "LIBRARY_KEEPER_MODEL", "LIBRARY_KEEPER_LLM"]:
    os.environ.pop(k, None)
sys.path.insert(0, os.getcwd())
import models, llm, chat, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

V = chat.visitor_text
cases = {
    "حسب بطاقته هو رواية سريعة.": "حسب القرّاء هو رواية سريعة.",
    "على حسب البطاقة الإيقاع بطيء": "حسب القرّاء الإيقاع بطيء",
    "حسب بطاقتها أجواؤها حزينة.": "حسب القرّاء أجواؤها حزينة.",
    "مذكور في البطاقة إنه خفيف.": "يقول القرّاء إنه خفيف.",
    "بطاقة الكتاب تقول إنه مناسب للمبتدئين.": "القرّاء يقولون إنه مناسب للمبتدئين.",
    "البطاقة تقول إن إيقاعه سريع": "القرّاء يقولون إن إيقاعه سريع",
    "ما عندي في البطاقة تفاصيل عن النهاية.": "ما عندي في آراء القرّاء تفاصيل عن النهاية.",
    "رواية ممتعة وسريعة.": "رواية ممتعة وسريعة.",
}
for src, want in cases.items():
    ok(V(src) == want, f"rewrite: {src} -> {V(src)}")
ok(all("بطاق" not in V(s) for s in cases), "no card wording survives")

ok("لا تذكر له أبدًا كلمة \"بطاقة\"" in chat.RESPOND_SYSTEM and "حسب القرّاء" in chat.RESPOND_SYSTEM, "prompt forbids the word and asks for 'according to readers'")
ok("بطاقته فقط" not in chat.RESPOND_SYSTEM and "(من البطاقة)" not in chat.RESPOND_SYSTEM, "prompt no longer calls the facts a card")

# end to end through /api/chat with a mocked model that still says "البطاقة"
if os.path.exists(llm.SETTINGS_PATH): os.remove(llm.SETTINGS_PATH)
os.environ["OPENAI_API_KEY"] = "k-o"
c = login(A.app.test_client())
c.post("/api/admin/llm", json={"provider": "openai", "model": "gpt-6-luna"})
EMPTY = llm.clean_prefs({})
systems = []
def fake_complete(system, messages, schema, max_tokens=700, active=None):
    if "intent" in schema["properties"]:
        p = {k: "any" for k in EMPTY}; p.update(genres=["رواية"])
        return {"intent": "recommend", "preferences": p, "mentioned_titles": [], "wants_new_options": False}
    systems.append(system)
    return {"reply": "هذي رواية حلوة، حسب بطاقته إيقاعه سريع، ومذكور في البطاقة إنه خفيف.", "show_book_ids": [],
            "suggestions": ["وش تقول البطاقة؟"]}
llm.complete_json = fake_complete
r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "ابي رواية"}]}).get_json()
ok(r["understanding"] == "ai" and "بطاق" not in r["reply"] and "حسب القرّاء" in r["reply"], "chat reply rewritten: " + r["reply"])
ok(all("بطاق" not in s for s in r["suggestions"]), "suggestion chips rewritten")
guid = systems[-1].split("توجيه لهذا الرد:")[-1]
ok("بطاق" not in guid, "per-intent guidance does not say card")
print(f"\nwording: {P} passed, {F} failed"); sys.exit(1 if F else 0)
