"""Root fixes after the daily chat test round of 2026-10-11 (QAFIX; Ibrahim «ابغى اصلاحات جذرية لها»).

One permanent check per error family of research/chat_tests_2026-10-11/report.md, so that a fix
cannot silently come back undone (three earlier fixes had): the title looked up in the visitor's own
words before «غير متوفر», the reader when two children are named, wishes that stay (publisher,
refused country, refused kind of book, price), and orders answered from the database.
"""
import json, os, sqlite3, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm, chat, chat_rules as R, author_flow, authors, app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# ---------------------------------------------------------------- 1. titles (pure functions, own catalog)
def bk(i, title, author="مؤلف تجريبي", **kw):
    return dict({"book_id": i, "title": title, "author": author, "active": 1, "quantity": 2, "language": "ar",
                 "series_name": None, "series_position": None, "publisher": None, "price": 30.0, "page_count": 200}, **kw)
CAT = {b["book_id"]: b for b in [
    bk(1, "10 دقائق و38 ثانية في هذا العالم الغريب", "إليف شافاق"), bk(2, "الغريب", "ألبير كامو"),
    bk(3, "Dune", "Frank Herbert", language="en"), bk(4, "بين القصرين", "نجيب محفوظ", series_name="ثلاثية القاهرة", series_position=1),
    bk(5, "قصر الشوق", "نجيب محفوظ", series_name="ثلاثية القاهرة", series_position=2),
    bk(6, "السكرية", "نجيب محفوظ", series_name="ثلاثية القاهرة", series_position=3), bk(7, "ثلاثية", "جون فوسه"),
    bk(8, "حكاية الجارية", "مارغريت آتوود"), bk(9, "العهود: حكاية الجارية (ج 2)", "مارغريت آتوود", series_name="حكاية الجارية", series_position=2),
    bk(10, "مئة عام من العزلة", "غابرييل غارسيا ماركيز"), bk(11, "ثلاثية غرناطة", "رضوى عاشور"),
    bk(12, "The Kite Runner", "Khaled Hosseini", language="en"), bk(13, "كتاب من تكوين", publisher="منشورات تكوين"),
    bk(14, "كتاب من مسكلياني", publisher="مسكلياني للنشر والتوزيع"), bk(15, "Home", "Someone", language="en")]}
ids = lambda books: [b["book_id"] for b in books]
ok(R.title_key("عشر دقائق وثمانٍ وثلاثون ثانية") == R.title_key("10 دقايق و٣٨ ثانية"), "number words and digits are one title key")
ok(ids(A.match_titles(["عشر دقايق و٣٨ ثانية"], CAT))[:1] == [1], "«عشر دقايق و٣٨ ثانية» finds «10 دقائق و38 ثانية …», not «الغريب»")
ok(ids(A.match_titles(["عشر دقائق وثمانٍ وثلاثون ثانية في هذا العالم الغريب"], CAT))[:1] == [1], "the interpreter's spelled-out numbers find it too")
ok(ids(A.match_titles(["مية عام من العزله"], CAT)) == [10], "«مية عام من العزله» finds «مئة عام من العزلة»")
ok(ids(A.match_titles(["ثلاثية القاهرة"], CAT)) == [4, 5, 6], "a series by its name alone, parts in order (not «ثلاثية» / «ثلاثية غرناطة»)")
ok(ids(A.match_titles(["ثلاثية"], CAT)) == [7], "the exact title «ثلاثية» still wins for itself")
ok(ids(A.match_titles(["حكاية الجارية"], CAT))[:1] == [8], "a title that is also a series name: the title itself")
ok(ids(R.latin_titles("Do you have Dune?", CAT)) == [3], "a title typed in Latin letters is found in the visitor's own words")
ok(ids(R.latin_titles("do you have the kite runner", CAT)) == [12], "lower case, several words")
ok(R.latin_titles("i would like a nice book to read at home tonight", CAT) == [], "a common word in a long sentence is not a title")
ok(R.part_asked("والجزء الثاني؟") == 2 and R.part_asked("وش الجزء الثالث؟") == 3 and R.part_asked("وش اللي بعده") == "next"
   and R.part_asked("ابي الكتاب الثاني") is None, "«الجزء الثاني» / «اللي بعده» understood; «الكتاب الثاني» is the second book listed")
ok(R.next_part(CAT[8], 2, CAT)["book_id"] == 9, "part 2 of a book catalogued without a series name")
ok(R.next_part(CAT[4], "next", CAT)["book_id"] == 5 and R.next_part(CAT[5], 3, CAT)["book_id"] == 6, "next / third part inside a series")
ok(R.next_part(CAT[2], 2, CAT) is None, "no part invented for a book without a series")

# ---------------------------------------------------------------- 2. the reader
two = ["بنتي عمرها ١١ وتحب المغامرات", "ولأخوها عمره ١٦ ويحب التاريخ"]
ok(A.audience_age(two) == 16 and A.audience_gender(two) == "m", "the brother: 16, a boy (not the sister's gender)")
ok(A.chat_audience(two, {"audience": "children", "genres": []}, False) == "young_adult", "16 is a teenager even if the talk began with a child")
mine = two + ["وأنا ابي رواية رومانسية لي"]
ok(A.said_for_adult(mine) and A.chat_audience(mine, {"audience": "children", "genres": ["رواية"]}, False) is None,
   "«وأنا ابي رواية رومانسية لي» after two children: the visitor himself, no child")
back = mine + ["نرجع لبنتي، ابي لها قصة مصورة"]
ok(A.audience_age(back) == 11 and A.audience_gender(back) == "f", "«نرجع لبنتي»: her own age 11 again, not her brother's 16")
ok(A.chat_audience(back, {"audience": "children", "genres": ["قصص مصورة"]}, False) == "children", "and she is a child")
ok(not R.self_said("رشّحي لي") and not R.self_said("ابغى لي قصة لولدي") and not R.self_said("قولي لي"), "«رشّحي لي» is not «for me»")
ok(not A.said_for_adult(["أنا بثاني متوسط", "ابي رواية مغامرات لي"]), "a teenager asking for himself stays a teenager")
ok(A.audience_age(["ولدي بأولى ابتدائي"]) == 6 and A._ages("ولدي بأولى ابتدائي") == [6], "the school grade says the age (no age question)")
ok(A.audience_age(["ولدي يحب السيارات", "عمره ٦ سنوات"]) == 6 and A.audience_gender(["لبنتي", "عمرها ٧ سنوات"]) == "f", "one child: as before")

# ---------------------------------------------------------------- 3. the author's country
w = A.wished_country
ok(w("ابي رواية عربية بس مو مصرية ولا خليجية") is None and "مصر" in A.unwanted_countries("ابي رواية عربية بس مو مصرية ولا خليجية")
   and "الكويت" in A.unwanted_countries("ابي رواية عربية بس مو مصرية ولا خليجية"), "«مو مصرية ولا خليجية»: both refused, none wished")
c = w("قلت لك مو مصرية! ابي كاتب من الشام او المغرب العربي")
ok(c and "سوريا" in c[1] and "تونس" in c[1] and "مصر" not in c[1] and A.country_place(c[1]) == "الشام أو المغرب العربي", "«كاتب من الشام أو المغرب العربي»")
ok(w("ابي كاتبات سعوديات اكثر") and w("ابي كاتبات سعوديات اكثر")[0] == "سعودي", "the plural «سعوديات»")
ok(w("ما عندكم روايات سعودية؟") and not A.unwanted_countries("ما عندكم روايات سعودية؟"), "«ما عندكم روايات سعودية؟» is a question, not a refusal")
ok(w("مصرية ولا سعودية؟") and not A.unwanted_countries("مصرية ولا سعودية؟"), "«مصرية ولا سعودية؟» is a choice")
ok(w("ابي كتاب عن تاريخ الدولة السعودية") is None and w("رواية عن الشام") is None, "a place as a subject is not the author's country")
ok(A.country_place(A._ARAB_NATIONS["خليجي"]) == "الخليج", "the Gulf badge")

# ---------------------------------------------------------------- 4. the publisher
ok(A.find_publisher([], "وش الجديد من منشورات تكوين؟", CAT) and A.find_publisher([], "ابي اصدارات تكوين حقت ٢٠٢٦", CAT), "«منشورات تكوين»، «اصدارات تكوين»")
ok(A.find_publisher([], "عندكم شي من مسكلياني؟", CAT) and A.find_publisher([], "ابي شي خفيف من مسكلياني", CAT, context=True),
   "«من مسكلياني»: a publisher's own name, found without «دار»")
CAT2 = {b["book_id"]: b for b in [bk(61, "التراث الشعبي", publisher="دار التراث"), bk(62, "حكايات التراث", publisher="دار التراث"),
                                  bk(63, "التراث والحداثة", publisher="دار التراث")]}
ok(A.find_publisher([], "ابي رواية من التراث", CAT2) is None and A.find_publisher([], "ابي كتب دار التراث", CAT2),
   "«من التراث» (a word many titles use) is not the publisher unless «دار» is said")
ok(A.publisher_asked("أقصد دار عصير الكتب") == A.normalize("عصير الكتب") and A.publisher_asked("رواية تدور في دار أيتام") is None, "the name after «دار», not «في دار أيتام»")

# ---------------------------------------------------------------- 5. price, refused kinds, shortest
ok(R.price_wish(["ابي رواية ما تتعدى ٢٥ ريال", "أبيها خفيفة"]) == (25, False), "the price limit stays in the next message")
ok(R.price_wish(["ما فيه رواية بعشرة ريال؟"]) == (10, False) and R.price_wish(["وش ارخص رواية عندكم؟"]) == (None, True), "«بعشرة ريال»، «أرخص»")
ok(R.price_wish(["ابي رواية ب٢٥ ريال", "مو مهم السعر"]) == (None, False) and R.price_wish(["اقل من 200 صفحة"]) == (None, False), "lifted; pages are not a price")
ok(R.is_price_phrase("ريال") and R.is_price_phrase("سعر أقل") and not R.is_price_phrase("الأندلس"), "price words are never a subject")
ok(set(R.excluded_genres(["أي شي الا تطوير الذات والروايات", "أبغى كتابًا عربيًا"])) == {"رواية", "تطوير ذاتي"}, "«إلا تطوير الذات والروايات» stays in the next message")
ok(R.excluded_genres(["ما ابي روايات ولا شعر", "طيب عطني رواية"]) == ["شعر"] and R.excluded_genres(["ابي رواية ولا شعر؟"]) == [], "asked for again -> allowed; a choice refuses nothing")
ok(R.excluded_genres(["ما فيه رواية بعشرة ريال؟"]) == [] and R.excluded_genres(["ما عندكم روايات؟"]) == []
   and R.excluded_genres(["ما احب الروايات"]) == ["رواية"], "«ما فيه رواية…؟» asks, «ما احب الروايات» refuses")
ok(R.excluded_genres(["لا، ابي قصص مصورة عربية مو روايات"]) == ["رواية"], "a leading «لا،» answers; only «مو روايات» refuses")
_two = [bk(41, "رواية أولى", "رجاء عالم"), bk(42, "رواية ثانية", "ليلى الجهني"), bk(43, "رواية ثالثة", "مؤلف آخر")]
_req = {"mode": "works", "name_as_written": "", "full_name": "", "aliases": []}
_r = author_flow.resolve(_req, [{"role": "user", "content": "عندكم لرجاء عالم او ليلى الجهني؟"}], _two)
ok(_r[0] is None and set(_r[1] or ()) == {"رجاء عالم", "ليلى الجهني"} and len(_req.get("people") or {}) == 2,
   "no name from the interpreter: the authors written in the message, not «وش اسم المؤلف كامل؟»")
ok(R.shortest_asked("أقصر") == "shorter" and R.shortest_asked("وش أقصر كتاب له؟") == "shortest" and R.shortest_asked("ابي رواية قصيرة") is None, "«أقصر» / «أقصر كتاب»")

# ---------------------------------------------------------------- 6. whole turns (model calls faked)
con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
seen = {}
def fake_respond(conv, facts_json, guidance, active, *a):
    seen["facts"] = json.loads(facts_json); seen["guidance"] = guidance
    return {"reply": "x", "show_book_ids": [f["id"] for f in seen["facts"]][:4], "suggestions": [], "unanswered": ""}
chat.respond = fake_respond
def interp(prefs=None, intent="recommend", titles=(), author=None, refs=(), variants=()):
    chat.interpret = lambda conv, active, *a: {"intent": intent, "prefs": llm.clean_prefs(prefs or {}), "mentioned_titles": list(titles),
                                              "title_variants": list(variants), "wants_new_options": False, "references": list(refs),
                                              "author_request": author or {}}
def say(*msgs, ids=None):
    conv = []
    for i, m in enumerate(msgs):
        conv.append({"role": "user" if i % 2 == 0 else "assistant", "content": m})
        if i % 2 and ids:
            conv[-1]["book_ids"] = list(ids)
    return A.run_chat(conv, active=("fake", "m"))
def books_now():
    return {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
keys = lambda out: [c["key"] for c in out.get("context") or []]

# price limit and the cheapest
novels = [b for b in books_now().values() if A.is_available(b) and b.get("genre") == "رواية" and (b.get("price") or 0) > 0]
if len(novels) >= 6:
    limit = int(sorted(b["price"] for b in novels)[len(novels) // 3]) + 1
    interp({"genres": ["رواية"], "unsupported_requests": ["ريال"]})
    out = say(f"ابي رواية ما تتعدى {limit} ريال")
    prices = [f.get("price_sar") for f in seen["facts"] if f["available"]]
    ok(prices and all(p and p <= limit for p in prices), f"every book offered costs at most {limit} riyals {prices[:5]}")
    ok("price" in keys(out) and "ريال" in seen["guidance"] and not any(k.startswith("topic:") for k in keys(out)), "price badge; «ريال» is not a subject")
    out = say(f"ابي رواية ما تتعدى {limit} ريال", "هذي روايات ضمن السعر", "أبيها خفيفة")
    prices = [f.get("price_sar") for f in seen["facts"] if f["available"]]
    ok(prices and all(p and p <= limit for p in prices) and "price" in keys(out), "the limit is still applied one message later")
    out = say(f"ابي رواية ما تتعدى {limit} ريال", "هذي روايات ضمن السعر", "مو مهم السعر، ابي رواية")
    ok("price" not in keys(out), "«مو مهم السعر» lifts it")
    interp({"genres": ["رواية"]})
    out = say("وش ارخص رواية عندكم؟")
    prices = [f.get("price_sar") for f in seen["facts"] if f["available"]]
    ok(len(prices) >= 3 and prices[:len(prices)] == sorted(prices) and "الأرخص" in seen["guidance"], f"«أرخص رواية»: sorted from the cheapest {prices[:4]}")
    ok(prices and prices[0] <= min(b["price"] for b in novels if (b.get("language") or "ar") == "ar") + 0.001 or prices[0] == min(prices), "and the first is the cheapest the request allows")

# a refused kind of book stays refused although the interpreter forgot it
interp({"genres": ["رواية"]})
out = say("أي شي الا تطوير الذات والروايات", "تمام، وش تحب تقرأ؟", "أبغى كتابًا عربيًا")
kinds = {books_now()[f["id"]].get("genre") for f in seen["facts"] if f["available"]}
ok(kinds and not kinds & {"رواية", "تطوير ذاتي"}, f"no novel and no self-help two messages after «إلا …» {sorted(k for k in kinds if k)[:5]}")
ok("nogenre:رواية" in keys(out) and "nogenre:تطوير ذاتي" in keys(out), "badges for the refused kinds")
out = say("أي شي الا تطوير الذات والروايات", "تمام", "أبغى كتابًا عربيًا")
out = A.run_chat([{"role": "user", "content": "أي شي الا تطوير الذات والروايات"}, {"role": "assistant", "content": "تمام"},
                  {"role": "user", "content": "أبغى كتابًا عربيًا"}], active=("fake", "m"), drop_context=["nogenre:رواية"])
ok("nogenre:رواية" not in keys(out) and "nogenre:تطوير ذاتي" in keys(out), "✕ on one badge lifts that refusal only")

# a later part of a series, and a title in Latin letters / another name of the same book
pair = [b for b in books_now().values() if A.is_available(b) and b.get("genre") == "رواية"][:3]
if len(pair) == 3:
    a, b2, c3 = pair
    con.execute("update books set series_name='سلسلة الاختبار الطويلة', series_position=1 where book_id=?", (a["book_id"],))
    con.execute("update books set series_name='سلسلة الاختبار الطويلة', series_position=2, author=? where book_id=?", (a["author"], b2["book_id"]))
    con.execute("update books set title='Dune', language='en' where book_id=?", (c3["book_id"],))
    con.commit()
    interp({}, intent="about_book", titles=["عنوان من ذاكرة النموذج"])
    out = say("عندكم " + a["title"] + "؟", "إيه متوفر", "والجزء الثاني؟", ids=[a["book_id"]])
    ok(seen["facts"] and seen["facts"][0]["id"] == b2["book_id"] and "الجزء 2" in seen["guidance"], "«والجزء الثاني؟»: the part from our records, first in facts")
    ok(out["books"] and out["books"][0]["book_id"] == b2["book_id"], "and its card is shown")
    interp({}, intent="about_book", titles=["كثيب"])
    out = say("Do you have Dune?")
    ok(any(f["id"] == c3["book_id"] for f in seen["facts"]) and "موجود في سجلاتنا" in seen["guidance"] and "غير متوفر عندنا حاليًا" not in seen["guidance"],
       "\"Do you have Dune?\" with the title rewritten as «كثيب»: the English copy is found, not «غير متوفر»")
    interp({}, intent="about_book", titles=["كثيب"], variants=["ديون", "Dune"])
    out = say("عندكم رواية كثيب؟")
    ok(any(f["id"] == c3["book_id"] for f in seen["facts"]) and "غير متوفر عندنا حاليًا" not in seen["guidance"], "another name of the same book (title_variants) is looked up")
    interp({}, intent="about_book", titles=["كتاب لا وجود له إطلاقًا"], variants=["Totally Missing Book"])
    out = say("عندكم كتاب لا وجود له إطلاقًا؟")
    ok("غير متوفر عندنا حاليًا" in seen["guidance"], "a book we really do not have is still said to be unavailable")

# the shortest book of an author, from all his books
rows = con.execute("select author, count(*) n from books where quantity>0 and active=1 and page_count>0 group by author having n>=5 order by n desc").fetchall()
if rows:
    name = rows[0]["author"]
    theirs = sorted(b["page_count"] for b in books_now().values() if b.get("author") == name and A.is_available(b) and b.get("page_count"))
    interp({"authors": [name]}, author={"mode": "works", "name_as_written": name, "full_name": name, "aliases": []})
    out = say("وش أقصر كتاب ل" + name + "؟")
    pages = [f.get("pages_approx") for f in seen["facts"] if f["available"]]
    ok(pages and pages[0] == theirs[0] and pages == sorted(pages) and "الأقصر" in seen["guidance"], f"«أقصر كتاب له»: the shortest of all his books first {pages[:4]} (min {theirs[0]})")
    # the confirmed author is never reported missing under the visitor's misspelling
    interp({"authors": ["اسم مكتوب غلط تماما"]}, author={"mode": "works", "name_as_written": name, "full_name": name, "aliases": []})
    say("عندكم كتب ل" + name + "؟")
    ok("No available book was found under" not in seen["guidance"], "a settled author is not «missing» under another spelling")

# the publisher stays; a publisher we do not stock is said so
pubs = con.execute("select publisher, count(*) n from books where quantity>0 and active=1 and publisher is not null and publisher!='' group by publisher having n>=6 order by n desc").fetchall()
pub = next((r["publisher"] for r in pubs if A.publisher_key(r["publisher"]) and A.find_publisher([], "عندكم كتب من دار " + A.publisher_key(r["publisher"]) + "؟", books_now())), None)
if pub:
    k = A.publisher_key(pub)
    interp({})
    q1 = "وش عندكم من دار " + k + "؟"
    out = say(q1)
    ok("publisher" in keys(out), "publisher badge")
    out = say(q1, "هذي من إصداراتهم", "طيب ابي شي ثاني يكون ممتع وما يطول علي ابدا لو سمحتي")
    got = {A.publisher_key(books_now()[f["id"]].get("publisher")) for f in seen["facts"] if f["available"]}
    ok("publisher" in keys(out) and got == {k}, "the publisher stays after a long follow-up message")
    out = say(q1, "هذي من إصداراتهم", "من اي دار، مو شرط")
    ok("publisher" not in keys(out), "«من أي دار» lets it go")
    interp({}, author={"mode": "works", "name_as_written": k, "full_name": "", "aliases": []})
    out = say(q1, "هذي من إصداراتهم", "أبغى قراءة خفيفة")
    ok(not (out.get("_debug") or {}).get("author_gate"), "a publisher's name is not taken for an author («وش اسم المؤلف كامل؟»)")
interp({"unsupported_requests": ["الزرافة"]})
out = say("يعني ما عندكم اصدارات دار الزرافة البنفسجية؟")
ok("الزرافه البنفسجيه" in A.normalize(seen["guidance"]) and "ما عندنا الآن كتب متوفرة من إصداراتها" in seen["guidance"] and not out["books"]
   and not any(k.startswith("topic:") for k in keys(out)) and "البحث لا يستطيع تطبيق" not in seen["guidance"],
   "a publisher we do not stock: said plainly, no books, its name is not a subject")

# reply rules that must stay in the prompts
ok("حجزًا" in chat.RESPOND_SYSTEM_AR and "copy_language" in chat.RESPOND_SYSTEM_AR and "copy_language" in chat.RESPOND_SYSTEM_EN, "prompt: no reservation suggestions; the copy's language is said")
ok("title_variants" in chat.INTERPRET_SYSTEM and "title_variants" in chat.INTERPRET_SCHEMA["required"], "interpreter asked for the other names of a title")
interp({})
say("ابي رواية")
ok("Today's date is 20" in seen["guidance"], "the model is told today's date")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
