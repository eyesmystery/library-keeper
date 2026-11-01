"""[CHATFIX30b] Ibrahim's answers on the inbox review of 2026-10-30: (1) «كتب عن أفغانستان» — «واضح وتقترح»: books ABOUT a subject
are suggested at once; (2) «ممتاز خلها تساير السائل باسلوبه»: a visitor who writes فصحى is answered in فصحى, a dialect one keeps
the dialect; (3) «التلوين» shows the colouring books (children's activity books); (4) «قوة الآن» (self-help, missing): no memoir of
illness as «close to it»; (5) the visitor's books carry goodreads_url. Checks without the model (its answers are faked)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm as L, chat as C, app as A, chat_rules as CR
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()

# --- (1) books about a subject -------------------------------------------------------------------------------------------
for text, want in (("كتب عن أفغانستان", False), ("كتاب عن أفغانستان", False), ("عندكم كتب عن أفغانستان؟", False),
                   ("أبغى كتب عن أفغانستان", False), ("روايات عن الحرب", False),
                   ("أفغانستان", True), ("فلسفة", True), ("السعادة", True), ("جبر", True), ("كتب فلسفة", True)):
    ok(CR.general_first_message(text) == want, f"(1) «{text}» {'is' if want else 'is not'} a general first message")
ok(CR.books_about_asked("عندكم كتب عن أفغانستان؟") and not CR.books_about_asked("أفغانستان"), "(1) books_about_asked")

# --- (2) register --------------------------------------------------------------------------------------------------------
for text, want in (("هل لديكم كتب عن الفلسفة اليونانية؟", "fusha"), ("أريد كتابًا أقصر", "fusha"), ("ما هي أفضل رواية لديكم؟", "fusha"),
                   ("أبحث عن رواية تاريخية", "fusha"), ("وش عندكم؟", "dialect"), ("ابي كتاب اقصر", "dialect"),
                   ("ابغى شي خفيف عشان السفر", "dialect"), ("عندكم كتب عن الفلسفة اليونانية؟", None), ("شكرا", None),
                   ("hello", None)):
    ok(CR.message_register(text) == want, f"(2) «{text}» -> {want}")
ok(CR.visitor_register(["هل لديكم كتب عن الفلسفة؟", "شكرا"]) == "fusha", "(2) the latest message with a signal decides (fusha)")
ok(CR.visitor_register(["هل لديكم كتب عن الفلسفة؟", "ابي شي اقصر"]) == "dialect", "(2) … the visitor switched to dialect")
ok(CR.visitor_register(["شكرا"]) == "dialect" and CR.visitor_register([]) == "dialect", "(2) no signal -> the default dialect")
for dia, fus in (("ما عندنا الآن كتب لـهنري ترويا. تبغى أقترح لك كتبًا قريبة من أسلوب كتاباته؟",
                  "ليس لدينا الآن كتب لـهنري ترويا. هل تريد أن أقترح عليك كتبًا قريبة من أسلوب كتاباته؟"),
                 ("«قوة الآن» غير متوفرة عندنا حاليًا. تبغى أقترح لك شيئًا قريبًا منها؟",
                  "«قوة الآن» غير متوفرة لدينا حاليًا. هل تريد أن أقترح عليك شيئًا قريبًا منها؟"),
                 ("ما لقيت عندنا مؤلفًا بهذا الاسم. تبغى أقترح لك بدائل؟", "لم أجد لدينا مؤلفًا بهذا الاسم. هل تريد أن أقترح عليك بدائل؟"),
                 ("تبغى كتب تلوين للكبار ولا للأطفال؟", "هل تريد كتب تلوين للكبار أم للأطفال؟"),
                 ("وش اللي يعجبك فيه؟", "ما الذي يعجبك فيه؟"), ("تحب تقرأ شي جاد؟", "هل تحب أن تقرأ شيء جاد؟")):
    ok(CR.fusha_rewrite(dia) == fus, f"(2) «{dia}» -> «{CR.fusha_rewrite(dia)}»")
ok(CR.fusha_rewrite("ولا تقل هذا.") == "ولا تقل هذا." and CR.fusha_rewrite("Hello there") == "Hello there",
   "(2) «ولا» outside a question and English text are left alone")
ok(CR.fusha_rewrite("عندكمو") == "عندكمو" and CR.fusha_rewrite("وعندنا كتب") == "ولدينا كتب", "(2) whole words only, «و» before them kept")
ok("خلها تساير السائل باسلوبه" in src and "chat_rules.fusha_rewrite(res[\"reply\"]" in src, "(2) the rewrite runs on every reply")

# --- (3) activity books ----------------------------------------------------------------------------------------------------
for text, want in (("التلوين", True), ("كتب تلوين", True), ("ابي كتاب تلوين لبنتي", True), ("كتاب ملصقات لولدي", True),
                   ("تلوين للكبار", False), ("كتب عن الفن", False), ("رواية عن رسام", False)):
    ok(CR.activity_wish(text) == want, f"(3) «{text}» {'is' if want else 'is not'} a children's activity book")

# --- (4) self-help / spirituality --------------------------------------------------------------------------------------------
ok(CR.reference_form({}, {"genres": ["Self Help", "Nonfiction", "Spirituality", "Philosophy"]}) == "selfhelp", "(4) «قوة الآن» tags")
ok(CR.reference_form({"format": "self_dev"}) == "selfhelp" and CR.reference_form({"format": "essays"}, {"genres": ["Nonfiction", "Essays"]}) is False
   and CR.reference_form({"format": "novel"}) is True, "(4) reference_form")
ok(not CR.same_form_as("selfhelp", {"genre": "سيرة ذاتية"}) and not CR.same_form_as("selfhelp", {"genre": "رواية"})
   and CR.same_form_as("selfhelp", {"genre": "تطوير ذاتي"}) and CR.same_form_as("selfhelp", {"genre": "علم نفس"}),
   "(4) self-help: self-help / psychology / religion / thought shelves only")

# --- the whole chat with a faked model ------------------------------------------------------------------------------------
def clear():
    for fn in ("clear_cache", "invalidate_cache"):
        if hasattr(A, fn):
            getattr(A, fn)()

books = [dict(b) for b in A.list_books(only_active=False)]
avail = [b for b in books if A.is_available(b)]
con = models.get_connection()
used = set()
def pick(genre=None):
    b = next(b for b in avail if b["book_id"] not in used and (genre is None or b.get("genre") == genre))
    used.add(b["book_id"]); return b["book_id"]
def setbook(bid, **kw):
    kw.setdefault("quantity", 3); kw.setdefault("active", 1)
    con.execute("UPDATE books SET " + ", ".join(f"{k}=?" for k in kw) + " WHERE book_id=?", list(kw.values()) + [bid])
AFG = pick("رواية")
setbook(AFG, title="أرض ورماد تجربة", author="عتيق رحيمي", genre="رواية", summary="رواية عن الحرب في أفغانستان",
        keywords=json.dumps(["أفغانستان", "حرب"], ensure_ascii=False))
_cards0 = A.get_profile_cards()
COLOR = next(b["book_id"] for b in avail if b.get("genre") == "أطفال" and b["book_id"] not in used
             and A.age_allowed(_cards0.get(b["book_id"]), "children", (), b, None))   # a children's book with no content warning
used.add(COLOR)
setbook(COLOR, title="مجموعة التلوين الكبيرة تجربة", author="دار تجربة", genre="أطفال", age_min=None, age_max=None, summary=None,
        keywords=None, subgenres=None)
PAINT = pick("رواية")
setbook(PAINT, title="الحرب والتربنتين تجربة", genre="رواية", summary="رواية عن رسام وعن التلوين والذكريات",
        keywords=json.dumps(["التلوين", "فن"], ensure_ascii=False))
SELF = pick()
setbook(SELF, title="حرر نفسك تجربة", genre="تطوير ذاتي", summary="عن السكينة والوعي باللحظة")
MEMO = pick()
setbook(MEMO, title="ظلام مرئي تجربة", genre="سيرة ذاتية", summary="مذكرات عن الاكتئاب")
GR = pick()
setbook(GR, title="كتاب جودريدز تجربة", author="مؤلف جودريدز تجربة", goodreads_url="https://www.goodreads.com/book/show/12345.Test")
GR2 = pick()
setbook(GR2, title="كتاب جودريدز ثان تجربة", author="مؤلف جودريدز تجربة", goodreads_url="https://www.goodreads.com/book/show/678.Second")
con.execute("UPDATE books SET quantity=0 WHERE title IN ('قوة الآن') AND book_id NOT IN (?, ?)", (SELF, MEMO))
con.commit(); clear()
A.topics._cache["index"] = None   # the subject index is rebuilt on a size change only

BASE = {"intent": "recommend", "preferences": {}, "mentioned_titles": [], "title_variants": [], "wants_new_options": False,
        "reference_books": [], "author_request": {}, "avoid_topics": [], "order_by": "none", "reader_is_visitor": False,
        "reply_language": "ar", "whole_series": [], "first_publication": False, "publication_years": {"from": 0, "to": 0},
        "reader_age": 0, "physical_wishes": [], "author_countries": [], "about_shown": False, "copy_language": "any",
        "about_raising_child": False, "avoid_author_countries": [], "visitor_female": False}
def raw(**kw):
    r = json.loads(json.dumps(BASE)); r.update(kw); return r

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
    # (1)
    answer.update(ids=None, reply="عندنا «أرض ورماد تجربة» عن الحرب في أفغانستان.")
    out, facts, cards = run(raw(preferences={"unsupported_requests": ["أفغانستان"]}), "كتب عن أفغانستان")
    ok("أول رسالة" not in (seen.get("guidance") or "") and AFG in cards, f"(1) «كتب عن أفغانستان»: books at once ({cards})")
    out, facts, cards = run(raw(preferences={"unsupported_requests": ["أفغانستان"]}), "أفغانستان")
    ok("أول رسالة" in (seen.get("guidance") or "") and cards == [], "(1) «أفغانستان» alone: one question first, no book")

    # (2) a فصحى visitor: guidance, and the model's dialect words rewritten; a dialect visitor untouched
    answer.update(ids=None, reply="إيه، عندنا «أرض ورماد تجربة». تبغى شي أقصر ولا كذا مناسب؟")
    out, facts, cards = run(raw(preferences={"unsupported_requests": ["أفغانستان"]}), "هل لديكم كتب عن أفغانستان؟")
    ok(CR.FUSHA_GUIDANCE in (seen.get("guidance") or ""), "(2) the فصحى guidance reaches the responder")
    ok(out["reply"] == "نعم، لدينا «أرض ورماد تجربة». هل تريد شيء أقصر أم هكذا مناسب؟", f"(2) the reply in فصحى: {out['reply']}")
    out, facts, cards = run(raw(preferences={"unsupported_requests": ["أفغانستان"]}), "ابي كتب عن أفغانستان")
    ok(CR.FUSHA_GUIDANCE not in (seen.get("guidance") or "") and out["reply"].startswith("إيه، عندنا"), "(2) a dialect visitor keeps the dialect")
    out, facts, cards = run(raw(author_request={"name_as_written": "فلان الغائبي", "full_name": "فلان الغائبي",
                                                "aliases": ["Absent Writer"], "mode": "works"}), "هل لديكم كتب فلان الغائبي؟")
    ok(out["reply"].startswith("ليس لدينا الآن كتب لـفلان الغائبي. هل تريد أن أقترح عليك"), f"(2) a fixed reply in فصحى: {out['reply']}")

    # (3) colouring books
    answer.update(ids=None, reply="عندنا «مجموعة التلوين الكبيرة تجربة».")
    out, facts, cards = run(raw(preferences={"unsupported_requests": ["التلوين"]}), "التلوين")
    ok(COLOR in facts and PAINT not in facts, f"(3) «التلوين»: the colouring book, not the painter's novel ({facts})")
    ok(COLOR in cards and "أول رسالة" not in (seen.get("guidance") or ""), f"(3) shown at once ({cards})")
    out, facts, cards = run(raw(preferences={"unsupported_requests": ["تلوين"], "audience": "children"}), "ابي كتاب تلوين لبنتي")
    ok(not out["reply"].startswith("كم عمر") and COLOR in cards, f"(3) «لبنتي»: no age question first, the book shown ({out['reply']}, {cards})")
    out, facts, cards = run(raw(preferences={"unsupported_requests": ["التلوين"]}), "كتب تلوين للكبار")
    ok((out.get("_debug") or {}).get("prefs", {}).get("audience") != "children", "(3) «للكبار»: the reader is not made a child")

    # (4) «قوة الآن» missing: self-help likeness, never a memoir
    A.outside_books.lookup = lambda title, *a, **k: {"status": "found", "title": "سكينة اللحظة الغائبة", "author": "Eckhart Tolle",
                                                     "genres": ["Self Help", "Nonfiction", "Spirituality", "Psychology"], "subjects": [],
                                                     "description": "دليل إلى التنوير الروحي والعيش في اللحظة الحاضرة", "sources": []}
    A.embeddings.similar_to = lambda text, near, top=4: [(i, 0.9) for i in (MEMO, SELF) if i in near]
    answer.update(ids=[MEMO, SELF], reply="«قوة الآن» غير متوفر. عندك «ظلام مرئي تجربة» و«حرر نفسك تجربة».")
    out, facts, cards = run(raw(intent="about_book", mentioned_titles=["سكينة اللحظة الغائبة"],
                                reference_books=[{"title": "سكينة اللحظة الغائبة", "author": "إيكهارت تول", "format": "self_dev", "moods": [],
                                                  "themes": [], "subgenres": [], "known": True, "relation": "same_book"}]),
                            "سكينة اللحظة الغائبة")
    ok(SELF in facts and MEMO not in facts, f"(4) self-help likeness only ({facts})")
    ok(cards == [SELF], f"(4) the memoir gets no card even when the responder asks for it ({cards})")
    A.outside_books.lookup, A.embeddings.similar_to = _lookup, _similar

    # (5) goodreads_url reaches the visitor
    answer.update(ids=None, reply="«كتاب جودريدز تجربة» متوفر.")
    out, facts, cards = run(raw(intent="about_book", mentioned_titles=["كتاب جودريدز تجربة"]), "عندكم كتاب جودريدز تجربة؟")
    gb = next((b for b in out["books"] if b["book_id"] == GR), None)
    ok(gb and gb.get("goodreads_url") == "https://www.goodreads.com/book/show/12345.Test", f"(5) a chat result book carries goodreads_url ({gb and gb.get('goodreads_url')})")
finally:
    C.interpret, C.respond, L.complete_json = _interp, _resp, _orig_cj
    A.open_questions.record, A.outside_books.lookup, A.embeddings.similar_to, A.second_look.readings = _rec, _lookup, _similar, _readings

client = A.app.test_client()
r = client.get(f"/api/more?by=author&book_id={GR}").get_json()
ok(any(b.get("goodreads_url") == "https://www.goodreads.com/book/show/678.Second" for b in r.get("books") or []),
   "(5) «more by this author» carries goodreads_url")
r = client.get("/api/books").get_json()
ok(any(b.get("book_id") == GR and b.get("goodreads_url") for b in r), "(5) /api/books carries goodreads_url")
_pb = A._pick_book(next(dict(b) for b in A.list_books(only_active=False) if b["book_id"] == GR))
ok(_pb.get("goodreads_url") == "https://www.goodreads.com/book/show/12345.Test", "(5) the picks' book carries goodreads_url")
r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "كتاب جودريدز تجربة"}]}).get_json()
ok(all("goodreads_url" in b for b in r.get("books") or []), f"(5) /api/chat (keyword path) books keep the field ({len(r.get('books') or [])} books)")

con.close()
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
