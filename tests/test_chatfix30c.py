"""[CHATFIX30c] Ibrahim «ابدا الاصلاح كله» (2026-10-30) after the review of the day's real chats on 8802: (1) ratings only from data
(books.goodreads_rating): a «high rating» wish orders by it, a prize is never a rating, an unsourced «تقييم عالي» is taken out;
(2) «فاجئيني» brings books at once and its «جديدة» does not carry into a new request (reverted by CHATFIX31 (3), Ibrahim 2026-11-01: it stays
until the visitor removes it); (3) «الإسلام في أوروبا» reaches the places
(«باري»، «الأندلس») and not a book about Europe alone; (4) a card book named only inside a readers' impression gets its author;
(5) «ليه» and other dialect words; (6) fusha_rewrite never touches a title (AUDIT 2026-10-30 P2, book 1857).
Checks without the model (its answers are faked)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm as L, chat as C, app as A, chat_rules as CR, topics as T
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()

# --- (1) ratings ---------------------------------------------------------------------------------------------------------
for text, want in (("ابغى روايات تاريخية تقييمها عالي", True), ("الأعلى تقييمًا", True), ("كتب تقييمها مرتفع", True),
                   ("تقييم مرتفع", True), ("highly rated novels", True), ("top-rated fantasy", True),
                   ("كتاب عن التقييم التربوي", False), ("رواية عالية الجودة", False)):
    ok(CR.high_rating_wished(text) == want, f"(1) «{text}» {'is' if want else 'is not'} a high-rating wish")
REAL = ("أنصحك بـ«أغالب مجرى النهر» لسعيد خطيبي؛ بعض القرّاء أعطوه تقييمًا عاليًا: فاز بالبوكر العربية 2026. و«يريفان» لجيلبير سينويه؛ "
        "بعض القرّاء وصفوه بأنه شديد عاطفيًا وأجواؤه حزينة. و«قطار إلى سمرقند» لغوزيل ياخينا؛ قراءة شديدة عاطفيًا، وتقييم بعض القرّاء له مرتفع. "
        "تفضّل رواية أجواؤها غامضة، ولا رحلة تاريخية مؤثرة؟")
fixed = CR.strip_unsourced_rating(REAL, [])
ok("تقييم" not in fixed and "فاز بالبوكر العربية 2026" in fixed, f"(1) the real reply: no rating claimed, the prize kept as a prize ({fixed})")
ok("«قطار إلى سمرقند» لغوزيل ياخينا؛ قراءة شديدة عاطفيًا." in fixed and fixed.endswith("مؤثرة؟"), "(1) the rest of the reply is whole")
ok("وتقييم بعض القرّاء له مرتفع" in CR.strip_unsourced_rating(REAL, ["قطار إلى سمرقند"]), "(1) a claim next to a rated book stays")
ok(CR.strip_unsourced_rating("“Dune” is highly rated by readers.", []) == "" or "highly rated" not in CR.strip_unsourced_rating("“Dune” is highly rated by readers. It is long.", []),
   "(1) English claims too")
ok(CR.strip_unsourced_rating("رواية جميلة.", []) == "رواية جميلة.", "(1) a reply with no claim is untouched")

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
R_HI, R_MID, R_FEW, R_NONE = pick("رواية"), pick("رواية"), pick("رواية"), pick("رواية")
setbook(R_HI, title="رواية مقيمة عاليا تجربة", goodreads_rating=4.46, goodreads_ratings_count=1530,
        goodreads_rating_source="https://www.goodreads.com/book/show/1", goodreads_rating_at="2026-10-30")
setbook(R_MID, title="رواية مقيمة وسط تجربة", goodreads_rating=3.71, goodreads_ratings_count=212)
setbook(R_FEW, title="رواية بتقييمات قليلة تجربة", goodreads_rating=4.9, goodreads_ratings_count=3)
setbook(R_NONE, title="رواية بلا تقييم تجربة", goodreads_rating=None, goodreads_ratings_count=None)
con.commit()
_ab = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
f_hi = A.book_fact(_ab[R_HI], None, "x")
f_few = A.book_fact(_ab[R_FEW], None, "x")
ok(f_hi.get("goodreads_rating") == {"average_out_of_5": 4.5, "ratings": 1530, "site": "Goodreads"}, f"(1) the rating reaches the facts ({f_hi.get('goodreads_rating')})")
ok("goodreads_rating" not in f_few, "(1) fewer than 5 ratings: not given (floor 5 since 2026-11-01)")
ok("goodreads_rating (إن وجد)" in C.RESPOND_SYSTEM_AR and "goodreads_rating (when present)" in C.RESPOND_SYSTEM_EN,
   "(1) the responder's rules say how to use it, and that a prize is no rating")

# --- (5) dialect words; (6) titles kept ----------------------------------------------------------------------------------
for text, want in (("ليه ما اقترحتها لي؟", "dialect"), ("وشلون اختار؟", "dialect"), ("تكفى عطني شي", "dialect"),
                   ("ابا رواية", "dialect"), ("ماذا عن رواية باري؟", "fusha"), ("هل تعتبر رواية عن تاريخ الاسلام في اوروبا؟", "fusha")):
    ok(CR.message_register(text) == want, f"(5) «{text}» -> {want}")
ok(CR.visitor_register(["ماذا عن رواية باري؟", "هل تعتبر رواية عن تاريخ الاسلام في اوروبا؟", "ليه ما اقترحتها لي؟"]) == "dialect",
   "(5) the latest clear signal wins (dialect after فصحى)")
T1857 = "سلسلة التَّربية الاجتماعيِّة: خيرا تعمل خيرا تلقى"
ok(CR.fusha_rewrite(f"عندنا «{T1857}» تبغاه؟") == f"لدينا «{T1857}» تريده؟", "(6) book 1857's quoted title is kept verbatim")
ok(CR.fusha_rewrite(f"عندنا {T1857} الحين.", protect=[T1857]) == f"لدينا {T1857} الآن.", "(6) … and unquoted, as a protected title")
ok(CR.fusha_rewrite("عندنا كتاب لحسن زين الدين.", protect=["حسن زين الدين"]) == "لدينا كتاب لحسن زين الدين.", "(6) an author's name is kept")
ok(CR.fusha_rewrite("ما تلقى مثله.") == "ما تجد مثله.", "(6) the swap still works outside titles")

# --- (4) a card book introduced with its author ----------------------------------------------------------------------------
TAM = ("أرشّح لك «الضائعة» لعبد الله الخويطر؛ رواية سعودية من ٨٨ صفحة. أو «عصر الحب» لنجيب محفوظ، ١٦٠ صفحة، وبعض القرّاء "
       "يحسّون «التميمة» قراءة خفيفة عاطفيًا، وهي ١٧٦ صفحة.")
CARDS = [("الضائعة", "عبد الله الخويطر"), ("عصر الحب", "نجيب محفوظ"), ("التميمة", "نبيل فاروق")]
got = CR.introduce_authors(TAM, CARDS)
ok("يحسّون «التميمة» لنبيل فاروق قراءة" in got and got.count("لعبد الله الخويطر") == 1 and got.count("لنجيب محفوظ") == 1,
   f"(4) «التميمة» gets «لنبيل فاروق», the others are untouched ({got})")
ok(CR.introduce_authors("و«التميمة» لنبيل فاروق، وبعض القرّاء يحسّون «التميمة» خفيفة.", CARDS[2:]).count("نبيل فاروق") == 1,
   "(4) an author already named is not added again")
ok(CR.introduce_authors("عندنا «التميمة»، ١٧٦ صفحة.", CARDS[2:]) == "عندنا «التميمة»، ١٧٦ صفحة.", "(4) only a title named inside an impression is fixed")
ok(CR.introduce_authors("وبعض القرّاء يحسّون «الطيب» خفيفة.", [("الطيب", "الطيب صالح")]) in
   ("وبعض القرّاء يحسّون «الطيب» للطيب صالح خفيفة.", "وبعض القرّاء يحسّون «الطيب» خفيفة."), "(4) «لل» before a name with «ال»")

# --- (2) surprise and carried recency --------------------------------------------------------------------------------------
ok(CR.surprise_asked("فاجئيني بكتب جديدة") and CR.surprise_asked("Surprise me") and not CR.surprise_asked("رواية مفاجئة"[:4]),
   "(2) «فاجئيني» / 'surprise me' is a surprise asked for")
# CHATFIX31 (3), Ibrahim 2026-11-01 («على حسب، يمكن يبغى يضيف ويحدد اكثر مو انه يغير الكتاب. المفروض يظل عالسياق الا اذا هو
# الغى السياق او بدأ محادثة جديدة»): «جديدة» now stays through a new subject too, until the visitor removes it (its badge, or
# «مو لازم جديدة») or starts a new chat. The three cases that said None (let go at a new subject) now say "carried"
for msgs, want in ((["فاجئيني بكتب جديدة", "أي شي يفاجئني", "ليش اخترت لي هذه الكتب", "شي في علم النفس", "ابا اجمل روايه رومنسيه"], "carried"),
                   (["فاجئيني بكتب جديدة", "شي في علم النفس"], "carried"),
                   (["فاجئيني بكتب جديدة", "غيرها"], "carried"), (["روايات جديدة", "غيرها"], "carried"),
                   (["روايات جديدة", "اقصر", "غيرها"], "carried"), (["روايات جديدة", "شي ثاني"], "carried"),
                   (["روايات جديدة", "شي في علم النفس", "غيرها"], "carried"), (["وش وصلكم جديد هالشهر؟", "أبي رواية منها"], "carried"),
                   (["روايات جديدة", "هذي مو جديدة"], "said"), (["روايات جديدة"], "said")):
    ok(A.new_release_wished(msgs) == want, f"(2) {msgs[-1]!r} after {msgs[:-1]} -> {want} ({A.new_release_wished(msgs)})")
ok("_opening and chat_rules.surprise_asked(user_messages[0])" in src, "(2) the surprise start button opens no question")

# --- (3) a subject in a region ---------------------------------------------------------------------------------------------
BARI, ANDA, ELEPH, ISLAM_ONLY = pick("رواية"), pick("رواية"), pick("رواية"), pick("رواية")
setbook(BARI, title="باري", genre="رواية", summary="رواية تاريخية عن صراع بين عالمين", keywords=json.dumps(["تاريخ", "حروب"], ensure_ascii=False),
        subjects=None, subgenres=None)
setbook(ANDA, title="أيام قرطبة الأخيرة تجربة", genre="رواية", summary="رواية عن سقوط الأندلس", subjects=json.dumps(["الأندلس"], ensure_ascii=False))
setbook(ELEPH, title="مسيرة الفيل تجربة", genre="رواية", summary="فيل يقطع رحلة طويلة عبر أوروبا", keywords=json.dumps(["أوروبا", "رحلة"], ensure_ascii=False))
setbook(ISLAM_ONLY, title="حكاية في بغداد تجربة", genre="رواية", summary="رواية عن الإسلام في بغداد العباسية", keywords=json.dumps(["الإسلام"], ensure_ascii=False))
con.commit()
for fn in ("clear_cache", "invalidate_cache"):
    if hasattr(A, fn):
        getattr(A, fn)()
T._cache["index"] = None
_ab = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
_ix = T.index(_ab)
for phrases in (["الإسلام", "أوروبا"], ["الإسلام في أوروبا"], ["المسلمين في اوروبا"]):
    got = {k: T.match(phrases, k, _ix)[0] > 0 for k in (BARI, ANDA, ELEPH, ISLAM_ONLY)}
    ok(got == {BARI: True, ANDA: True, ELEPH: False, ISLAM_ONLY: False}, f"(3) {phrases}: «باري» and «الأندلس» yes, Europe alone or Islam alone no ({got})")
ok(T.match(["أوروبا"], ELEPH, _ix)[0] > 0 and T.match(["الإسلام"], ISLAM_ONLY, _ix)[0] > 0, "(3) a single subject is matched as before")
ok(T.split_region(["الإسلام في أوروبا"]) == (["الاسلام"], ["اوروبا"]) and T.split_region(["الحرب"]) == (["الحرب"], []), "(3) split_region")
ok(T.searchable(["الإسلام", "أوروبا"], _ix) == ["الإسلام", "أوروبا"], "(3) the pair is searched together")

# --- the whole chat with a faked model ------------------------------------------------------------------------------------
B1857 = pick()
setbook(B1857, title=T1857, author="فريق تجربة الأطفال")
con.commit()
for fn in ("clear_cache", "invalidate_cache"):
    if hasattr(A, fn):
        getattr(A, fn)()
T._cache["index"] = None

BASE = {"intent": "recommend", "preferences": {}, "mentioned_titles": [], "title_variants": [], "wants_new_options": False,
        "reference_books": [], "author_request": {}, "avoid_topics": [], "order_by": "none", "reader_is_visitor": False,
        "reply_language": "ar", "whole_series": [], "first_publication": False, "publication_years": {"from": 0, "to": 0},
        "reader_age": 0, "physical_wishes": [], "author_countries": [], "about_shown": False, "copy_language": "any",
        "about_raising_child": False, "avoid_author_countries": [], "visitor_female": False}
def raw(**kw):
    r = json.loads(json.dumps(BASE)); r.update(kw); return r
_orig_cj, _interp, _resp = L.complete_json, C.interpret, C.respond
_rec, _readings = A.open_questions.record, A.second_look.readings
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
    # (1) «روايات تقييمها عالي»: the rated books first, the highest first; the rating wish is no subject
    answer.update(ids=None, reply="عندك «رواية بلا تقييم تجربة»، وتقييم بعض القرّاء له مرتفع. و«رواية مقيمة عاليا تجربة» تقييمها عالي.")
    out, facts, cards = run(raw(preferences={"genres": ["رواية"], "unsupported_requests": ["تقييم مرتفع"]}), "ابغى روايات تقييمها عالي")
    ok(facts[:2] == [R_HI, R_MID], f"(1) rated books first, the highest first ({facts[:4]})")
    ok("متوسط تقييمه على Goodreads" in (seen.get("guidance") or ""), "(1) the guidance asks for the average and the count")
    ok(not [x for x in logged if x[0] in ("wish", "topic")], f"(1) «تقييم مرتفع» is not logged as a wish ({logged})")
    ok("له مرتفع" not in out["reply"] and "تقييمها عالي" in out["reply"],
       f"(1) the unsourced claim goes, the claim next to a rated book stays ({out['reply']})")
    # … no rated book among those that fit: say so
    con2 = models.get_connection(); con2.execute("UPDATE books SET goodreads_rating=NULL, goodreads_ratings_count=NULL"); con2.commit(); con2.close()
    out, facts, cards = run(raw(preferences={"genres": ["رواية"]}), "ابغى روايات تقييمها عالي")
    ok("ما في كتب facts أي كتاب له تقييم" in (seen.get("guidance") or ""), "(1) no rating in our records: said plainly")

    # (2) «فاجئيني بكتب جديدة»: books now, not the path's question
    answer.update(ids=None, reply="هذي بدايات متنوعة.")
    out, facts, cards = run(raw(intent="clarify"), "فاجئيني بكتب جديدة")
    ok(not out["reply"].startswith("يسعدني! تبغى المفاجأة") and cards, f"(2) «فاجئيني بكتب جديدة»: books at once ({out['reply'][:40]}, {cards})")

    # (3) «روايات تاريخية عن الإسلام في أوروبا»
    answer.update(ids=None, reply="عندك «باري» و«أيام قرطبة الأخيرة تجربة».")
    out, facts, cards = run(raw(preferences={"genres": ["رواية"], "unsupported_requests": ["الإسلام", "أوروبا"]}),
                            "ابغى روايات تاريخية عن الاسلام في اوروبا")
    ok(BARI in facts and ANDA in facts and ELEPH not in facts and ISLAM_ONLY not in facts,
       f"(3) «باري» and the Andalusia novel; not Europe alone, not Islam alone ({facts})")

    # (4) the chat adds the author of a card book named inside an impression
    answer.update(ids=[R_HI], reply="وبعض القرّاء يحسّون «رواية مقيمة عاليا تجربة» خفيفة.")
    out, facts, cards = run(raw(intent="about_book", mentioned_titles=["رواية مقيمة عاليا تجربة"]), "عندكم رواية مقيمة عاليا تجربة؟")
    _au = A.author_flow.display(_ab[R_HI].get("author") or "")
    ok(R_HI in cards and ("«رواية مقيمة عاليا تجربة» " + CR._lam(_au)) in out["reply"], f"(4) {out['reply']}")

    # (6) a فصحى visitor: the card's title is kept verbatim
    answer.update(ids=[B1857], reply=f"نعم، عندنا «{T1857}» وما تلقى مثله.")
    out, facts, cards = run(raw(intent="about_book", mentioned_titles=[T1857]), f"هل لديكم كتاب {T1857}؟")
    ok(f"«{T1857}»" in out["reply"] and "لدينا" in out["reply"] and "تجد مثله" in out["reply"], f"(6) {out['reply']}")
finally:
    C.interpret, C.respond, L.complete_json = _interp, _resp, _orig_cj
    A.open_questions.record, A.second_look.readings = _rec, _readings

con.close()
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
