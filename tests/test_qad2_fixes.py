"""Fixes after the daily chat test round of 2026-10-12 (QAD; Ibrahim «طيب صلح كل الاخطاء»).

One check per error of research/chat_tests_2026-10-12/report.md still open after QAFIX (bac8b30):
the publisher and the other editions of an asked book, the library in numbers, «بنتي ١٥» and «لي أنا كأم»,
the prize / years kept on «أبيها بالعربي», a sub-prize by its full name, «جديد هالأسبوع», and a subject we
hold no book about.
"""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm, chat, chat_rules as R, author_flow, honors, app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

seen = {}
def fake_respond(conv, facts_json, guidance, active, *a):
    seen["facts"] = json.loads(facts_json); seen["guidance"] = guidance
    return {"reply": "x", "show_book_ids": [f["id"] for f in seen["facts"]][:4], "suggestions": [], "unanswered": ""}
chat.respond = fake_respond
def interp(prefs=None, intent="recommend", titles=(), author=None):
    chat.interpret = lambda conv, active, *a: {"intent": intent, "prefs": llm.clean_prefs(prefs or {}), "mentioned_titles": list(titles),
                                              "title_variants": [], "wants_new_options": False, "references": [],
                                              "author_request": author or {}}
def say(*msgs, ids=None):
    conv = [{"role": "user" if i % 2 == 0 else "assistant", "content": m} for i, m in enumerate(msgs)]
    for c in conv:
        if c["role"] == "assistant" and ids:
            c["book_ids"] = list(ids)
    return A.run_chat(conv, active=("fake", "m"))
books = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
avail = [b for b in books.values() if A.is_available(b)]

# ---------------------------------------------------------------- 1. the reader (case 38)
ok(R.ages_in("وفيه شي للمراهق نفسه يقراه؟ بنتي ١٥") == [15] and A._ages("بنتي ١٥") == [15], "«بنتي ١٥» is an age")
ok(R.ages_in("ولدي ٩ سنين وبنتي ٤، ابي لهم قصص") == [9, 4], "«ولدي ٩ سنين وبنتي ٤»: two ages, in order, none twice")
kids = ["ولدي ٩ سنين وبنتي ٤، ابي لهم قصص", "للصغيرة", "وللكبير شي عن الفضاء"]
ok(R.reader_person(kids[:2]) == ("f", 4) and R.reader_person(kids) == ("m", 9), "«للصغيرة» is the girl (4), «وللكبير» the boy (9)")
ok(R.ages_in("ابي لبنتي 3 كتب") == [], "«لبنتي 3 كتب» is a number of books, not an age")
ok(A.detect_audience(A.normalize("بنتي ١٥")) == "young_adult", "«بنتي ١٥»: a teenager")
two = ["ابي كتب عن الصحة النفسية للمراهقين، لي أنا كأم", "وفيه شي للمراهق نفسه يقراه؟ بنتي ١٥"]
ok(A.audience_age(two) == 15 and A.chat_audience(two, {"audience": "young_adult"}, False) == "young_adult", "then the daughter, 15")
ok(R.parent_said(two[0]) and A.said_for_adult(two[:1]), "«لي أنا كأم»: the reader is the mother")
ok(A.chat_audience(two[:1], {"audience": "young_adult", "genres": ["يافعين"]}, False) is None, "teenagers are the subject, not the reader")
ok(not R.parent_said("ابي كتاب كامل عن التاريخ") and not R.parent_said("رواية للمراهقين"), "no parent read into «كامل» / «للمراهقين»")
interp({"audience": "young_adult", "genres": ["يافعين"]})
out = say(two[0])
ok(out.get("audience") is None and "القارئ هو الكبير السائل نفسه" in seen["guidance"], "the reply is told: books for the parent")
ok(not any(books[f["id"]].get("genre") in A.CHILD_GENRES for f in seen["facts"]), "no children's or YA books offered to the mother")

# ---------------------------------------------------------------- 2. the library in numbers (cases 8, 44)
for q in ("وش التصنيفات اللي عندكم؟", "كم كتاب عندكم بالمكتبة؟", "طيب وش أكثر نوع عندكم؟", "How many books do you have?"):
    ok(R.overview_asked(q), f"«{q}» asks about the library itself")
for q in ("كم كتاب عندكم لنجيب محفوظ؟", "كم كتاب فاز بالبوكر", "عطني من تصنيف الشعر", "ابي رواية"):
    ok(not R.overview_asked(q), f"«{q}» does not")
interp({}, intent="clarify")
say("وش التصنيفات اللي عندكم؟")
top = max({b.get("genre") for b in avail if b.get("genre")}, key=lambda g: sum(1 for b in avail if b.get("genre") == g))
ok(f"المتوفر الآن: {len(avail)} عنوانًا" in seen["guidance"] and f"التصنيفات من الأكثر عناوين: {top}" in seen["guidance"],
   "the count of titles and every category, the largest first, reach the reply")

# ---------------------------------------------------------------- 3. publisher and editions (case 10)
fact = A.book_fact(dict(avail[0], publisher="دار تجربة"), None, "x")
ok(fact.get("publisher") == "دار تجربة", "the publisher of our edition is in the book's facts")
ok("publisher" in chat.RESPOND_SYSTEM_AR and "other_editions" in chat.RESPOND_SYSTEM_AR and "other_editions" in chat.RESPOND_SYSTEM_EN,
   "prompt: say the publisher and every edition when asked")
cat = {1: {"book_id": 1, "title": "فن الحرب", "author": "سون تزو", "active": 1, "quantity": 2, "price": 21.34, "publisher": "نهضة مصر", "language": "ar"},
       2: {"book_id": 2, "title": "فن الحرب", "author": "سون تزو", "active": 1, "quantity": 1, "price": 12.9, "publisher": "دار الكرمة", "language": "ar"},
       3: {"book_id": 3, "title": "فن الحرب", "author": "سون تزو", "active": 1, "quantity": 0, "price": 30, "publisher": "دار نافدة", "language": "ar"},
       4: {"book_id": 4, "title": "فن الحرب", "author": "مؤلف آخر", "active": 1, "quantity": 1, "price": 9, "publisher": "دار", "language": "ar"}}
eds = A.same_book_editions(cat[1], cat)
ok([e["book_id"] for e in eds] == [2] and A.edition_fact(eds[0])["publisher"] == "دار الكرمة",
   "other editions: same title and author, in stock only, with their publisher and price")

# ---------------------------------------------------------------- 4. prizes (cases 13.2 and 16)
for q in ("أبيها بالعربي", "عربية", "بس لكاتب عربي", "ابيها مترجمه"):
    ok(R.refine_only(q), f"«{q}» only adds a language / origin")
for q in ("ابي رواية", "للصغيرة", "ابي شي غير روائي"):
    ok(not R.refine_only(q), f"«{q}» is a request of its own")
U = "https://example.org/x"
b1, b2 = avail[0]["book_id"], avail[1]["book_id"]
honors.add("book_awards", b1, {"award_key": "goncourt", "result": "winner", "year": 2001, "source_url": U}, "test")
honors.add("book_awards", b2, {"award_key": "other", "award_name": "جائزة غونكور للرواية الأولى", "result": "winner", "year": 2017, "source_url": U}, "test")
honors.add("book_awards", b2, {"award_key": "other", "award_name": "جائزة الرواية العربية", "result": "winner", "year": 2010, "source_url": U}, "test")
honors.clear_cache()
ok(honors.other_name_in("فيه عندكم رواية فازت بغونكور للرواية الأولى؟") == "جائزة غونكور للرواية الأولى", "the sub-prize found by its full name")
ok(honors.other_name_in("رواية فازت بغونكور") is None, "«غونكور» alone stays the main prize")
ok(honors.other_name_in("ابي رواية عربية فازت بجائزة") is None, "two common words («الرواية العربية») are not a prize's name")
interp({"awards": ["goncourt"], "genres": ["رواية"]})
say("فيه عندكم رواية فازت بغونكور للرواية الأولى؟")
ids = [f["id"] for f in seen["facts"] if f["available"]]
ok(b2 in ids and b1 not in ids, "«غونكور للرواية الأولى»: its winner, not the main Goncourt's")
interp({"awards": ["goncourt"], "genres": ["رواية"]})
say("ابي رواية فازت بجائزة غونكور")
interp({"genres": ["رواية"]})   # the interpreter drops the prize on the follow-up
say("ابي رواية فازت بجائزة غونكور", "هذي الفائزة", "أبيها بالعربي")
ok([f["id"] for f in seen["facts"] if f["available"]][:1] == [b1], "«أبيها بالعربي» after a prize: still that prize")
interp({"genres": ["رواية"]})
say("ابي روايات كلاسيكية كتبت قبل ١٩٥٠", "هذي", "عربية")
interp({"genres": ["رواية"]}, intent="clarify")
say("ابي روايات كلاسيكية كتبت قبل ١٩٥٠", "هذي", "عربية", ids=[avail[2]["book_id"]])
ok("لا تقترح أي كتاب الآن" not in seen["guidance"], "«عربية» after books were shown: books again, not only a question")
ok("1949" in seen["guidance"] or "١٩٤٩" in seen["guidance"] or "1950" in seen["guidance"], "«عربية» after «قبل ١٩٥٠»: the year still holds")

# ---------------------------------------------------------------- 5. authors (case 33)
# «نزار قباني» -> «تقصد صباح قباني؟» stays: Ibrahim's rule of 2026-10-02 (a shared family name is asked about,
# «جمال العقاد» -> «عباس محمود العقاد», test_reference.py); the follow-up hang was QAFIX's fix (bac8b30)
cat_a = [{"book_id": 1, "author": "صباح قباني", "active": 1, "quantity": 1}]
byd = {author_flow.display(b["author"]): [b] for b in cat_a}
ok(author_flow.near_authors("نزار قباني", byd, cat_a) == ["صباح قباني"], "a shared family name is still asked about (Ibrahim's rule)")

# ---------------------------------------------------------------- 6. new releases, subjects (cases 1, 24)
ok(A.new_release_said("يا هلا، وش عندكم جديد هالأسبوع؟") and A.new_release_said("what's new?") is not None, "«جديد هالأسبوع» is a new-release wish")
ok(A.new_release_wished(["وش عندكم جديد هالأسبوع؟", "ابي شي غير روائي"]) == "carried", "and it carries on to the next message")
interp({"unsupported_requests": ["اختيار التخصص الجامعي"]})
say("عمري ١٧ وابي كتاب يساعدني اختار تخصص الجامعة")
ok("ما لقيت كتابًا عنه هنا" in seen["guidance"], "a subject we hold no book about: say so first")

# (the frozen test catalog stocks Nizar Qabbani himself, so a made-up poet stands in for him)
# ---------------------------------------------------------------- 7. a well-known author with no book (Ibrahim 2026-10-12)
import sqlite3 as _sq, open_questions
_c = models.get_connection()
_c.execute("""INSERT INTO authors (name_ar, name_latin, aliases, known_for, formats, subgenres, moods, themes, pace, typical_length,
              audience, style_note_ar, research_status, research_note, created_at, updated_at)
              VALUES ('سامي القباني', 'Sami Al-Qabbani', '[]', '["قالت لي السمراء", "الرسم بالكلمات"]', '["poetry"]', '["شعر غزل"]',
                      '["romantic"]', '["الحب"]', 'medium', 'short', 'adult', 'شاعر الحب والمرأة', 'researched',
                      'well-known author, no book in our catalog when added', '2026-10-12', '2026-10-12')""")
_c.commit(); _c.close()
nreq = {"mode": "works", "name_as_written": "سامي القباني", "full_name": "", "aliases": []}
near_cat = [{"author": "منذر القباني", "active": 1, "quantity": 1}]
r, names, direct = author_flow.resolve(dict(nreq), [{"role": "user", "content": "عندكم شي لسامي القباني؟"}], near_cat)
ok(r is None and not names, "a stored well-known author with no book: no «تقصد منذر القباني؟» (the «نزار قباني» case)")
ok(A.known_absent_author(nreq, books) is not None, "known_absent_author finds him (no book in stock)")
interp({"authors": ["سامي القباني"]}, author=dict(nreq))
out = say("عندكم شي لسامي القباني؟")
ok(not (out.get("_debug") or {}).get("author_gate") and "مؤلف معروف" in seen["guidance"] and "ما عندنا من كتبه شيء الآن" in seen["guidance"]
   and "«قالت لي السمراء»" in seen["guidance"], "the reply is told: he is known (his works), none in stock, then the closest books")
_c = models.get_connection()
ok(_c.execute("SELECT COUNT(*) FROM open_questions WHERE kind='missing_author' AND phrase LIKE '%سامي القباني%'").fetchone()[0] == 1,
   "the request is logged for the library (أسئلة بلا جواب)")
_c.close()
ok(A.known_absent_author({"mode": "works", "name_as_written": books[avail[0]["book_id"]]["author"]}, books) is None,
   "an author with books in stock is not «absent»")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
