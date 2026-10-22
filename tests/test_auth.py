"""Admin protection: sign-in, roles, CSRF, throttling, first-run setup, users, audit, rate limits, budget."""
import os, sys, json
sys.path.insert(0, os.getcwd())
for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"]:
    os.environ.pop(k, None)
import models, auth, limits, llm, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

anon = A.app.test_client()
# first run
s = anon.get("/api/admin/session")
ok(s.status_code == 401 and s.get_json()["needs_setup"], "no accounts -> session says setup needed")
ok(anon.get("/api/admin/catalog").status_code == 401 and anon.get("/api/admin/llm").status_code == 401, "admin APIs need sign-in")
ok(anon.patch("/api/books/1", json={"price": 1}).status_code == 401, "book edits need sign-in")
ok(anon.get("/api/books").status_code == 200 and anon.post("/api/recommend", json={"query": "رواية"}).status_code == 200,
   "visitor endpoints stay public")
remote = A.app.test_client()
r = remote.post("/api/admin/setup", json={"username": "boss", "name": "المدير", "password": "Strong-pass-9"},
                environ_base={"REMOTE_ADDR": "192.168.1.50"})
ok(r.status_code == 403, "first account only from this computer")
r = anon.post("/api/admin/setup", json={"username": "boss", "name": "المدير", "password": "short"})
ok(r.status_code == 400, "weak password rejected")
r = anon.post("/api/admin/setup", json={"username": "boss", "name": "إبراهيم", "password": "Strong-pass-9"})
ok(r.status_code == 200 and r.get_json()["user"]["role"] == "manager", "first manager created and signed in")
ok(anon.post("/api/admin/setup", json={"username": "x2", "name": "x", "password": "Strong-pass-9"}).status_code == 409,
   "setup cannot run twice")
csrf = r.get_json()["csrf"]
ok(anon.get("/api/admin/catalog").status_code == 200, "signed-in manager reads the catalog")
ok(anon.post("/api/admin/usage", json={"daily_model_calls": 5}).status_code == 403, "POST without CSRF token refused")
ok(anon.post("/api/admin/usage", json={"daily_model_calls": 5}, headers={"X-CSRF-Token": "wrong"}).status_code == 403, "wrong CSRF token refused")
ok(anon.post("/api/admin/usage", json={"daily_model_calls": 500}, headers={"X-CSRF-Token": csrf}).status_code == 200, "correct CSRF token accepted")
h = {"X-CSRF-Token": csrf}
row = models.get_connection().execute("select password_hash from staff_users where username='boss'").fetchone()[0]
ok(row.startswith("scrypt$") and "Strong-pass-9" not in row, "password stored as scrypt hash only")

# staff account
r = anon.post("/api/admin/users", json={"username": "sara", "name": "سارة", "role": "staff", "password": "Staff-pass-2"}, headers=h)
ok(r.status_code == 200, "manager adds a staff account")
ok(anon.post("/api/admin/users", json={"username": "sara", "name": "x", "role": "staff", "password": "Staff-pass-2"}, headers=h).status_code == 400,
   "duplicate username refused")
sara_id = r.get_json()["user"]["user_id"]
st = A.app.test_client()
r = st.post("/api/admin/login", json={"username": "SARA", "password": "Staff-pass-2"})
ok(r.status_code == 200 and r.get_json()["user"]["role"] == "staff", "staff signs in (username case-insensitive)")
sh = {"X-CSRF-Token": r.get_json()["csrf"]}
ok(st.get("/api/admin/catalog").status_code == 200 and st.get("/api/admin/books/3").status_code == 200, "staff: catalog and cards")
ok(st.patch("/api/books/3", json={"quantity": 4}, headers=sh).status_code == 200, "staff: inventory edit")
ev = st.get("/api/admin/books/3").get_json()["attributes"][5]["evidence"][0]["evidence_id"]
attr = st.get("/api/admin/books/3").get_json()["attributes"][5]["key"]
r = st.post(f"/api/admin/books/3/attributes/{attr}", json={"action": "confirm", "evidence_id": ev, "reviewer": "مزوّر"}, headers=sh)
row = models.get_connection().execute("select verified_by from attribute_evidence where evidence_id=?", (ev,)).fetchone()[0]
ok(r.status_code == 200 and row == "سارة", "review recorded under the real signed-in name, not a typed one")
for path in ["/api/admin/import/history", "/api/admin/stats"]:
    ok(st.get(path).status_code == 403, f"staff blocked from {path}")
# «الإعدادات» is open to staff (Ibrahim 2026-10-13): model, accounts, usage, test mode, audit log
for path in ["/api/admin/llm", "/api/admin/users", "/api/admin/testlog", "/api/admin/audit", "/api/admin/usage"]:
    ok(st.get(path).status_code == 200, f"staff may open {path} (settings)")

# disable -> immediate sign-out; last manager protected
ok(anon.post(f"/api/admin/users/{sara_id}", json={"active": False}, headers=h).status_code == 200, "manager disables staff")
ok(st.get("/api/admin/catalog").status_code == 401, "disabled account loses access immediately")
ok(A.app.test_client().post("/api/admin/login", json={"username": "sara", "password": "Staff-pass-2"}).status_code == 401, "disabled account cannot sign in")
boss_id = auth.list_users()[0]["user_id"]
ok(anon.post(f"/api/admin/users/{boss_id}", json={"active": False}, headers=h).status_code == 400, "the last active manager cannot be disabled")
ok(anon.post(f"/api/admin/users/{boss_id}", json={"role": "staff"}, headers=h).status_code == 400, "the last manager cannot be demoted")
ok(any(u["username"] == "sara" and u["disabled_at"] for u in auth.list_users()), "disabled user kept with date (history intact)")

# password change signs out other sessions
other = A.app.test_client(); other.post("/api/admin/login", json={"username": "boss", "password": "Strong-pass-9"})
r = anon.post("/api/admin/me/password", json={"current": "Strong-pass-9", "new": "Newer-pass-10"}, headers=h)
ok(r.status_code == 200, "own password changed")
h = {"X-CSRF-Token": r.get_json()["csrf"]}
ok(other.get("/api/admin/catalog").status_code == 401 and anon.get("/api/admin/catalog").status_code == 200,
   "other sessions signed out, this one continues")

# throttling
bad = A.app.test_client()
codes = [bad.post("/api/admin/login", json={"username": "boss", "password": "wrong"}).status_code for _ in range(6)]
r = bad.post("/api/admin/login", json={"username": "boss", "password": "Newer-pass-10"})
ok(codes[-1] == 401 and r.status_code == 401 and "5 دقائق" in r.get_json()["error"], "5 failures lock sign-in for 5 minutes")
auth._failures.clear()
ok(bad.post("/api/admin/login", data="username=boss&password=x", content_type="application/x-www-form-urlencoded").status_code == 400,
   "form posts (cross-site) refused")

# audit log
acts = [a["action"] for a in anon.get("/api/admin/audit").get_json()]
ok({"user.setup", "user.created", "book.update", "card.confirm", "user.updated", "user.password_changed", "budget.set", "login"} <= set(acts),
   "audit log records setup, users, edits, reviews, budget, sign-ins")
ok(not any("pass" in json.dumps(a, ensure_ascii=False).lower() and "***" not in json.dumps(a, ensure_ascii=False)
           for a in anon.get("/api/admin/audit").get_json() if a["action"] == "user.updated"), "audit never stores passwords")
ok(anon.post("/api/admin/logout", headers=h).status_code == 200 and anon.get("/api/admin/catalog").status_code == 401, "sign-out")

# visitor rate limit (per conversation and per address)
limits.reset()
c2 = A.app.test_client()
codes = [c2.post("/api/chat", json={"conversation_id": "conv-abcdef12", "messages": [{"role": "user", "content": "رواية"}]}).status_code for _ in range(31)]
ok(codes[:30] == [200] * 30 and codes[30] == 429, "31st message in one conversation within 10 min -> 429")
r = c2.post("/api/chat", json={"conversation_id": "conv-abcdef12", "messages": [{"role": "user", "content": "رواية"}]})
ok("انتظر" in r.get_json()["reply"], "friendly Arabic message on rate limit")
limits.reset()
limits.PER_ADDRESS = (5, 600)
codes = [c2.post("/api/recommend", json={"query": "رواية"}).status_code for _ in range(6)]
ok(codes[-1] == 429, "per-address limit applies to /api/recommend too")
limits.PER_ADDRESS = (120, 600); limits.reset()

# daily budget -> keyword fallback, counted
calls = []
def fake_complete(system, messages, schema, max_tokens=700, active=None):
    calls.append(1); llm.last_call.update(ok=True, usage={"prompt_tokens": 10, "completion_tokens": 5})
    if llm.call_hook: llm.call_hook(dict(llm.last_call))
    return {"intent": "recommend", "preferences": {}, "mentioned_titles": [], "wants_new_options": False} if "intent" in json.dumps(schema) \
        else {"reply": "تفضل", "show_book_ids": [], "suggestions": []}
llm.complete_json = fake_complete
llm.get_active = lambda: ("openai", "fake")
limits.set_daily_cap(4)
base = limits.usage()["today"]["model_calls"]
limits.set_daily_cap(base + 4)
outs = [c2.post("/api/chat", json={"messages": [{"role": "user", "content": "رواية"}]}).get_json()["understanding"] for _ in range(3)]
u = limits.usage()
ok(outs == ["ai", "ai", "keywords"] and u["capped"] and u["today"]["capped_turns"] >= 1, "budget used up -> keyword fallback, counted as capped")
ok(u["today"]["tokens"] >= 60, "tokens counted from model calls")
print(f"TOTAL PASS={P} FAIL={F}")
