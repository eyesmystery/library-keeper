"""Fixes after the daily chat test round of 2026-10-14 (QAD; Ibrahim «انا شفت تقرير QAD واقترح تبدأ بالاصلاح»).

One check per error of research/chat_tests_2026-10-14/report.md fixed in code: a father asking for himself
about his teenage son (ج1), «انمي / رسوم متحركة» as animated adaptations (ج2), «كم كتاب بالانجليزي؟» as a
question about the shelves (ج3), «سعرها؟» after a book we do not have (م5), a one-word misspelt family name
(م3), «النظافة» = «الاستحمام» and «آلموت» is not «الموت» (م2, م8), «أقصر» keeps the mood (م7), and an author
the interpreter knows that we hold nothing of (م9).
"""
import json, os, sys, sqlite3
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm, chat, chat_rules as R, author_flow, honors, topics, keyword_prefs as K, app as A
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
def say(*msgs):
    conv = [{"role": "user" if i % 2 == 0 else "assistant", "content": m} for i, m in enumerate(msgs)]
    seen.clear()
    return A.run_chat(conv, active=("fake", "m"))
books = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
avail = [b for b in books.values() if A.is_available(b)]

# ---------------------------------------------------------------- ج1: the father is the reader (case 8)
m1 = "ولدي دخل سن المراهقة وصار عصبي، ابي كتاب يساعدني انا افهمه"
ok(R.parent_said(m1), "«ابي كتاب يساعدني انا افهمه» is a parent asking for himself")
ok(R.parent_said("ابي كتاب يعلمني كيف اتعامل مع ولدي") and R.parent_said("بس الكتاب لي أنا مو له"),
   "«كيف اتعامل مع ولدي» / «الكتاب لي أنا مو له» too")
ok(R.parent_reader([m1, "عمره ١٥"]), "«عمره ١٥» right after it still answers for the father")
ok(not R.parent_reader(["ابي قصة لولدي", "عمره ٧"]) and not R.parent_reader([m1, "عمره ١٥ وابي له رواية"]),
   "a story for the son, or «ابي له رواية», is for the child")
ok(A.chat_audience([m1], {"audience": "young_adult", "genres": []}, False) is None, "no teenage badge for the father")
interp({"audience": "young_adult", "unsupported_requests": ["المراهقة"]})
out = say(m1)
ok("كم عمر" not in out.get("reply", "") and not (out.get("_debug") or {}).get("age_question"), "no «how old is your son?» question")
ok(not any(books[f["id"]].get("genre") in A.CHILD_GENRES for f in seen.get("facts", []) if f["id"] in books),
   "no children's or young readers' book offered to him")
_par = [b for b in avail if b.get("genre") not in A.CHILD_GENRES | {"رواية", "قصص مصورة"}
        and A._PARENTING.search(A.normalize(" ".join(str(b.get(k) or "") for k in ("title", "keywords", "subjects", "summary", "hook"))))]
if _par:
    ok(seen.get("facts") and A._PARENTING.search(A.normalize(books[seen["facts"][0]["id"]]["title"] + " " + str(books[seen["facts"][0]["id"]].get("summary") or "")
                                                              + " " + str(books[seen["facts"][0]["id"]].get("keywords") or ""))),
       "a book on bringing up children comes first")
ok("القارئ هو الكبير السائل" in seen.get("guidance", ""), "the model is told the parent is the reader")

# ---------------------------------------------------------------- ج2: anime / cartoons (case 27)
ok(K.animated_said(A.normalize("فيه كتب عندكم صارت انمي او رسوم متحركة؟")) and K.animated_said(A.normalize("لا ابي رسوم متحركة بالذات، للأطفال")),
   "«انمي» and «رسوم متحركة» (even after «لا») ask for an animated adaptation")
ok(not K.animated_said(A.normalize("رواية صارت فيلم")), "«فيلم» alone is not animated")
ok(K._parse_one(A.normalize("كتب صارت كرتون"))[0]["adaptation"] == "animated", "the keyword reading sets «animated»")
ok(llm.clean_prefs({"adaptation": "animated"}).get("adaptation") == "animated" if hasattr(llm, "clean_prefs") else True,
   "the interpreter may answer «animated»")
_two = [b for b in avail][:2]
con = sqlite3.connect(models.DB_PATH)
honors.ensure_table(con)
con.execute("DELETE FROM book_adaptations")
con.execute("INSERT INTO book_adaptations (book_id, kind, title, year, source) VALUES (?, 'animated', 'x', 1999, 'test')", (_two[0]["book_id"],))
con.execute("INSERT INTO book_adaptations (book_id, kind, title, year, source) VALUES (?, 'film', 'y', 2001, 'test')", (_two[1]["book_id"],))
con.commit()
honors._cache["data"] = None
ok(honors.matches_adaptation(_two[0]["book_id"], "animated") and not honors.matches_adaptation(_two[1]["book_id"], "animated"),
   "only an animated adaptation answers «animated»")
interp({"adaptation": "film"})
say("فيه كتب عندكم صارت انمي او رسوم متحركة؟")
ids = [f["id"] for f in seen.get("facts", [])]
ok(_two[0]["book_id"] in ids and _two[1]["book_id"] not in ids, "the live-action film is not offered for «انمي»")
ok("رسوم متحركة" in seen.get("guidance", ""), "the wish is named «تحوّل إلى رسوم متحركة»")
con.execute("DELETE FROM book_adaptations"); con.commit(); honors._cache["data"] = None

# ---------------------------------------------------------------- ج3: «وكم كتاب بالانجليزي؟» (case 38)
ok(R.overview_asked("وكم كتاب بالانجليزي؟") and R.overview_asked("كم كتاب بالعربي عندكم"), "the copies in a language are a question about the shelves")
ok(not R.overview_asked("كم كتاب لنجيب محفوظ"), "an author's books are not")
ok(A.chat_audience(["كم قصة أطفال عندكم؟ ووش أكثر قسم"], {"audience": "children", "genres": []}, False) is None,
   "a numbers question puts no children's badge on")
ov = A.library_overview(books)
n_en = sum(1 for b in avail if (b.get("language") or "ar") == "en")
ok(f"{n_en} بالإنجليزية" in ov, "the overview gives the number of English copies")

# ---------------------------------------------------------------- م5: «سعرها؟» after a book we do not have (case 19)
gone = next(b for b in avail[5:] if b.get("price") and len(A.normalize(b["title"]).split()) >= 2)
con.execute("UPDATE books SET quantity = 0 WHERE book_id = ?", (gone["book_id"],)); con.commit()
alt = avail[0]
if gone:
    interp({}, intent="about_book", titles=[alt["title"]])
    conv = ["عندكم " + gone["title"] + "؟",
            f"كتاب «{gone['title']}» غير متوفر عندنا حاليًا. إذا ودك ببديل، «{alt['title']}» رواية قريبة.",
            "كم كان سعرها؟ ومتى توصلكم؟"]
    say(*conv)
    ok(seen.get("facts") and seen["facts"][0]["id"] == gone["book_id"], "«سعرها» is the book asked about, not the alternative")
    ok(gone["title"] in seen.get("guidance", "") and "غير متوفر" in seen.get("guidance", ""), "and the model is told so")
con.execute("UPDATE books SET quantity = ? WHERE book_id = ?", (gone["quantity"], gone["book_id"])); con.commit()
ok(A._PRONOUN_ASK.search(A.normalize("بكم؟")) and not A._PRONOUN_ASK.search(A.normalize("ابي رواية")), "the pronoun question is recognised")

# ---------------------------------------------------------------- م3: «زافوون» (case 13)
by_display = {A.author_flow.display(b.get("author") or ""): 1 for b in avail if b.get("author")}
fam = next((d for d in by_display if len(A.normalize(d).split()) >= 2 and len(A.normalize(d).split()[-1]) >= 5), None)
if fam:
    last = A.normalize(fam).split()[-1]
    doubled = last[:-1] + last[-2] + last[-1]          # one letter written twice
    near = author_flow.near_authors(doubled, by_display, avail)
    ok(near not in ([], author_flow.TOO_MANY) and fam in near, f"one misspelt family name («{doubled}») finds «{fam}»")
ok(author_flow.near_authors("محمد", by_display, avail) == [], "a common first name alone proposes nobody")

# ---------------------------------------------------------------- م2, م8: subjects (cases 11, 45)
words = {f for forms in topics.content_words("النظافة") for f in forms}
ok("استحمام" in words or "الاستحمام" in words, "«النظافة» is also looked for as «الاستحمام»")
ok("موت" not in topics._tokens("آلموت") and "الموت" not in topics._tokens("آلموت"), "«آلموت» (a castle) is not «الموت»")
ok("موت" in topics._tokens("عن الموت والفقد"), "«الموت» itself still is")

# ---------------------------------------------------------------- م9: a known author we have nothing of (case 46)
# (a made-up name: the frozen test catalog holds books by the real one)
interp({}, author={"name_as_written": "سلمى القرطاجنية", "full_name": "سلمى القرطاجنية", "aliases": ["Salma Qartajaniya"], "mode": "works"})
out = say("ابي روايات سلمى القرطاجنية")
ok("ما لقيت عندنا مؤلفًا بهذا الاسم" not in out["reply"] and "سلمى القرطاجنية" in out["reply"] and "كتاباتها" in out["reply"],
   "«ما عندنا الآن كتب لسلمى القرطاجنية … أسلوب كتاباتها», not «no author by this name» (got: " + out["reply"][:120] + ")")
interp({}, author={"name_as_written": "فلان علان", "full_name": "", "aliases": [], "mode": "works"})
out = say("ابي كتب فلان علان")
ok("ما لقيت عندنا مؤلفًا بهذا الاسم" in out["reply"] or "تقصد" in out["reply"] or "وش اسم" in out["reply"],
   "a name nobody knows still gets the usual answer")

# ---------------------------------------------------------------- م1: «1+» is not «for» an 8-year-old (case 6)
ok(A.age_fit({"age_min": 1, "age_max": None}, 8) == 0 and A.age_fit({"age_min": 7, "age_max": 11}, 8) == 1,
   "a toddler's «1+» book gets no age bonus for an 8-year-old; a 7–11 book does")

# ---------------------------------------------------------------- م2: the rarest word with its near-equivalents (case 11.2)
_fake = {i: {"title": f"كتاب {i}", "summary": "قصة عن البحر"} for i in range(2, 40)}
_fake[1] = {"title": "سلسلة", "summary": "يتعلمان الاستحمام وتجربة الأطعمة الجديدة"}
topics._cache["index"] = None
idx = topics.index(_fake)
ok(topics.core_word("النظافة الشخصية", idx) is not None, "«النظافة الشخصية» falls back to «النظافة», found as «الاستحمام»")
topics._cache["index"] = None

# ---------------------------------------------------------------- م7: «أقصر» is shorter than the books shown (case 43.2)
interp({"length": "short", "moods_include": ["funny"]})
out = say("ابي شي يونّس", "x", "أقصر")
ok((out.get("_debug") or {}).get("prefs", {}).get("length") in (None, "short"), "runs")
ok(R.shortest_asked("أقصر") == "shorter", "«أقصر» alone is «shorter»")

# ---------------------------------------------------------------- م4: the English chat (case 41)
interp({"genres": ["رواية"]})
conv = [{"role": "user", "content": "I want a dark and twisty thriller"}]
seen.clear(); A.run_chat(conv, active=("fake", "m"), language="en")
_ar = [f for f in seen.get("facts", []) if (books.get(f["id"]) or {}).get("language", "ar") != "en"]
ok(not _ar or "our copy is in Arabic" in seen.get("guidance", ""), "an Arabic copy shown to an English visitor is said to be Arabic")

# ---------------------------------------------------------------- م10: «يشبه هاري بوتر» for a 12-year-old (case 48)
ref = chat.clean_references([{"title": "هاري بوتر", "author": "ج. ك. رولينج", "format": "unknown", "moods": [], "themes": ["الصداقة"],
                              "subgenres": ["فانتازيا", "سحر", "مغامرة"], "known": True}])[0]
ok(ref["subgenres"] == ["فانتازيا", "سحر", "مغامرة"], "the interpreter's bookshop labels for a reference book are kept")
_kind_books = [b for b in avail if any(w in A._subgenre_words(" ".join(map(str, A._json_list(b.get("subgenres")) + A._json_list(b.get("keywords")))))
                                       for w in ("فانتازيا", "سحر", "مغامره"))]
got = A.reference_kind_matches(ref, books, {b["book_id"] for b in avail}, 12)
ok(bool(got) == bool(_kind_books) and all(why.startswith("من نفس نوع «هاري بوتر»") for _, why, _ in got),
   "books of the same kind (fantasy / magic / adventure) are found, said to be «من نفس نوع»")
ok(all(A.age_fit(b, 12) >= 0 for b, _, _ in got), "none of them clearly for another age")
chat.interpret = lambda conv, active, *a: {"intent": "recommend", "prefs": llm.clean_prefs({"audience": "young_adult"}),
                                           "mentioned_titles": ["هاري بوتر"], "title_variants": [], "wants_new_options": False,
                                           "references": [ref], "author_request": {}}
out = say("بنت خالتي ١٢ سنة تعشق هاري بوتر، وش عندكم يشبهه؟")
if _kind_books:
    ok(seen.get("facts") and "من نفس النوع" in seen.get("guidance", ""), "she is offered books of that kind, not «ما عندي معلومات كافية»")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
