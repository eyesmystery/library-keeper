"""Chat fixes of 2026-10-06 (20 reader tests): subject search, «مو رعب أطفال», an open request,
a title we do not have, and one missing author among several."""
import os, sys, json, sqlite3
for k in ["ANTHROPIC_API_KEY","OPENAI_API_KEY","GEMINI_API_KEY","LIBRARY_KEEPER_PROVIDER","LIBRARY_KEEPER_MODEL","LIBRARY_KEEPER_LLM"]:
    os.environ.pop(k, None)
sys.path.insert(0, os.getcwd())
import models, llm, chat, topics, app as A
from auth_helper import login
c = login(A.app.test_client())
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# ---------- the audience word after a negation is not the reader
n = A.normalize
ok(A.detect_audience(n("أبي رواية رعب تخوف صدق، مو رعب أطفال")) is None, "«مو رعب أطفال»: not a child reader")
ok(A.detect_audience(n("كتاب ليس للأطفال")) is None, "«ليس للأطفال»: not a child reader")
ok(A.detect_audience(n("something scary, not for kids")) is None, "'not for kids': not a child reader")
ok(A.detect_audience(n("أبغى قصة لولدي عمره ٨ سنوات")) == "children", "a stated age still means a child")
ok(A.detect_audience(n("ما أبغى رواية، أبغى قصص أطفال")) == "children", "a negation far before the word does not cancel it")
ok(A.detect_audience(n("قصة لطفل بدون عنف")) == "children", "a negation after the word does not cancel it")
ok(A.detect_audience(n("مو للمراهقين")) is None, "«مو للمراهقين»: not a teenage reader")

# ---------- subject search on a small made-up shelf
shelf = {i: b for i, b in enumerate([
    {"title": "مختصر تاريخ الأندلس", "summary": "عرض موجز", "genre": "تاريخ"},
    {"title": "المسيح الأندلسي", "summary": "رواية تاريخية", "genre": "رواية"},
    {"title": "وداعا للتسويف", "summary": "خطوات عملية", "genre": "تطوير ذاتي"},
    {"title": "كيف تتغير", "summary": "يشرح لماذا نقع في التسويف وكيف نتركه", "genre": "تطوير ذاتي"},
    {"title": "الغرب كما رآه العرب", "summary": "", "genre": "تاريخ"},
    {"title": "غربة الياسمين", "summary": "", "genre": "رواية"},
    {"title": "رحلة", "subjects": json.dumps(["الفضاء", "المستقبل"], ensure_ascii=False), "genre": "رواية"},
] + [{"title": f"تاريخ عام {i}", "summary": "كتاب في التاريخ", "genre": "تاريخ"} for i in range(40)], 1)}
topics._cache.update(index=None)
idx = topics.index(shelf)
score = lambda phrase, i: topics.match([phrase], i, idx)[0]
about = lambda phrase, i: topics.match([phrase], i, idx)[1] == [phrase]
ok(score("الأندلس", 1) == topics.TITLE and score("الأندلس", 2) == topics.TITLE, "a subject in the title, also as an adjective (الأندلسي)")
ok(score("تاريخ الأندلس", 1) == topics.TITLE and score("تاريخ الأندلس", 10) == 0 and topics.searchable(["تاريخ"], idx) == [], "a common word (تاريخ) is not a subject by itself")
ok(score("تاريخ الأندلس", 2) > 0 and score("معنى الأندلس", 1) == 0, "every word of a subject must be in the book, common ones included")
ok(about("التسويف", 3) and not about("التسويف", 4) and topics.match(["التسويف"], 4, idx)[2] == ["التسويف"], "a summary only mentions a subject; title and subjects are about it")
ok(score("التسويف", 3) == topics.TITLE and score("التسويف", 4) == topics.TEXT, "title beats summary; an attached preposition (للتسويف) is read")
ok(score("الغربة", 6) == topics.TITLE and score("الغربة", 5) == 0, "«الغربة» is not «الغرب»")
ok(score("الفضاء", 7) == topics.SUBJECTS, "a subject listed in the book's subjects")
ok(topics.searchable(["تاريخ", "بدون كلام فاضي", "الأندلس"], idx) == ["بدون كلام فاضي", "الأندلس"] or topics.searchable(["تاريخ", "الأندلس"], idx) == ["الأندلس"], "only subjects with a specific word are searched")
ok(score("المال", 5) == 0 and not (topics.stems("المالي") & topics.stems("المال")), "«المالي» is not «المال»; only a longer stem takes the adjective ending")
ok(topics.content_words("أدب السجون") == topics.content_words("السجون"), "«أدب» in a shelf label is not a word to look for")
ok(score("تحفيزي فاضي", 3) == 0, "every specific word of a subject must be in the book")
topics._cache.update(index=None)

# ---------- chat with a mocked model
c.post("/api/admin/llm", json={"provider": "openai", "model": "gpt-6-luna"})
os.environ["OPENAI_API_KEY"] = "k-o"
c.post("/api/admin/llm", json={"provider": "openai", "model": "gpt-6-luna"})
EMPTY = llm.clean_prefs({})
def prefs(**kw): d = {k: (v if v is not None else "any") for k, v in EMPTY.items()}; d.update(kw); return d
script, calls = {}, []
def fake_complete(system, messages, schema, max_tokens=700, active=None):
    kind = "interpret" if "intent" in schema["properties"] else "respond"
    calls.append({"kind": kind, "system": system, "messages": messages, "max_tokens": max_tokens})
    step = script[kind]
    return step(system, messages) if callable(step) else step
llm.complete_json = fake_complete
def facts_of(call): return json.loads(call["system"].split("<facts>\n")[1].split("\n</facts>")[0])
def show_all(system, messages): return {"reply": "هذي", "show_book_ids": [f["id"] for f in facts_of({"system": system})], "suggestions": []}
def say(msgs): return c.post("/api/chat", json={"messages": msgs}).get_json()
con = sqlite3.connect(models.DB_PATH)

script["respond"] = show_all
script["interpret"] = {"intent": "recommend", "preferences": prefs(genres=["رواية"], unsupported_requests=["الغربة"]), "mentioned_titles": [], "wants_new_options": False}
r = say([{"role": "user", "content": "رواية عن الغربة"}])
f = facts_of(calls[-1])
ok(f and "غربة" in f[0]["title"] and "«الغربة»" in f[0]["why_it_matches"], "the book about the subject is the first fact and says so")
ok("الزائر يبغى موضوع: الغربة" in calls[-1]["system"] and "البحث لا يستطيع" not in calls[-1]["system"], "a subject that was found is no longer called unsearchable")
ok(r["books"] and "غربة" in r["books"][0]["title"] and "الغربة" in r["books"][0]["reason"], "the visitor sees the subject as the reason")
ok(calls[-2]["kind"] == "interpret" and calls[-2]["max_tokens"] >= 2500, "the interpreter has room to finish its answer")

script["interpret"] = {"intent": "clarify", "preferences": prefs(), "mentioned_titles": [], "wants_new_options": False}
r = say([{"role": "user", "content": "عطني أي شي حلو"}])
f = facts_of(calls[-1])
starters = [x for x in f if "اقتراح للبداية" in x["why_it_matches"]]
if starters:   # the test catalog has researched cards with a pleasant mood
    ok(2 <= len(starters) <= 3 and len({x["id"] for x in starters}) == len(starters), "an open request gets two or three different starting points")
    ok("بدايات متنوعة" in calls[-1]["system"], "and the model is told to present them all")
    genres = [con.execute("select genre from books where book_id=?", (x["id"],)).fetchone()[0] for x in starters]
    ok(len(set(genres)) == len(genres), "each from a different category")
else:
    ok("اسأل سؤالًا واحدًا" in calls[-1]["system"], "no researched pleasant book in the test catalog: the model only asks")

script["interpret"] = {"intent": "about_book", "preferences": prefs(), "mentioned_titles": ["كتاب لا وجود له إطلاقا"], "wants_new_options": False}
r = say([{"role": "user", "content": "عندكم كتاب لا وجود له إطلاقا؟"}])
ok(facts_of(calls[-1]) == [] and r["books"] == [], "a title we do not have: no random alternative is offered")
ok("غير متوفر عندنا حاليًا" in calls[-1]["system"] and "لا تعرض أي كتاب" in calls[-1]["system"], "and the model is told to say so and ask")

book3 = con.execute("select title from books where book_id=3").fetchone()[0]
script["interpret"] = {"intent": "about_book", "preferences": prefs(), "mentioned_titles": [book3], "wants_new_options": False}
say([{"role": "user", "content": f"عندكم {book3}؟"}])
ok(facts_of(calls[-1])[0]["id"] == 3 and "غير متوفر عندنا حاليًا" not in calls[-1]["system"], "a title we do have is answered as before")

have = con.execute("select author from books where active=1 and quantity>0 and author is not null limit 1").fetchone()[0]
script["interpret"] = {"intent": "recommend", "preferences": prefs(authors=[have, "مؤلف غير موجود أبدا"]), "mentioned_titles": [], "wants_new_options": False}
say([{"role": "user", "content": "أحب " + have + " ومؤلف غير موجود أبدا"}])
ok("No available book was found under: مؤلف غير موجود أبدا" in calls[-1]["system"] and have not in calls[-1]["system"].split("No available book was found under:")[1].split(".")[0], "each named author is checked on his own")
two = [r[0] for r in con.execute("select author from books where active=1 and quantity>0 and author is not null and author not like '%،%' and author not like '% و%' group by author having count(*)>=2 limit 2")]
style = {"known": True, "formats": ["novel"], "subgenres": ["رواية نفسية"], "moods": ["dark"], "pace": "slow", "length": "long", "themes": ["الخير والشر"]}
script["interpret"] = {"intent": "recommend", "preferences": prefs(), "mentioned_titles": [], "wants_new_options": False, "reference_books": [],
                       "author_request": {"name_as_written": two[0] + " و" + two[1], "full_name": "، ".join(two), "aliases": two, "mode": "similar", "style": style}}
n_before = len(calls)
r = say([{"role": "user", "content": f"أحب {two[0]} و{two[1]}، وش عندكم يشبههم؟"}])
ok(len(calls) == n_before + 2 and calls[-1]["kind"] == "respond" and "تقصد" not in r["reply"], "two authors named for likeness: no which-one question")
ok(not any(b["author"] in two for b in r["books"]), "and none of their own books is offered as «like them»")
script["interpret"]["author_request"] = {"name_as_written": two[0], "full_name": two[0], "aliases": [], "mode": "works", "style": dict(style, known=False)}
r = say([{"role": "user", "content": f"وش عندكم لـ{two[0]}؟"}])
ok(r["books"] and all(b["author"] == two[0] for b in r["books"]), "one author's own books are still found as before")
# ---------- second round of reader tests (2026-10-07)
import keyword_prefs
u = A.local_understand(["أبغى رواية بس ما تكون كئيبة ولا فيها موت، ولا أبغى شي رومانسي"])
ok("romantic" in u["moods_exclude"] and "romantic" not in u["moods_include"], "«ولا أبغى شي رومانسي»: the negation attached to «و» is read")
ok("موت" not in u["title_keywords"], "a negated word is not searched as a title word")
books = {1: {"book_id": 1, "title": "العمى", "quantity": 1, "active": 1}, 2: {"book_id": 2, "title": "البومة العمياء", "quantity": 1, "active": 1},
         3: {"book_id": 3, "title": "الحزن العميق", "quantity": 1, "active": 1}, 4: {"book_id": 4, "title": "أزهار عباد الشمس العمياء", "quantity": 1, "active": 1},
         5: {"book_id": 5, "title": "1984", "quantity": 1, "active": 1}, 6: {"book_id": 6, "title": "1984", "quantity": 1, "active": 1}, 7: {"book_id": 7, "title": "1984", "quantity": 1, "active": 1},
         8: {"book_id": 8, "title": "الجريمة والعقاب (1-2)", "quantity": 1, "active": 1}}
got = [b["book_id"] for b in A.match_titles(["العمى", "١٩٨٤"], books)]
ok(got[0] == 1 and 2 not in got and 3 not in got and 4 not in got, "an exact title is not lost among looser matches")
ok(len([i for i in got if i in (5, 6, 7)]) == 2, "the second title of a comparison is found too, two editions at most")
ok([b["book_id"] for b in A.match_titles(["الجريمة والعقاب"], books)] == [8], "a title with an edition note is still found")
ok("Always write the reply and the suggestions in English" in chat.RESPOND_SYSTEM_EN, "English visitors are answered in English")
retry_bodies = []
real_post, real_complete = llm._post, llm.complete_json
def fake_post(url, body, headers, timeout):
    retry_bodies.append(body)
    return {"choices": [{"message": {"content": "" if len(retry_bodies) == 1 else "{\"x\": 1}"}}]}
llm._post = fake_post
import importlib
r = llm._complete_json(llm.PROVIDERS["ollama"], "ollama", "m", "sys", [{"role": "user", "content": "hi"}], {"type": "object", "properties": {"x": {"type": "integer"}}}, 600, 5)
llm._post = real_post
ok(r == {"x": 1} and "reasoning_effort" not in retry_bodies[0] and retry_bodies[1].get("reasoning_effort") == "none", "an empty model answer is retried without the thinking phase")
script["interpret"] = {"intent": "recommend", "preferences": prefs(genres=["رواية"], unsupported_requests=["الغربة"]), "mentioned_titles": [], "wants_new_options": False}
script["respond"] = show_all
say([{"role": "user", "content": "أبغى رواية بس ما تكون كئيبة ولا فيها غربة"}])
ok("الزائر يبغى موضوع" not in calls[-1]["system"] and not any("«الغربة»" in f["why_it_matches"] for f in facts_of(calls[-1])), "a subject the visitor rejects is not searched for")
ok("إذا كتب الزائر بالفصحى" in chat.RESPOND_SYSTEM_AR, "a visitor writing in formal Arabic is answered in it")
if os.path.exists(llm.SETTINGS_PATH): os.remove(llm.SETTINGS_PATH)
print(f"TOTAL PASS={P} FAIL={F}")
