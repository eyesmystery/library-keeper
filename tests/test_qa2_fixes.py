"""Fixes from the second chat test round (QA2, 2026-10-09; Ibrahim «موافق» / «صلحها»)."""
import json, os, sqlite3, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm, chat, honors, author_flow, authors, app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
all_books = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
seen = {}
def fake_respond(conv, facts_json, guidance, active, *a):
    seen["facts"] = json.loads(facts_json); seen["guidance"] = guidance
    return {"reply": "x", "show_book_ids": [f["id"] for f in seen["facts"]][:4], "suggestions": []}
chat.respond = fake_respond
def interp(prefs=None, intent="recommend", titles=(), author=None, refs=()):
    chat.interpret = lambda conv, active, *a: {"intent": intent, "prefs": llm.clean_prefs(prefs or {}), "mentioned_titles": list(titles),
                                              "wants_new_options": False, "references": list(refs),
                                              "author_request": author or {}}
def say(*msgs):
    conv = []
    for i, m in enumerate(msgs):
        conv.append({"role": "user" if i % 2 == 0 else "assistant", "content": m})
    return A.run_chat(conv, active=("fake", "m"))

# ---- 1. the interpreter's «adult» never crashes the chat
interp({"audience": "adult", "genres": ["رواية"]})
try:
    out = say("ابي هدية لزوجتي بمناسبة ذكرى زواجنا")
    ok(isinstance(out.get("context"), list) and out.get("audience") is None, "«adult» from the interpreter: no crash, no child badge")
except Exception as e:
    ok(False, f"«adult» crashed: {e!r}")
ok(A.chat_context({"genres": set()}, {"audience": "adult"}, ["x"]) == [], "chat_context ignores an unknown reader")
def boom(*a, **k):
    raise KeyError("adult")
_real = A.run_chat
calls = []
def flaky(messages, active=None, **k):
    calls.append(active)
    if active is not False:
        raise KeyError("adult")
    return _real(messages, active=False, **k)
A.run_chat = flaky
r = A.app.test_client().post("/api/chat", json={"messages": [{"role": "user", "content": "هدية لأبوي"}]})
ok(r.status_code == 200 and r.get_json().get("reply"), "/api/chat answers (keywords) instead of a server error")
A.run_chat = lambda *a, **k: boom()
r = A.app.test_client().post("/api/chat", json={"messages": [{"role": "user", "content": "هدية لأبوي"}]})
ok(r.status_code == 200 and "خلل بسيط" in r.get_json().get("reply", ""), "and a polite line if that fails too")
A.run_chat = _real

# ---- 2. the child stays when the interpreter reads «قصص» as «رواية»
ok(A.chat_audience(["أنا معلمة صف ثالث ابتدائي وأبغى قصص أقرأها للطالبات", "أبغى قصص تربوية عن الصدق والأمانة"],
                   {"audience": "children", "genres": ["رواية"]}, False) == "children", "«قصص تربوية» after a primary-school class keeps the child")
ok(A.chat_audience(["ولدي عمره ٦ ويحب السيارات", "شي ثاني"], {"genres": ["رواية"]}, False) == "children", "«شي ثاني» keeps the child")
ok(A.chat_audience(["انا عمري ١٥ وابي رواية حب"], {"audience": "young_adult", "genres": ["رواية"]}, False) == "young_adult",
   "«عمري ١٥» is a teenager")
ok(A.chat_audience(["لبنتي عمرها ٦", "طيب وكتب فلسفة لي أنا"], {"genres": ["فلسفة"]}, False) is None, "another kind of book for himself leaves the child")
ok(A.detect_audience(A.normalize("عمري 30")) is None and A.adult_said("عمري ٣٠"), "«عمري ٣٠» is an adult")

# ---- 3. two authors in one message
by = lambda name: [b for b in all_books.values() if b.get("author") == name and A.is_available(b)]
two = [n for n in ("غازي القصيبي", "نجيب محفوظ") if by(n)]
if two:
    req = {"mode": "works", "name_as_written": two[0] + " أو مؤلف لا وجود له أبدًا", "full_name": "", "aliases": []}
    reply, names, only = author_flow.resolve(req, [{"role": "user", "content": "ابي شي ل" + req["name_as_written"]}], all_books.values())
    ok(reply is None and only and two[0] in names and len(req.get("people") or {}) == 2, "«فلان أو فلان»: looked up one by one")
if len(two) == 2:
    req = {"mode": "works", "name_as_written": two[0] + " و" + two[1], "full_name": "", "aliases": []}
    reply, names, only = author_flow.resolve(req, [{"role": "user", "content": req["name_as_written"] + "؟"}], all_books.values())
    ok(reply is None and set(two) <= set(names), "«فلان وفلان» joined by «و»: both authors")
    interp({"authors": [req["name_as_written"]]}, author=dict(req, people=None, mode="works"))
    say(req["name_as_written"] + "؟ مو عندكم؟")
    got = {f["author"] for f in seen["facts"]}
    ok(set(two) <= got, "facts carry books of both authors")
    ok("أكثر من مؤلف" in seen["guidance"] and "No available book was found under" not in seen["guidance"], "guidance names both, none «missing»")

# ---- 4. «لدان براون» / a misspelling is not «missing» when his books are in facts
murakami = [b for b in all_books.values() if "موراكامي" in (b.get("author") or "") and A.is_available(b)]
if murakami:
    name = murakami[0]["author"]
    interp({"authors": ["ل" + name]}, author={"mode": "works", "name_as_written": name, "full_name": name, "aliases": []})
    say("عندكم ل" + name + "؟")
    ok("No available book was found under" not in seen["guidance"], "«ل» + author: not reported missing")
ok(author_flow._named_before("هاروكي موراكامي", [{"role": "user", "content": "ابي كتب هاروكي موركامي"}], authors),
   "the visitor's own earlier spelling counts as the full name")

# ---- 5. prizes before a year, count of the whole list
ok(A.years_said("وش فاز قبل ٢٠١٥؟") == (None, 2014) and A.years_said("بعد 2020") == (2021, None)
   and A.years_said("since 2020") == (2020, None) and A.years_said("بين ٢٠١٠ و٢٠١٥") == (2010, 2015)
   and A.years_said("عندكم رواية ١٩٨٤؟") == (None, None), "years said: before / after / since / between; a title is not a year")
ipaf = [r["book_id"] for r in con.execute("select ba.book_id from book_awards ba join books b using(book_id) where ba.award_key='ipaf' and ba.result='winner' and b.quantity>0 and b.active=1")]
if ipaf:
    interp({"awards": ["ipaf"], "award_year": 2015})
    out = say("وش فاز بالبوكر العربية قبل ٢٠١٥؟")
    years = [y for f in seen["facts"] for a in f.get("awards", []) for y in [int(t) for t in a.split() if t.isdigit() and len(t) == 4]]
    ok(seen["facts"] and all(y <= 2014 for y in years), "«قبل ٢٠١٥»: only prizes up to 2014")
    ok(any(c["key"] == "before" for c in out["context"]), "badge «حتى ٢٠١٤»")
    ok("المتوفر عندنا الآن:" in seen["guidance"], "the whole count is given to the reply")
nobel = con.execute("select count(*) from author_awards where award_key='nobel'").fetchone()[0]
if nobel:
    interp({"awards": ["nobel"]})
    say("مين من الفايزين بنوبل عندكم كتبهم؟")
    auths = [f["author"] for f in seen["facts"][:4]]
    ok(len(set(auths)) == len(auths), "«مين عندكم»: one book per winner first")

# ---- 10. a prize stored under «other» by its name
other = con.execute("select award_name from author_awards where award_key='other' and award_name like 'جائزة %' limit 1").fetchone()
if other:
    phrase = other[0].replace("جائزة ", "", 1)
    ok(A.prize_named("عندكم كتاب فاز بجائزة " + phrase + "؟") is not None, f"«جائزة {phrase}» is found by name")
    ok(honors.matches_award(0, ["name:" + phrase]) == [], "name key on a book without it: nothing")
ok(A.prize_named("عندكم كتاب فاز بجائزة عالمية؟") is None, "a generic «جائزة عالمية» is not a name")

# ---- 6. «ثلاثية نجيب محفوظ»
tri = [r["book_id"] for r in con.execute("select book_id from books where series_name='ثلاثية القاهرة'")]
if tri:
    got = [b["book_id"] for b in A.match_titles(["ثلاثية نجيب محفوظ"], all_books)]
    ok(set(got) == set(tri), "«ثلاثية نجيب محفوظ» -> the three volumes, not another «ثلاثية»")
fosse = [b for b in all_books.values() if A.normalize(b["title"]).strip() == "ثلاثيه"]
if fosse:
    ok(fosse[0]["book_id"] in [b["book_id"] for b in A.match_titles(["ثلاثية"], all_books)], "«ثلاثية» alone still finds the book of that title")

# ---- 7. comics for a 13-year-old: only those with a fitting researched age
comic = con.execute("select book_id from books where genre='قصص مصورة' and age_min is not null and age_min<=13 and (age_max is null or age_max>=12) and quantity>0 and active=1 limit 1").fetchone()
b = dict(con.execute("select * from books limit 1").fetchone(), genre="قصص مصورة")
ok(not A.age_allowed(None, "young_adult", (), dict(b, age_min=None, age_max=None), 13), "a comic with no known age is not offered to a teen")
ok(A.age_allowed(None, "young_adult", (), dict(b, age_min=12, age_max=None), 13)
   and not A.age_allowed(None, "young_adult", (), dict(b, age_min=16, age_max=None), 13), "a comic is offered when its age range fits")
interp({"audience": "young_adult", "genres": ["قصص مصورة"]})
out = say("بنتي ١٣ سنة وتحب القصص المصورة والمانجا", "تمام", "عندكم قصص مصورة؟")
ok("للطفل ولا للكبار" not in out["reply"], "no «للطفل ولا للكبار» when the kind was asked for the child")
if comic:
    ok(any(f["genre"] == "قصص مصورة" for f in seen["facts"]), "comics shelf reached for the teen")
interp({"genres": ["تاريخ"], "audience": "children"})
out1 = say("بنتي عمرها ٨", "تمام", "عندكم كتب تاريخ؟")
out2 = say("بنتي عمرها ٨", "تبي كتب تاريخ للطفل ولا للكبار؟", "عندكم كتب تاريخ؟")
ok("للطفل ولا للكبار" in out1["reply"] and "للطفل ولا للكبار" not in out2["reply"], "the question is asked once per conversation")

# ---- 8. «الدولة السعودية» is a subject
ok(A.wished_country("طيب عن تاريخ الدولة السعودية الأولى") is None and A.wished_country("ابي رواية لكاتب سعودي") == ("سعودي", ["السعودية"])
   and A.wished_country("روايات سعودية") is not None, "«الدولة السعودية» is not the author's country")

# ---- 9. the words of a missing title never pick books by title
MISSING = "الأب الغني والأب الفقير"
if any(A.is_available(b) for b in A.match_titles([MISSING], all_books)):
    MISSING = "حكاية الأب الغني والعم الطيب"   # the prototype catalog has the real one
interp_refs = chat.clean_references([{"title": MISSING, "author": "", "format": "nonfiction", "moods": [], "themes": [], "known": True}])
chat.interpret = lambda conv, active, *a: {"intent": "recommend", "prefs": llm.clean_prefs({"title_keywords": [MISSING]}),
                                          "mentioned_titles": [], "wants_new_options": False, "references": interp_refs, "author_request": {}}
say("عندكم كتاب " + MISSING + "؟", "غير متوفر", "الإدارة المالية")
bad = [(f["title"], f["why_it_matches"]) for f in seen["facts"] if "عنوانه يطابق كلمات" in f["why_it_matches"]]
ok(not bad, f"no «title matches your words» from the missing book's title {bad}")

# ---- 11. publication year
y = con.execute("select max(cast(publication_year as int)) from books where quantity>0 and active=1").fetchone()[0]
if y and y > 2020:
    interp({})
    out = say("Anything published after 2020?")
    ok(seen["facts"] and all(int(str(f.get("first_published") or f.get("edition_year"))[:4]) > 2020 for f in seen["facts"] if f["available"]), "«after 2020»: books published 2021 or later")
    ok(any(c["key"] == "pubyear" for c in out["context"]), "badge for the publication year")

# ---- «فاجئيني بكتب جديدة» then «رواية»: still new books (Ibrahim's staff chat 2026-10-11 21:14)
W = A.new_release_wished
ok(W(["فاجئيني بكتب جديدة", "رواية"]) == "carried", "start button «كتب جديدة» carried to the next answer")
ok(W(["فاجئيني بكتب جديدة", "رواية", "هذي مو جديدة. المفروض تعتمد جديدة في السياق بعد"]) == "said", "«هذي مو جديدة» insists on it")
ok(W(["ابغى روايات جديدة"]) == "said", "«روايات جديدة» is a new-release wish")
ok(W(["فاجئيني بكتب جديدة", "رواية", "مو لازم جديدة، أي شي غامض"]) is None, "«مو لازم جديدة» takes it back")
ok(W(["فاجئيني بكتب جديدة", "رواية", "أحب الروايات الغامضة"]) == "carried", "a later wish keeps «new»")
ok(W(["ابغى رواية غامضة"]) is None, "no «new» said: no year limit")
if y and y > 2022:
    interp({"genres": ["رواية"]})
    out = say("فاجئيني بكتب جديدة", "يسعدني! تبغى المفاجأة رواية، ولا كتاب غير روائي؟", "رواية")
    lo = A.time.localtime().tm_year - A.NEW_RELEASE_YEARS
    yrs = [int(str(f.get("first_published") or f.get("edition_year"))[:4]) for f in seen["facts"] if f["available"] and (f.get("first_published") or f.get("edition_year"))]
    ok(yrs and all(v >= lo for v in yrs), f"«جديدة» then «رواية»: books published {lo} or later {yrs[:5]}")

# ---- 12. reply rules
ok("انسب كل كتاب لمؤلفه" in chat.RESPOND_SYSTEM_AR and "Attribute each book to its own author" in chat.RESPOND_SYSTEM_EN, "reply rules: each book's own author")
ok("id رقم داخلي" in chat.RESPOND_SYSTEM_AR, "reply rules: the id is never a year")

# ---- a misspelt author name is asked about («أسامة الشازلي» -> «أسامة عبد الرءوف الشاذلي»?) (Ibrahim 2026-10-09)
cand = None
for b in all_books.values():
    w = author_flow.display(b.get("author")).split()
    if len(w) >= 2 and A.is_available(b) and any(ch in w[-1] for ch in "ذزظ") and len(w[-1]) >= 4:
        cand = w; break
if cand:
    fam = cand[-1].translate(str.maketrans("ذزظ", "زذز"))
    typo = cand[0] + " " + fam
    q, names, _ = author_flow.resolve({"mode": "works", "name_as_written": typo, "full_name": typo, "aliases": []},
                                      [{"role": "user", "content": "عندكم كتب " + typo + "؟"}], all_books.values())
    ok(bool(q) and "تقصد" in q and " ".join(cand) in q, f"misspelt «{typo}»: «تقصد {' '.join(cand)}؟»")
else:
    ok(True, "no author with ذ/ز/ظ in the fixture: skipped")

# ---- the book page shows the publication year; the reply rule answers «متى صدر؟» from year (Ibrahim 2026-10-09)
page = A.app.test_client().get("/").get_data(as_text=True)
ok('id="dYearBox"' in page and 'id="yearLabel"' in page, "book page has «سنة النشر»")
ok("متى صدر" in chat.RESPOND_SYSTEM_AR and "when the book came out" in chat.RESPOND_SYSTEM_EN, "reply rules: answer the publication year from year")

# ---- migration tolerates a column another process added
c2 = sqlite3.connect(models.DB_PATH)
models._add_column(c2, "books", "age_min", "INTEGER")
ok(True, "adding an existing column is tolerated")
print(f"TOTAL PASS={P} FAIL={F}")
sys.exit(1 if F else 0)
