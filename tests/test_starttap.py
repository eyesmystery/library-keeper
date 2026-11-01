"""[STARTTAP] Ibrahim 2026-11-01 «تمام اضف العداد»: count the taps on the three start choices of the visitor page —
«خلّنا نلقى كتابك» (chat), «قرأت كتابًا؟ شاركنا رأيك» (reading), «اقترح كتابًا لرفوفنا» (shelf) — numbers only,
shown in «إحصائيات الزوار» and in the inbox file (section 14)."""
import os, sys, sqlite3, json, re
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, insights, inbox_export, inbox_send, sealed, start_taps, testlog
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

ROOT = os.path.dirname(os.getcwd())
con = sqlite3.connect(models.DB_PATH)
today = insights.local_day()
v = A.app.test_client()
URL = "/api/start-tap"
def rows():
    return {(d, c, l): n for d, c, l, n in con.execute("select day, choice, language, taps from start_taps")}
def total():
    return con.execute("select coalesce(sum(taps),0) from start_taps").fetchone()[0]

# ---- the inbox file before any tap
body, counts = inbox_export.build("open", "test")
ok("## 14. من أين يبدأ الزوار: ضغطات صفحة البداية (0)" in body and "العدّ لم يبدأ بعد" in body and counts["start_taps"] == 0,
   "inbox file before any tap: section 14 with 0 and «العدّ لم يبدأ بعد»")
st0 = login(A.app.test_client()).get("/api/admin/stats?days=30").get_json()["start_taps"]
ok(st0["total"] == 0 and st0["started"] is None and st0["compare"] is None and st0["pct"]["chat"] is None,
   "stats before any tap: zeros, no start day, no comparison")

# ---- the table holds numbers only
cols = [r[1] for r in con.execute("pragma table_info(start_taps)")]
ok(cols == ["day", "choice", "language", "taps"], f"table columns are day, choice, language, taps only ({cols})")
ok(not any(re.search(r"(^|_)(ip|addr|address|session|agent|ua|conversation|conversation_id|visitor|user|text|cookie)(_|$)", c) for c in cols),
   "no identifying column (address, session, browser, conversation, text)")

# ---- route validation
start_taps.reset()
r = v.post(URL, data="not json", content_type="application/json")
ok(r.status_code == 400, "a body that is not JSON → 400")
ok(v.post(URL, json=["chat"]).status_code == 400, "a JSON list → 400")
ok(v.post(URL, json={}).status_code == 400 and v.post(URL, json={"choice": "other"}).status_code == 400
   and v.post(URL, json={"choice": ["chat"]}).status_code == 400 and v.post(URL, json={"choice": "CHAT"}).status_code == 400
   and v.post(URL, json={"choice": 1}).status_code == 400, "missing / unknown / wrong-type choice → 400")
ok(total() == 0, "refused taps store nothing")
ok(v.get(URL).status_code == 405, "GET is not allowed")

# ---- counting
r = v.post(URL, json={"choice": "chat", "lang": "ar"})
ok(r.status_code == 204 and r.data == b"", "a tap on «خلّنا نلقى كتابك» → 204, empty body")
for _ in range(2):
    v.post(URL, json={"choice": "chat", "lang": "ar"})
v.post(URL, json={"choice": "chat", "lang": "en"})
v.post(URL, json={"choice": "reading", "lang": "ar"})
v.post(URL, json={"choice": "reading", "lang": "fr"})          # an unknown language counts as Arabic
v.post(URL, json={"choice": "shelf"})                           # no language → Arabic
# navigator.sendBeacon may send the JSON as text/plain
r = v.post(URL, data=json.dumps({"choice": "shelf", "lang": "en"}), content_type="text/plain;charset=UTF-8")
ok(r.status_code == 204, "a beacon sent as text/plain is accepted")
got = rows()
ok(got == {(today, "chat", "ar"): 3, (today, "chat", "en"): 1, (today, "reading", "ar"): 2,
           (today, "shelf", "ar"): 1, (today, "shelf", "en"): 1}, f"one row per day, choice and language with its count ({got})")

# ---- test mode is not counted
before = total()
testlog.set_enabled(True)
ok(testlog.enabled(), "test mode switched on for the check")
r = v.post(URL, json={"choice": "chat", "lang": "ar"})
ok(r.status_code == 204 and total() == before, "a tap while «وضع الاختبار» is on → 204, not counted")
testlog.set_enabled(False)
v.post(URL, json={"choice": "chat", "lang": "ar"})
ok(total() == before + 1, "test mode off again → counted")

# ---- a browser signed in to the admin page is not counted (staff, manager, developer)
before = total()
for role in ("staff", "manager", "developer"):
    s = login(A.app.test_client(), role)
    r = s.post(URL, json={"choice": "reading", "lang": "ar"})
    ok(r.status_code == 204, f"signed-in {role}: 204")
ok(total() == before, "taps from a browser signed in to the admin page are not counted")
out = login(A.app.test_client(), "staff")
out.post("/api/admin/logout", json={})
out.post(URL, json={"choice": "reading", "lang": "ar"})
ok(total() == before + 1, "after signing out the same browser counts again")

# ---- rate limit per address, kept in memory only
start_taps.reset()
before = total()
codes = [v.post(URL, json={"choice": "chat"}, environ_base={"REMOTE_ADDR": "10.9.9.9"}).status_code for _ in range(35)]
ok(codes[:30] == [204] * 30 and set(codes[30:]) == {429}, f"one address: 30 taps a minute, then 429 ({codes.count(204)} × 204)")
ok(total() == before + 30, "only the allowed taps are counted")
ok(v.post(URL, json={"choice": "chat"}, environ_base={"REMOTE_ADDR": "10.9.9.10"}).status_code == 204, "another address is not affected")
ok(v.post(URL, json={"choice": "nope"}, environ_base={"REMOTE_ADDR": "10.9.9.9"}).status_code == 400, "an unknown choice is still 400 at the limit")
con.commit()
raw = b""
for suffix in ("", "-wal", "-journal"):
    if os.path.exists(models.DB_PATH + suffix):
        with open(models.DB_PATH + suffix, "rb") as f:
            raw += f.read()
ok(b"10.9.9.9" not in raw and "10.9.9.9".encode("utf-16-le") not in raw, "the address is not written anywhere in the database")
start_taps.reset()

# ---- «إحصائيات الزوار»
got = {c: sum(n for (d, cc, l), n in rows().items() if cc == c) for c in start_taps.CHOICES}
tot = sum(got.values())
ok(v.get("/api/admin/stats").status_code == 401 and login(A.app.test_client(), "staff").get("/api/admin/stats").status_code == 403,
   "the stats stay manager-only")
m = login(A.app.test_client())
st = m.get("/api/admin/stats?days=30").get_json()["start_taps"]
ok(st["counts"] == got and st["total"] == tot, f"stats: the three counts of the period ({st['counts']})")
ok(st["pct"] == {c: round(100 * got[c] / tot) for c in start_taps.CHOICES}, f"stats: share % of each ({st['pct']})")
ok(st["started"] == today and st["compare"] is None, "counting started today → no before/after comparison")
ok(m.get("/api/admin/stats?days=7").get_json()["start_taps"]["total"] == tot, "the 7-day period has today's taps too")
# taps before the links were made smaller (2026-10-30 19:00): only when such data exists
for _ in range(3):
    start_taps.record("reading", "ar", day="2026-10-20")
start_taps.record("chat", "ar", day="2026-10-20")
for _ in range(5):
    start_taps.record("chat", "ar", day="2026-10-30")               # the day of the change itself: in neither side
st = m.get("/api/admin/stats?days=0").get_json()["start_taps"]
ok(st["started"] == "2026-10-20" and st["compare"] is not None, "data before 2026-10-30 → a before/after split")
ok(st["compare"]["before"]["counts"] == {"chat": 1, "reading": 3, "shelf": 0} and st["compare"]["before"]["pct"]["reading"] == 75,
   "before = up to 2026-10-29")
ok(st["compare"]["after"]["counts"] == got, "after = from 2026-10-31 (the change day itself left out)")
ok(st["total"] == tot + 9, "the whole period counts every day")
html = open(os.path.join(ROOT, "admin", "index.html"), encoding="utf-8").read()
ok("من أين يبدأ الزوار" in html and "d.start_taps" in html and "العدّ بدأ في" in html and "قبل تصغير الرابطين وبعده" in html,
   "the stats page has the section «من أين يبدأ الزوار» (counts, share, start day, before/after)")
ok("!(d.start_taps && d.start_taps.total)" in html, "the stats page is not shown as empty when there are only taps")

# ---- the inbox file (section 14)
days, started = start_taps.daily(30)
want = sum(d["total"] for d in days)
body, counts = inbox_export.build("open", "test")
ok(f"## 14. من أين يبدأ الزوار: ضغطات صفحة البداية ({want})" in body and counts["start_taps"] == want,
   f"inbox: section 14 with the total of the last 30 days ({want})")
t = {c: got[c] for c in start_taps.CHOICES}
ok(f"| {today} | {t['chat']} | {t['reading']} | {t['shelf']} | {tot} |" in body, "inbox: today's row with the three counts")
ok("| اليوم | خلّنا نلقى كتابك (المحادثة) | قرأت كتابًا؟ شاركنا رأيك | اقترح كتابًا لرفوفنا | المجموع |" in body
   and "العدّ بدأ في 2026-10-20" in body and "آخر 30 يومًا" in body, "inbox: Arabic headings, the start day and the period")
ok(body.index("## 13.") < body.index("## 14."), "section 14 comes after section 13")
body_all, counts_all = inbox_export.build("all", "test")
ok("| 2026-10-20 | 1 | 3 | 0 | 4 |" in body_all and "| 2026-10-30 | 5 | 0 | 0 | 5 |" in body_all and counts_all["start_taps"] == tot + 9
   and "المدة: كل السجل" in body_all, "inbox «all»: every day since counting started")
ok(not re.search(r"10\.9\.9\.(9|10)", body_all), "inbox: no address")
# the sealed file the library sends, opened the way scripts/inbox_pull.py opens it
SK = sealed.new_private(); PK = sealed.public_key(SK)
inbox_send.public_key = lambda: PK
name, blob, cnt = inbox_send.build_file("open", "manual", "test")
doc = json.loads(sealed.open_sealed(SK, blob))
ok("## 14. من أين يبدأ الزوار" in doc["markdown"] and f"| {today} |" in doc["markdown"] and doc["counts"]["start_taps"] == want,
   "the sealed inbox file carries section 14 and its count")
pull = open(os.path.join(ROOT, "scripts", "inbox_pull.py"), encoding="utf-8").read()
ok('f.write(doc.get("markdown") or "")' in pull, "inbox_pull.py writes the file's Markdown as it is (section 14 included)")

# ---- the visitor page sends the taps
js = open(os.path.join(ROOT, "frontend", "app.js"), encoding="utf-8").read()
ok("navigator.sendBeacon(`${API}/start-tap`" in js and "keepalive: true" in js and ".catch(() => {})" in js,
   "app.js: sendBeacon with a fetch keepalive fallback, errors silent")
ok(re.search(r"\$\('startBtn'\)\.addEventListener\('click', \(\) => \{.*else \{ newChat\(\); startTap\('chat'\); \} \}\);", js) is not None,
   "«خلّنا نلقى كتابك» counts as chat after its own action, only for a fresh start (not going back to an open chat)")
ok("$('readingBtn').addEventListener('click', () => { openReading(); startTap('reading'); });" in js, "«قرأت كتابًا؟» counts as reading")
ok("openShelf(''); startTap('shelf'); });" in js, "«اقترح كتابًا لرفوفنا» counts as shelf")
fn = js[js.index("function startTap"):js.index("counting never gets in the visitor's way")]
ok("JSON.stringify({ choice, lang: currentLanguage })" in fn and "conversation" not in fn and "messages" not in fn,
   "only the choice and the language are sent")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
