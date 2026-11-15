"""ADMINWISH (Ibrahim 2026-11-13 «نبغى نخلي اقتراحات الزوار اوضح في الادارة. سواءا اقتراحات مباشرة او اسئلة عن كتب غير
متوفرة»): «التنبيهات ← طلبات واقتراحات الزوار» — candidate books, book suggestions typed as a note, the chat's missing
books / authors and the outside lookups merged by title; «متوفر الآن» checked live (a title that starts with the asked
one); one status (جديد / قيد المتابعة / تم توفيره / مرفوض) written to every merged row and to the audit log; the badge
counts distinct open titles; staff and managers only; the inbox file keeps working. Test database only, no model."""
import os, sqlite3, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, open_questions as OQ, shelf_suggestions as SS, visitor_notes as VN, outside_books as OB
import visitor_wishes as W, inbox_export, chat_rules
from auth_helper import login

P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

ROOT = os.path.dirname(os.getcwd())
visitor = A.app.test_client()
con = models.get_connection()

# ---- one book asked for four ways
T = "حديقة الاختبار البعيدة"
FULL = T + ": رواية عن الصبر"
rec = {"key": OB.key_of(T), "title": FULL, "author": "كاتب تجريبي", "year": 2019, "publisher": "دار تجريب",
       "sources": ["https://www.goodreads.com/book/show/1"]}
SS.add_outside(rec, asked_as=T, note="أبيه لبنتي")
SS.add_outside(rec, asked_as=T)
for _ in range(3):
    OQ.record("missing_book", T)
ok(visitor.post("/api/suggestion", json={"note": f"كتاب مقترح للرفوف: {T} — كاتب تجريبي"}).status_code == 201,
   "a book suggested as typed is saved")
OB._save(OB.key_of(T), T, {"title": FULL, "author": "كاتب تجريبي", "year": 2019, "publisher": "دار تجريب"}, "found")
for _ in range(4):
    OB._count(OB.key_of(T))
# two books with one title by two authors stay apart
SS.add_outside({"key": "مدينة الضباب الثالثه 1", "title": "مدينة الضباب الثالثة", "author": "سالم الحربي"})
SS.add_outside({"key": "مدينة الضباب الثالثه 2", "title": "مدينة الضباب الثالثة", "author": "نورة العتيبي"})
# a general suggestion, a subject (developer only) and a report (not a wish)
visitor.post("/api/suggestion", json={"note": "أتمنى ركن هادئ للقراءة"})
OQ.record("topic", "موضوع للفحص فقط")
ok(W.parse_suggestion("Book suggested for the shelves: Quiet Garden — A. Writer") == ("Quiet Garden", "A. Writer")
   and W.parse_suggestion("أتمنى ركن هادئ للقراءة") is None, "the visitor page's «كتاب مقترح للرفوف:» note is read as a title + author")

# ---- a book in stock whose title starts with the asked one, and one that only starts like it
avail = [b for b in (dict(r) for r in A.list_books(only_active=True)) if A.is_available(b)]
def _prefix(b):
    words = (b["title"] or "").split()
    for k in range(1, len(words)):
        p = " ".join(words[:k])
        if len(OB.norm(p)) >= 6 and OB.norm(b["title"]).startswith(OB.norm(p) + " "):
            return p
    return None
pb = next(b for b in avail if _prefix(b) and not any(chat_rules.title_key(o["title"]) == chat_rules.title_key(_prefix(b)) for o in avail))
PREF = _prefix(pb)
OQ.record("missing_book", PREF)
# (a longer title of ANOTHER book: one that starts with PREF would be merged with it, as «قوة الآن» with «قوة الآن: الدليل…»)
pb2 = next(b for b in avail if len(OB.norm(b["title"])) >= 6 and OB.norm(b["title"]).split()[0] != OB.norm(PREF).split()[0])
LONGER = pb2["title"] + " الجزء المفقود للفحص"
OQ.record("missing_book", LONGER)
ab = next(b for b in avail if len((b.get("author") or "").split()) >= 2 and not OB._LATIN.search(b["author"]) and "،" not in b["author"])
OQ.record("missing_author", ab["author"])

# ---- access
ok(visitor.get("/api/admin/inbox/wishes").status_code == 401 and visitor.post("/api/admin/inbox/wishes", json={"refs": ["q:1"], "status": "following"}).status_code == 401,
   "an anonymous visitor gets 401 (list and change)")
st = login(A.app.test_client(), "staff")
mg = login(A.app.test_client())
r = st.get("/api/admin/inbox/wishes")
ok(r.status_code == 200, "a staff account sees the list")
d = r.get_json()
rows = {w["title"]: w for w in d["items"]}

# ---- merging
g = rows.get(T)
ok(g is not None and sorted(x[0] for x in g["refs"]) == ["c", "n", "q"] and len(g["refs"]) == 3,
   "candidate + typed suggestion + chat question of one book are ONE row")
src = {s["key"]: s["times"] for s in (g or {}).get("sources", [])}
ok(g and g["total"] == 6 and src == {"direct": 3, "chat": 3, "outside": 4},
   "sources as tags: اقتراح مباشر ×3, سؤال في المحادثة ×3, غير متوفر عندنا (بحثت عنه منار ×4); total 6 requests")
ok(g and g["found_title"] == FULL and g["year"] == 2019 and g["publisher"] == "دار تجريب" and g["author"] == "كاتب تجريبي"
   and g["visitor_notes"] == ["أبيه لبنتي"], "the outside card's title, year, publisher and the visitor's note are on the row")
ok(g and g["status"] == "new" and g["kind"] == "book" and g["first_at"] <= g["last_at"], "a new row: «جديد», kind book, first and last date")
fog = [w for w in d["items"] if w["title"] == "مدينة الضباب الثالثة"]
ok(len(fog) == 2, "one title by two different authors stays two rows")
ok("موضوع للفحص فقط" not in rows, "subjects / wishes / questions stay in «أسئلة بلا جواب» (developer)")
ok([x["note"] for x in d["general"]] == ["أتمنى ركن هادئ للقراءة"] and d["general"][0]["status"] == "new",
   "a suggestion that is not about a book is listed apart («اقتراحات عامة»)")

# ---- «متوفر الآن», checked live and flexibly
p = rows.get(PREF)
ok(p and any(b["book_id"] == pb["book_id"] for b in p["in_stock_now"]) and p["suggest_provided"],
   f"«{PREF}» asked → «{pb['title']}» in stock now (title starts with it), and «تم توفيره» is proposed")
lg = rows.get(LONGER)
ok(lg is not None and not any(b["book_id"] == pb2["book_id"] for b in lg["in_stock_now"]),
   "a longer asked title is not matched by a shorter book title (no «اللؤلؤة» for «اللؤلؤة التي كسرت محارتها»)")
au = rows.get(ab["author"])
ok(au and au["kind"] == "author" and any(b["book_id"] == ab["book_id"] for b in au["in_stock_now"]),
   "an author asked for: their books in stock now are shown")
ok(g and not g["in_stock_now"] and not g["suggest_provided"], "a book we still do not have: nothing in stock, no proposal")

# ---- the badge
c0 = st.get("/api/admin/inbox/count").get_json()
al = {i["key"]: i for i in st.get("/api/admin/inbox/alerts").get_json()["items"]}
ok(c0["wishes_open"] == d["counts"]["open"] and c0["wishes_general_open"] == 1 and al["wishes"]["open"] == c0["wishes_open"] + 1
   and al["wishes"]["go"] == "inbox:wishes", "«التنبيهات» and /count: distinct open titles (+ open general suggestions)")
ok("suggestion" not in al and "candidates" not in al, "the old «اقتراحات الزوار» / «كتب رشّحها الزوار» rows are one row now")
att0 = st.get("/api/admin/inbox/alerts").get_json()["attention"]

# ---- one status for every merged row
title = T
r = st.post("/api/admin/inbox/wishes", json={"refs": g["refs"], "status": "following", "title": title})
ok(r.status_code == 200 and sorted(r.get_json()["updated"]) == sorted(g["refs"]), "staff set «قيد المتابعة»")
def stored():
    out = {}
    for ref in g["refs"]:
        t, i = ref.split(":")
        table, col, _ = W.TABLES[t]
        out[t] = tuple(con.execute(f"SELECT status, handled_by, staff_note FROM {table} WHERE {col}=?", (int(i),)).fetchone())
    return out
s1 = stored()
ok(s1["c"][0] == "following" and s1["q"][0] == "following" and s1["n"][0] == "following" and s1["c"][1] == "اختبار staff",
   "written to the candidate, the chat question and the typed suggestion, with who did it")
au_row = con.execute("SELECT action, target, details FROM audit_log WHERE action LIKE 'wish.%' ORDER BY log_id DESC").fetchone()
ok(au_row and au_row[0] == "wish.following" and au_row[1] == title and "c:" in au_row[2], "the change is in the audit log")
OQ.record("missing_book", T)
ok(stored()["q"][0] == "following", "asked again in the chat while being followed: it stays «قيد المتابعة»")
ok(st.get("/api/admin/inbox/alerts").get_json()["attention"] == att0, "«قيد المتابعة» is still open (still counted)")
r = st.post("/api/admin/inbox/wishes", json={"refs": g["refs"], "note": "طلبناه من الموزّع", "title": title})
ok(r.status_code == 200 and all(v[2] == "طلبناه من الموزّع" for v in stored().values()), "the staff note is written to every row")
w2 = {w["title"]: w for w in st.get("/api/admin/inbox/wishes?status=following").get_json()["items"]}.get(T)
ok(w2 and w2["staff_note"] == "طلبناه من الموزّع" and w2["status"] == "following", "the list shows the note under «قيد المتابعة»")
r = mg.post("/api/admin/inbox/wishes", json={"refs": g["refs"], "status": "provided", "title": title})
s2 = stored()
ok(r.status_code == 200 and s2["c"][0] == "added" and s2["q"][0] == "done" and s2["n"][0] == "done",
   "«تم توفيره» (manager): added for the candidate, done for the question and the suggestion")
ok(st.get("/api/admin/inbox/alerts").get_json()["attention"] == att0 - 1, "a provided title leaves the badge")
ok(T not in {w["title"] for w in st.get("/api/admin/inbox/wishes").get_json()["items"]}
   and T in {w["title"] for w in st.get("/api/admin/inbox/wishes?status=provided").get_json()["items"]}
   and T in {w["title"] for w in st.get("/api/admin/inbox/wishes?status=done").get_json()["items"]}, "it moves from «المفتوحة» to «تم توفيره»")
OQ.record("missing_book", T)
ok({w["title"]: w for w in st.get("/api/admin/inbox/wishes").get_json()["items"]}.get(T, {}).get("status") == "new",
   "asked again after «تم توفيره»: the row opens again as «جديد»")
st.post("/api/admin/inbox/wishes", json={"refs": g["refs"], "status": "declined", "title": title})
s3 = stored()
ok(s3["c"][0] == "declined" and s3["q"][0] == "declined", "«مرفوض» on every row")
SS.add_outside(rec, asked_as=T)
OQ.record("missing_book", T)
ok(stored()["c"][0] == "declined" and stored()["q"][0] == "declined", "asked again after «مرفوض»: it stays «مرفوض»")
gen = st.get("/api/admin/inbox/wishes").get_json()["general"][0]
ok(st.post("/api/admin/inbox/wishes", json={"refs": [gen["ref"]], "status": "provided"}).status_code == 200
   and con.execute("SELECT status FROM visitor_notes WHERE note_id=?", (int(gen["ref"][2:]),)).fetchone()[0] == "done",
   "a general suggestion «تمت المعالجة»")

# ---- refused
rep = VN.add_report({"book_id": pb["book_id"], "title": pb["title"]}, "cover")
ok(st.post("/api/admin/inbox/wishes", json={"refs": g["refs"], "status": "bogus"}).status_code == 400
   and st.post("/api/admin/inbox/wishes", json={"refs": ["x:1"], "status": "new"}).status_code == 400
   and st.post("/api/admin/inbox/wishes", json={"refs": g["refs"]}).status_code == 400
   and st.post("/api/admin/inbox/wishes", json={"refs": ["q:987654321"], "status": "new"}).status_code == 404
   and st.post("/api/admin/inbox/wishes", json={"refs": [f"n:{rep}"], "status": "declined"}).status_code == 404,
   "bad status, bad refs, nothing to change, unknown row, a book report (not a wish) refused")
ok(st.post("/api/admin/inbox/wishes", json={"refs": g["refs"], "status": "new"}, headers={"X-CSRF-Token": "x"}).status_code == 403,
   "a change without the page's CSRF token is refused")

# ---- the old lists and the inbox file keep working with the new states
st.post("/api/admin/inbox/wishes", json={"refs": [x for x in g["refs"]], "status": "following"})
c = st.get("/api/admin/inbox/candidates?status=open").get_json()
ok(any(x["status"] == "following" for x in c["items"]) and c["counts"]["following"] >= 1, "«كتب مرشّحة» API: «following» is listed with the open ones")
ok(any(x["phrase"] == T for x in st.get("/api/admin/inbox/requested?status=open").get_json()["items"]), "«طلبات القرّاء» API still answers")
ok(any(x["status"] == "following" for x in st.get("/api/admin/inbox?kind=suggestion&status=open").get_json()["items"]), "«الاقتراحات» API too")
def section(body, n):
    return body.split(f"\n## {n}. ")[1].split("\n## ")[0]
body, counts = inbox_export.build("open", "test")
ok("قيد المتابعة" in section(body, 2) and "قيد المتابعة" in section(body, 3) and "قيد المتابعة" in section(body, 12)
   and "ملاحظة الموظف: طلبناه من الموزّع" in section(body, 12) and "ملاحظة الموظف: طلبناه من الموزّع" in section(body, 3),
   "the inbox file (open): §2 / §3 / §12 show «قيد المتابعة» and the staff note")
st.post("/api/admin/inbox/wishes", json={"refs": g["refs"], "status": "declined"})
body, counts = inbox_export.build("done", "test")
ok("مرفوض" in section(body, 2) and "مرفوض" in section(body, 3) and "مرفوض (لن نوفره)" in section(body, 12)
   and counts["candidates"] >= 1 and T in section(body, 12), "the inbox file (handled): «مرفوض» in §2, §3 and §12")
ok(st.get("/api/admin/inbox/export?status=all").status_code == 200, "the export route answers")

# ---- schema: a library database from before gets the new column at start (models.init_db -> ensure_table)
for t in ("open_questions", "visitor_notes", "candidate_books"):
    ok("staff_note" in {r[1] for r in con.execute(f"PRAGMA table_info({t})")}, f"{t}.staff_note exists")
old = sqlite3.connect(":memory:"); old.row_factory = sqlite3.Row
old.execute("CREATE TABLE open_questions (q_id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, phrase TEXT NOT NULL, phrase_key TEXT NOT NULL, book_title TEXT, times INTEGER NOT NULL DEFAULT 1, first_at TEXT NOT NULL, last_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', handled_by TEXT, handled_at TEXT, UNIQUE (kind, phrase_key))")
OQ.ensure_table(old)
ok("staff_note" in {r[1] for r in old.execute("PRAGMA table_info(open_questions)")}, "an old open_questions table gets staff_note")
mk = open(os.path.join(ROOT, "scripts", "make_release.py"), encoding="utf-8").read()
ok(all(f'"{t}"' in mk.split("EMPTY_TABLES")[1][:1600] for t in ("visitor_notes", "open_questions", "candidate_books", "outside_books")),
   "the tables stay library data (never in a release package)")
au_src = open(os.path.join(ROOT, "scripts", "apply_update.py"), encoding="utf-8").read()
ok("status IN ('open', 'following')" in au_src, "an update's solved items close «قيد المتابعة» ones too")

# ---- the page
html = open(os.path.join(ROOT, "admin", "index.html"), encoding="utf-8").read()
ok('id="subWishes"' in html and 'id="wishPane"' in html and "async function loadWishes" in html and "/api/admin/inbox/wishes" in html,
   "«التنبيهات ← طلبات واقتراحات الزوار» is on the page")
ok('id="inboxTabSuggestion"' not in html and 'id="inboxTabCandidates"' not in html and "loadCandidates" not in html,
   "«الاقتراحات» and «كتب مرشّحة» left «من الزوار» (no double list)")
ok('id="rqGo"' in html and "goAlert('inbox:wishes')" in html and "['wishes', 'suggestion', 'candidates', 'requested'].includes(sub)" in html,
   "«القرّاء ← طلبات القرّاء» and the old alert links lead to the new screen")
ok(all(x in html for x in ("جديد", "قيد المتابعة", "تم توفيره", "مرفوض", "متوفر الآن: علّمه «تم توفيره»", "ملاحظة الموظف")),
   "the four states, the «تم توفيره» proposal and the staff note are on the page")
con.close()
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
