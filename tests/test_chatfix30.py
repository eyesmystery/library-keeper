"""[CHATFIX30] The library's inbox of 2026-10-29 13:24 (§8.1, §8.2, §3) and Ibrahim's request of 2026-10-30: (1) «تشريح الفيلم»
missing — anatomy books chosen by the word «تشريح», «nothing close» said with books shown, logged as a wish; (2) «سجون نختار أن
نحيا فيها» (essays) answered with two novels; (3) «روي ياكوبسون» -> «تقصد «روي ياكوبسن»؟», a wasted turn; (4) «كتاب زمان
الملائكه» logged as a subject not found while book 1412 was the answer; (5) a general first message («فلسفة»، «السعادة») got
books at once — Ibrahim: «ما يقترح على طول من اول سؤال اذا كان السؤال ما يوضح الاقتراح». Checks without the model (the
interpreter's and the responder's answers are faked)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm as L, chat as C, app as A, chat_rules as CR, author_flow as AF
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()

# --- helpers --------------------------------------------------------------------------------------------------------------
# (4)/(1) a phrase that is a title named
for phrase, titles, want in (("كتاب زمان الملائكه", ["زمان الملائكة"], True), ("زمان الملائكه", ["زمان الملائكة: رواية"], True),
                             ("كتاب تشريح الفيلم", ["تشريح الفيلم"], True), ("ابحث عن كتاب تشريح الفيلم", ["تشريح الفيلم"], True),
                             ("رواية الحرب والسلام", ["الحرب والسلام"], True), ("الحرب", ["الحرب والسلام"], False),
                             ("السعادة", ["في السعادة: رحلة فلسفية"], False), ("تشريح", ["تشريح الفيلم"], False), ("", ["x"], False)):
    ok(CR.phrase_is_title(phrase, titles) == want, f"(4) «{phrase}» {'is' if want else 'is not'} the title {titles}")

# (2) fiction / non-fiction
ok(CR.reference_is_fiction({"format": "essays"}) is False, "(2) a book of essays is non-fiction")
ok(CR.reference_is_fiction({"format": None}, {"genres": ["Nonfiction", "Essays", "Psychology"]}) is False, "(2) outside «Nonfiction» decides")
ok(CR.reference_is_fiction({"format": "thought"}, {"genres": ["Fiction", "Classics"]}) is True, "(2) outside «Fiction» wins over a guessed form")
ok(CR.reference_is_fiction({"format": "novel"}) is True, "(2) a novel is fiction")
ok(CR.reference_is_fiction({"format": None, "subgenres": ["رواية تاريخية"]}) is True, "(2) a novel by its subgenre")
ok(CR.reference_is_fiction({}) is None, "(2) nothing known -> None")
ok(CR.reference_is_fiction({"format": "arts"}, {"genres": ["Film", "Textbooks"]}) is False, "(1) a film book is non-fiction")
ok({"سينما", "افلام", "فيلم"} <= CR.outside_subject_words({"genres": ["Film", "Nonfiction", "Textbooks"]})
   and not CR.outside_subject_words({"genres": ["Nonfiction", "Textbooks"]}), "(1) the outside tag «Film» gives the film words")
ok(CR.reference_is_fiction({}, {"genres": ["Film", "Nonfiction"]}) is False, "(1) «Film, Nonfiction» is non-fiction")
ok(CR.book_is_fiction({"genre": "رواية"}) is True and CR.book_is_fiction({"genre": "فكر"}) is False
   and CR.book_is_fiction({"genre": "أطفال"}) is None and CR.book_is_fiction({"genre": ""}) is None, "(2) our shelves")
ok(not CR.same_form_as(False, {"genre": "رواية"}) and CR.same_form_as(False, {"genre": "علم نفس"})
   and CR.same_form_as(None, {"genre": "رواية"}) and CR.same_form_as(True, {"genre": ""}), "(2) same_form_as")

# (5) general first message
for text, want in (("فلسفة", True), ("السعادة", True), ("وش عندكم؟", True), ("مرحبا، ابغى اقرا شي", True), ("كتب فلسفة", True),
                   ("فلسفة؟", True), ("تاريخ", True),
                   # left to the model's own «clarify» (OWN1): a form asked for, a bare «ابي كتاب», a question of his own
                   ("رشحي لي رواية", False), ("ابي رواية", False), ("رواية", False), ("ابي كتاب", False), ("I want to start reading", False),
                   ("وش الفلسفة؟", False),
                   ("رشحي لي رواية قصيرة عن الحرب", False), ("رواية رعب", False), ("ابحث عن رواية تاريخية", False),
                   ("ابي كتاب عن السعادة", False), ("فاجئيني بكتاب", False), ("روايات خليجية", False),
                   ("وش الفرق بين الفلسفة وعلم النفس؟", False)):
    ok(CR.general_first_message(text) == want, f"(5) «{text}» {'is' if want else 'is not'} a general first message")
for text, want in (("ليش نقرأ الفلسفة؟", True), ("وش الفرق بين الفلسفة وعلم النفس؟", True), ("عندكم روايات رعب؟", False),
                   ("ابي رواية رعب", False), ("وش عندكم؟", False)):
    ok(CR.subject_question(text) == want, f"(5) «{text}» {'is' if want else 'is not'} a question of his own about a subject")

# (3b) a bare person's name (follow-up of 2026-10-30)
_fn = {"هنري", "روي", "علي", "نجيب"}
for text, want in (("هنري ترويا", True), ("روي ياكوبسن", True), ("علي عزت بيغوفيتش", True), ("نجيب محفوظ", True),
                   ("فلسفة", False), ("السعادة", False), ("أفغانستان", False), ("جبر", False), ("سرد بسيط", False),
                   ("تقييم مرتفع", False), ("مرحبا منار", False), ("رواية رعب", False), ("هنري ترويا؟", True),
                   ("علي رواية", False)):
    ok(CR.looks_like_name(text, _fn) == want, f"(3b) «{text}» {'looks' if want else 'does not look'} like a name")
for text in ("فلسفة", "السعادة", "أفغانستان", "جبر"):
    ok(CR.general_first_message(text), f"(5) «{text}» stays a general first message")
ok(not CR.general_first_message("هنري ترويا") and not CR.general_first_message("علي عزت بيغوفيتش"), "(3b) a name is not general")

# (3) the same name in two spellings
ok(AF.same_name_spelling("روي ياكوبسون", "روي ياكوبسن"), "(3) «روي ياكوبسون» = «روي ياكوبسن»")
ok(not AF.same_name_spelling("روي ياكوبسن", "روي ياكوبسن"), "(3) the very same spelling is no respelling")
ok(not AF.same_name_spelling("ياكوبسون", "روي ياكوبسن"), "(3) one word vs a full name: another question")
ok(not AF.same_name_spelling("روي ياكوبسون", "يورغن فرانتز ياكوبسن"), "(3) another person")
books = [dict(b) for b in A.list_books(only_active=False)]
req = {"name_as_written": "روي ياكوبسون", "full_name": "روي ياكوبسن", "aliases": [], "mode": "works"}
conv = [{"role": "user", "content": "عندكم كتب روي ياكوبسون؟"}]
r = AF.resolve(req, conv, books)
ok(r[0] is None and r[2] is True and not r[1], f"(3) no «تقصد …؟»: answered at once as an author with no book here ({r[0]})")
ok(req.get("spelling_of") == "روي ياكوبسون" and req.get("name_as_written") == "روي ياكوبسن", "(3) the request carries the spelling")
req3 = {"name_as_written": "روي ياكوبسون", "full_name": "Roy Jacobsen", "aliases": [], "mode": "works"}
r3 = AF.resolve(req3, conv, books)
ok(r3[0] is None and r3[2] is True and req3.get("full_name") == "روي ياكوبسون" and "Roy Jacobsen" in req3.get("aliases"),
   f"(3) the model's Latin spelling («تقصد «Roy Jacobsen»؟» in the replay): no question, named as the visitor wrote him ({r3[0]}, {req3})")
req2 = {"name_as_written": "ياكوبسون", "full_name": "روي ياكوبسن", "aliases": [], "mode": "works"}
r2 = AF.resolve(req2, conv, books)
ok(bool(r2[0]) and "تقصد" in r2[0], f"(3) a one-word name still gets its question ({r2[0]})")

# --- the whole chat with a faked model ------------------------------------------------------------------------------------
def clear():
    for fn in ("clear_cache", "invalidate_cache"):
        if hasattr(A, fn):
            getattr(A, fn)()

avail = [b for b in books if A.is_available(b)]
con = models.get_connection()
def setbook(bid, **kw):
    kw.setdefault("quantity", 3); kw.setdefault("active", 1)
    con.execute("UPDATE books SET " + ", ".join(f"{k}=?" for k in kw) + " WHERE book_id=?", list(kw.values()) + [bid])
used = set()
def pick(genre=None):
    b = next(b for b in avail if b["book_id"] not in used and (genre is None or b.get("genre") == genre))
    used.add(b["book_id"]); return b["book_id"]
ZAMAN = pick()
setbook(ZAMAN, title="زمان الملائكة", author="آيريس مردوخ")
ANAT = pick()
setbook(ANAT, title="تشريح الجسد البشري المذهل", genre="علوم", summary="مدخل إلى تشريح الجسد ووظائف أعضائه", keywords=json.dumps(["تشريح"], ensure_ascii=False))
NOVEL = pick("رواية")
setbook(NOVEL, title="ربيع قرطبة تجربة", author="حسن أوريد", genre="رواية")
ESSAY = pick()
setbook(ESSAY, title="العقل الجمعي تجربة", author="كاتب فكر تجربة", genre="فكر", summary="مقالات في علم النفس الاجتماعي والتفكير الجمعي")
CINE = pick()
setbook(CINE, title="شغف السينما تجربة", genre="فنون وسينما", keywords=json.dumps(["سينما", "أفلام"], ensure_ascii=False))
CRAFT = pick()
setbook(CRAFT, title="غرزة الفلاحي تجربة", genre="فنون وسينما", keywords=json.dumps(["تطريز"], ensure_ascii=False))
con.execute("UPDATE books SET quantity=0 WHERE title IN ('تشريح الفيلم', 'سجون نختار أن نحيا فيها') AND book_id NOT IN (?, ?, ?, ?)",
            (ZAMAN, ANAT, NOVEL, ESSAY))
con.commit(); clear()

BASE = {"intent": "recommend", "preferences": {}, "mentioned_titles": [], "title_variants": [], "wants_new_options": False,
        "reference_books": [], "author_request": {}, "avoid_topics": [], "order_by": "none", "reader_is_visitor": False,
        "reply_language": "ar", "whole_series": [], "first_publication": False, "publication_years": {"from": 0, "to": 0},
        "reader_age": 0, "physical_wishes": [], "author_countries": [], "about_shown": False, "copy_language": "any",
        "about_raising_child": False, "avoid_author_countries": [], "visitor_female": False}
def raw(**kw):
    r = json.loads(json.dumps(BASE)); r.update(kw); return r
def ref(title, author="", fmt="unknown", known=False, relation="same_book", themes=()):
    return {"title": title, "author": author, "format": fmt, "moods": [], "themes": list(themes), "subgenres": [],
            "known": known, "relation": relation}

_orig_cj, _interp, _resp = L.complete_json, C.interpret, C.respond
_rec, _lookup, _similar, _readings = A.open_questions.record, A.outside_books.lookup, A.embeddings.similar_to, A.second_look.readings
seen, logged, answer = {}, [], {"ids": None, "reply": "رد تجربة."}
def _fake_resp(conv, facts, guidance, active, *a, **k):
    seen["facts"], seen["guidance"] = json.loads(facts), guidance
    ids = answer["ids"] if answer["ids"] is not None else [f["id"] for f in seen["facts"]][:4]
    return {"reply": answer["reply"], "show_book_ids": ids, "suggestions": [], "unanswered": "", "excluded_book_ids": []}
def run(r, messages):
    try:
        L.complete_json = lambda *a, **k: json.loads(json.dumps(r))
        it = _interp([{"role": "user", "content": "x"}], active=("fake", "fake"))
    finally:
        L.complete_json = _orig_cj
    C.interpret = lambda conv, active, *a, **k: json.loads(json.dumps(it, default=list))
    C.respond = _fake_resp
    seen.clear(); logged.clear()
    msgs = [{"role": "user", "content": messages}] if isinstance(messages, str) else messages
    out = A.run_chat(msgs, active=("fake", "m"))
    return out, [f["id"] for f in seen.get("facts") or []], [b["book_id"] for b in out["books"]]

A.open_questions.record = lambda kind, phrase, *a, **k: logged.append((kind, phrase))
A.second_look.readings = lambda *a, **k: []
try:
    # (4) «ايش تعرف عن كتاب زمان الملائكة؟» — answered about the book, nothing logged as a subject or a wish
    answer.update(ids=None, reply="«زمان الملائكة» رواية لآيريس مردوخ.")
    out, facts, cards = run(raw(intent="about_book", mentioned_titles=["زمان الملائكة"],
                                preferences={"title_keywords": ["زمان الملائكة"], "unsupported_requests": ["كتاب زمان الملائكه"]}),
                            "ايش تعرف عن كتاب زمان الملائكة؟")

    ok(ZAMAN in cards, f"(4) the book asked about is shown ({cards})")
    ok(not [x for x in logged if x[0] in ("topic", "wish")], f"(4) «كتاب زمان الملائكه» is not logged as a subject or a wish ({logged})")
    ok(not any(i != ZAMAN and "زمان" in A.normalize(next((b["title"] for b in books if b["book_id"] == i), "")) for i in facts)
       or ZAMAN in facts, "(4) no book is put forward for the word «زمان» of the title")

    # (1) «ابحث عن كتاب تشريح الفيلم» — read as a wish with the title as its subject and nothing known about the book
    answer.update(ids=[ANAT], reply="«تشريح الفيلم» غير متوفر عندنا. عندك «تشريح الجسد البشري المذهل».")
    out, facts, cards = run(raw(intent="recommend", mentioned_titles=["تشريح الفيلم"], reference_books=[ref("تشريح الفيلم")],
                                preferences={"title_keywords": ["تشريح الفيلم"], "unsupported_requests": ["كتاب تشريح الفيلم"]}),
                            "ابحث عن كتاب تشريح الفيلم")
    dbg = out.get("_debug") or {}
    ok(dbg.get("intent") == "about_book" and (dbg.get("reference") or {}).get("missing_title"),
       f"(1) a wish whose only subject is the title asked for is a missing book ({dbg.get('intent')})")
    ok(ANAT not in facts, f"(1) no anatomy book chosen by the word «تشريح» ({facts})")
    ok(cards == [], f"(1) nothing close found: no card, whatever the responder asks for ({cards})")
    ok(("missing_book", "تشريح الفيلم") in logged and not [x for x in logged if x[0] in ("wish", "topic")],
       f"(1) logged as a missing book only ({logged})")
    ok("لا تعرض أي كتاب" in (seen.get("guidance") or ""), "(1) the guidance says to show no book and ask")

    # (1b) the same book found outside, tagged "Film", unknown to the model: our film books by their subject, not «تشريح»
    A.outside_books.lookup = lambda title, *a, **k: {"status": "found", "title": "تشريح الفيلم", "author": "Bernard F. Dick",
                                                     "genres": ["Film", "Nonfiction", "Textbooks"], "subjects": [],
                                                     "description": "مفردات اللغة السينمائية", "sources": []}
    A.embeddings.similar_to = lambda text, near, top=4: []
    answer.update(ids=None, reply="«تشريح الفيلم» غير متوفر عندنا. عندك «شغف السينما تجربة».")
    out, facts, cards = run(raw(intent="about_book", mentioned_titles=["تشريح الفيلم"], reference_books=[ref("تشريح الفيلم")]),
                            "تشريح الفيلم")
    ok(CINE in facts and ANAT not in facts and CRAFT not in facts,
       f"(1b) the film book by its keywords; not the anatomy book, not a book that only shares the shelf «فنون وسينما» ({facts})")
    ok(cards == [CINE], f"(1b) its card is shown ({cards})")
    A.outside_books.lookup, A.embeddings.similar_to = _lookup, _similar

    # (2) «عندك كتاب سجون نختار ان نحيا فيها» (essays, missing): the likeness search keeps to non-fiction
    A.outside_books.lookup = lambda title, *a, **k: {"status": "found", "title": "سجون نختار أن نحيا فيها", "author": "دوريس ليسينغ",
                                                     "genres": ["Nonfiction", "Essays", "Psychology"], "subjects": [],
                                                     "description": "مقالات عن علم النفس الاجتماعي وتفكير الجماعات", "sources": []}
    A.embeddings.similar_to = lambda text, near, top=4: [(i, 0.9) for i in (NOVEL, ESSAY) if i in near]
    answer.update(ids=[NOVEL, ESSAY], reply="«سجون نختار أن نحيا فيها» غير متوفر. عندك «ربيع قرطبة تجربة» و«العقل الجمعي تجربة».")
    out, facts, cards = run(raw(intent="about_book", mentioned_titles=["سجون نختار أن نحيا فيها"],
                                reference_books=[ref("سجون نختار أن نحيا فيها", "دوريس ليسينغ", "essays", True)]),
                            "عندك كتاب سجون نختار ان نحيا فيها")
    ok(ESSAY in facts and NOVEL not in facts, f"(2) a book of essays: the non-fiction close to it, never a novel ({facts})")
    ok(cards == [ESSAY], f"(2) the novel gets no card even when the responder asks for it ({cards})")
    # … and a novel asked for gets novels
    A.outside_books.lookup = lambda title, *a, **k: {"status": "found", "title": "ظلال الأندلس البعيدة", "author": "كاتب غائب",
                                                     "genres": ["Fiction", "Historical Fiction"], "subjects": [], "description": "رواية تاريخية عن الأندلس", "sources": []}
    answer.update(ids=None, reply="رد تجربة.")
    out, facts, cards = run(raw(intent="about_book", mentioned_titles=["ظلال الأندلس البعيدة"],
                                reference_books=[ref("ظلال الأندلس البعيدة", "كاتب غائب", "novel", True)]), "عندكم ظلال الأندلس البعيدة؟")
    ok(NOVEL in facts and ESSAY not in facts, f"(2) a novel missing: novels close to it, not essays ({facts})")
    A.outside_books.lookup, A.embeddings.similar_to = _lookup, _similar

    # (3) «عندكم كتب روي ياكوبسون؟» — the answer at once, logged as a missing author
    answer.update(ids=None, reply="رد تجربة.")
    out, facts, cards = run(raw(intent="recommend", author_request={"name_as_written": "روي ياكوبسون", "full_name": "روي ياكوبسن",
                                                                    "aliases": [], "mode": "works"}), "عندكم كتب روي ياكوبسون؟")
    ok("تقصد" not in out["reply"] and out["reply"].startswith("ما عندنا الآن كتب لـروي ياكوبسن"), f"(3) {out['reply']}")
    ok(("missing_author", "روي ياكوبسن") in logged, f"(3) logged as a missing author ({logged})")

    # (3b) follow-up (MGR replay 2026-10-30): a bare author's name as the first message, read by the model as «clarify» with no
    # author request («تقصد تبغى كتابًا لهنري ترويا؟ وش النوع…»), goes through the author flow
    _c = models.get_connection()
    _c.execute("INSERT INTO authors (author_id, name_ar, aliases) VALUES (900301, 'هنري تجربة الأول', '[]')")
    _c.execute("INSERT INTO authors (author_id, name_ar, aliases) VALUES (900302, 'روي تجربة الثاني', '[]')")
    _c.execute("INSERT INTO authors (author_id, name_ar, aliases) VALUES (900303, 'يورغن فرانتز ياكوبسن', '[]')")
    _jb = pick()
    _c.execute("UPDATE books SET author='يورغن فرانتز ياكوبسن', title='باربرا تجربة', quantity=3, active=1 WHERE book_id=?", (_jb,))
    _c.commit(); _c.close(); clear()
    A._first_names_cache["n"] = None
    answer.update(ids=None, reply="تقصد تبغى كتابًا لهنري ترويا؟ وش النوع اللي يميل له مزاجك؟")
    out, facts, cards = run(raw(intent="clarify"), "هنري ترويا")
    ok(out["reply"].startswith("ما عندنا الآن كتب لـهنري ترويا") and ("missing_author", "هنري ترويا") in logged,
       f"(3b) «هنري ترويا» alone: «ما عندنا الآن كتب لـهنري ترويا…», logged as a missing author ({out['reply']}, {logged})")
    out, facts, cards = run(raw(intent="clarify"), "روي ياكوبسن")
    ok("يورغن فرانتز ياكوبسن" in out["reply"] and "تقصد" in out["reply"] and "وش النوع" not in out["reply"],
       f"(3b) «روي ياكوبسن» alone: the catalog author of that family name is offered, as before CHATFIX30 ({out['reply']})")
    out, facts, cards = run(raw(intent="clarify"), "سرد بسيط")
    ok(not (out.get("_debug") or {}).get("author_gate") and not [x for x in logged if x[0] == "missing_author"],
       "(3b) «سرد بسيط» is no name")

    # (5) a general first message: one question, no book; the facts the responder sees are empty
    answer.update(ids=None, reply="تبغى مدخلًا سهلًا للفلسفة ولا فيلسوفًا معيّنًا؟")
    out, facts, cards = run(raw(intent="recommend", preferences={"genres": ["فكر"], "unsupported_requests": ["فلسفة"]}), "فلسفة")
    ok(cards == [] and facts == [], f"(5) «فلسفة» first: no book ({cards}, {len(facts)} facts)")
    ok("أول رسالة" in (seen.get("guidance") or "") and "فلسفه" in (seen.get("guidance") or ""), "(5) the guidance asks one question about it")
    ok(not logged, f"(5) nothing logged for a turn that searched nothing ({logged})")
    out, facts, cards = run(raw(intent="recommend", preferences={"unsupported_requests": ["السعادة"]}), "السعادة")
    ok(cards == [] and facts == [], "(5) «السعادة» first: no book")
    out, facts, cards = run(raw(intent="clarify"), "وش عندكم؟")
    ok(cards == [], "(5) «وش عندكم؟»: no book")
    # a clear request keeps its books; so does the same word later in a conversation
    out, facts, cards = run(raw(intent="recommend", preferences={"genres": ["رواية"], "length": "short", "unsupported_requests": ["الحرب"]}),
                            "رشحي لي رواية قصيرة عن الحرب")
    ok((out.get("_debug") or {}).get("intent") == "recommend" and "أول رسالة" not in (seen.get("guidance") or ""),
       f"(5) «رشحي لي رواية قصيرة عن الحرب»: suggested at once ({(out.get('_debug') or {}).get('intent')})")
    out, facts, cards = run(raw(intent="recommend", preferences={"genres": ["فكر"], "unsupported_requests": ["فلسفة"]}),
                            [{"role": "user", "content": "مرحبا"}, {"role": "assistant", "content": "هلا! وش تحب تقرا؟"},
                             {"role": "user", "content": "فلسفة"}])
    ok("أول رسالة" not in (seen.get("guidance") or "") and facts, "(5) «فلسفة» as an answer to Manar's question: books")
    out, facts, cards = run(raw(intent="recommend", preferences={"genres": ["فكر"], "unsupported_requests": ["الفلسفة"]}),
                            "ليش نقرأ الفلسفة؟ وش فايدتها؟")
    ok("أجب عن سؤاله نفسه أولًا" in (seen.get("guidance") or ""), "(5) a question of his own is answered first")
finally:
    C.interpret, C.respond, L.complete_json = _interp, _resp, _orig_cj
    A.open_questions.record, A.outside_books.lookup, A.embeddings.similar_to, A.second_look.readings = _rec, _lookup, _similar, _readings

ok("CHATFIX30" in src and "_q30_general" in src and "_ref_suitable" in src, "the rules are in the chat")
ok('or bool(re.search(r"[a-z]", said)) != bool(re.search(r"[a-z]", real)))' in src,
   "(1) «برنارد ف. ديك» / “Bernard F. Dick”: an author in two scripts does not drop the model's themes of the missing book")
con.close()
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
