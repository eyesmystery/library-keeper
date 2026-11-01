"""[CHATFIX31] Ibrahim 2026-11-01 «ابدا» (the backend steps of the chat review) and two more: (1a) the card's reason is the most
specific one, the bare shelf only alone; (1b) the reply is about 45–60 words, and one that still runs far over is cut at
sentence boundaries (never a title, never the final question); (1c) the quick answers are short, tied to what was shown, never
the visitor's own words, never something our data cannot answer; (1d) in English "an Arabic novel" is a novel first written
in Arabic; (2) the Goodreads line on every book card a visitor sees; (3) «جديدة» stays in the context through new subjects
(Ibrahim: «المفروض يظل عالسياق الا اذا هو الغى السياق او بدأ محادثة جديدة»), shown as «صادرة من ٢٠٢٣», until removed.
Checks without the model (its answers are faked)."""
import json, os, re, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm as L, chat as C, app as A, chat_rules as CR, topics as T
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

ROOT = os.path.dirname(os.getcwd())
src = open(A.__file__, encoding="utf-8").read()
js = open(os.path.join(ROOT, "frontend", "app.js"), encoding="utf-8").read()
css = open(os.path.join(ROOT, "frontend", "styles.css"), encoding="utf-8").read()

# --- (1a) the card's reason ----------------------------------------------------------------------------------------------
ok(CR.card_reasons(["من تصنيف «رواية» كما طلبت", "سعره 30 ريال", "يتناول «الحرب»"]) == ["يتناول «الحرب»", "سعره 30 ريال"],
   "(1a) the subject first, the bare shelf dropped when anything more specific is there")
ok(CR.card_reasons(["من تصنيف «رواية» كما طلبت"]) == ["من تصنيف «رواية» كما طلبت"], "(1a) the shelf alone stays")
ok(CR.short_reason("تصنيفه الفرعي «رواية تاريخية» يوافق طلبك") == "«رواية تاريخية» كما طلبت" and CR.short_reason("سعره 30 ريال") == "سعره ٣٠ ريال",
   "(1a) short forms, the price in Arabic digits")
_rt = A.reason_text(["من تصنيف «رواية» كما طلبت", "تصنيفه الفرعي «رواية تاريخية» يوافق طلبك", "يتناول «الأندلس»"])
ok(_rt.startswith("اخترناه لأنه يتناول «الأندلس»") and "من تصنيف" not in _rt and len(_rt.split()) <= 14, f"(1a) one short line ({_rt})")

_bk = {"hook": "مغامرة عبثية مضحكة تبدأ من داخل خزانة.", "genre": "رواية", "original_language": "fr", "page_count": 304}
ok(A.card_line("اخترناه لأنه من تصنيف «رواية» كما طلبت.", _bk) == "مغامرة عبثية مضحكة تبدأ من داخل خزانة."
   and A.card_line("اخترناه لأنه سعره ٢٥٫١٨ ريال.", _bk) == _bk["hook"]
   and A.card_line("قد يكون قريبًا من وصفك حسب انطباعات قرّاء أولية لم تُراجع بعد.", dict(_bk, hook="سطر قصير")) == "سطر قصير.",
   "(1a) a bare-shelf (or price-only) card line gives the book's own short line (the replay of 2026-11-01)")
ok(A.card_line("اخترناه لأنه يتناول «الحرب».", _bk) == "اخترناه لأنه يتناول «الحرب»." and A.card_line("اخترناه لأنه من تصنيف «رواية» كما طلبت.", {})
   == "اخترناه لأنه من تصنيف «رواية» كما طلبت.", "(1a) a specific reason stays; no hook, the line stays")
ok(A.card_line("Matches what you asked for: Novel.", _bk, "en") == "Novel, originally French, 304 pages."
   and A.card_line("Matches what you asked for: published in 2026.", _bk, "en") == "Matches what you asked for: published in 2026.",
   f"(1a) English: its kind, original language and length ({A.card_line('Matches what you asked for: Novel.', _bk, 'en')})")
ok("reason = card_line(reason, all_books[i], language)" in src, "(1a) the chat's cards use it")

# --- (1b) the reply's length -------------------------------------------------------------------------------------------------
LONG = ("هذي روايات خفيفة تناسب مزاجك اليوم وتقدر تقرأها في جلسة أو جلستين بدون ملل. «موسم الهجرة إلى الشمال» للطيب صالح رواية قصيرة "
        "عن رجل يعود إلى قريته في السودان ويكتشف سرًا. «التميمة» لنبيل فاروق مغامرة سريعة الإيقاع. وكثير من القراء يحبون هذا النوع من "
        "الروايات لأنه يجمع بين التشويق والعمق في صفحات قليلة ويخليك تفكر بعد ما تخلص. وإذا حبيت نقدر نشوف كتب ثانية من نفس الجو أو "
        "نغيّر الاتجاه تمامًا إلى شي مختلف عن اللي عرضته لك الحين حسب رغبتك ومزاجك. أي واحد يشدك أكثر؟")
cut, dropped = CR.trim_reply(LONG, ["موسم الهجرة إلى الشمال", "التميمة"])
ok(CR._words(LONG) > CR.TRIM_OVER and CR._words(cut) <= CR.TRIM_TO and len(dropped) == 1, f"(1b) a long reply is cut ({CR._words(LONG)} -> {CR._words(cut)})")
ok("«موسم الهجرة إلى الشمال»" in cut and "«التميمة»" in cut and cut.endswith("أي واحد يشدك أكثر؟") and cut.startswith("هذي روايات"),
   "(1b) the titles, the first sentence and the final question stay")
ok(CR.trim_reply("عندك «س». وهي خفيفة. أي واحد؟", []) == ("عندك «س». وهي خفيفة. أي واحد؟", []), "(1b) a short reply is untouched")
ok(CR._sentences("رواية «د. جيكل والسيد هايد» قصيرة. تبغى غيرها؟") == ["رواية «د. جيكل والسيد هايد» قصيرة. ", "تبغى غيرها؟"],
   "(1b) a title's full stop is no sentence end")
_plain = " ".join(["كلمة"] * 30) + ". " + " ".join(["ثانية"] * 30) + ". وفيها رواية الطنطورية الجميلة " + " ".join(["كلمة"] * 30) + ". تبغى؟"
ok("الطنطورية" in CR.trim_reply(_plain, ["الطنطورية"])[0], "(1b) a card title written plainly keeps its sentence")
ok("٤٥–٦٠ كلمة" in C.RESPOND_SYSTEM_AR and "about 45–60 words" in C.RESPOND_SYSTEM_EN and "بجملة واحدة" in C.RESPOND_SYSTEM_AR
   and "answer in one sentence" in C.RESPOND_SYSTEM_EN, "(1b) the prompt asks for 45–60 words, an order or compare answer in one sentence")

# --- (1c) the quick answers ---------------------------------------------------------------------------------------------------
TWO = [{"price": 30, "page_count": 120}, {"price": 25, "page_count": 300}]
got = CR.clean_chips(["روايات خفيفة", "أبغى أحجزه", "وين ألقاه؟", "ورني كتب من نفس الجو لو سمحت يا منار", "ورّني غيرها"], "روايات خفيفة", TWO)
ok(got == ["أي واحد أقصر؟", "ورّني غيرها"], f"(1c) no repeat, no booking, no «where», none over five words; one tied first ({got})")
ok(CR.clean_chips(["كم عدد صفحاته؟", "ورّني غيرها"], "رواية", [{"price": 40}]) == ["كتب قريبة منه", "ورّني غيرها"],
   "(1c) one card: no page question it has no answer to, «كتب قريبة منه» first")
got = CR.clean_chips(["وش تقييمها؟", "من مترجم الثاني؟", "روايات مترجمة", "روايات تقييمها عالي"], "رواية", TWO)
ok(got == ["أي واحد أقصر؟", "روايات مترجمة", "روايات تقييمها عالي"], f"(1c) no rating or translator question our data cannot answer; new requests stay ({got})")
ok(CR.clean_chips(["وش تقييمها؟"], "رواية", [dict(TWO[0], goodreads_rating=4.1, goodreads_ratings_count=300)])[-1] == "وش تقييمها؟",
   "(1c) … asked when a shown book has a rating")
ok(CR.clean_chips(["Which is lighter?", "Show me others"], "light novels", TWO, "en") == ["Which is lighter?", "Show me others"],
   "(1c) a tied chip already there is not added again")
ok(len(CR.clean_chips(["أ ب", "ج د", "هـ و", "ز ح", "ط ي", "ك ل"], "", TWO)) == 5 and CR.clean_chips(["غيرها"], "", [])[0] == "غيرها",
   "(1c) at most five; an empty message drops nothing")
ok("ولا تكرر ما طلبه الزائر للتو" in C.RESPOND_SYSTEM_AR and "never repeat what the visitor just asked" in C.RESPOND_SYSTEM_EN
   and "خمسة ردود قصيرة" in C.RESPOND_SYSTEM_AR, "(1c) the prompt: two to four words, tied to what was shown, still five (Ibrahim 2026-10-27)")

# --- (1d) "an Arabic novel" ----------------------------------------------------------------------------------------------------
for text, want in (("a light Arabic novel, under 200 pages", True), ("Arabic literature please", True), ("books by Arab writers", True),
                   ("a novel in Arabic", False), ("the Arabic edition of Dune", False), ("Dune translated into Arabic", False),
                   ("a light novel", False)):
    ok(CR.arabic_origin_asked(text) == want, f"(1d) {text!r} -> {want}")

# --- (3) recency stays in the context -------------------------------------------------------------------------------------------
W = A.new_release_wished
for msgs, want in ((["روايات جديدة", "شي في علم النفس"], "carried"), (["فاجئيني بكتب جديدة", "شي في علم النفس", "ابا روايه رومنسيه"], "carried"),
                   (["روايات جديدة", "مو لازم جديدة"], None), (["روايات جديدة", "شي في علم النفس", "أي سنة"], None),
                   (["روايات جديدة", "حتى القديمة"], None), (["روايات جديدة", "ما يهم السنة", "شي في علم النفس"], None),
                   (["روايات جديدة", "بغض النظر عن سنة النشر"], None), (["كتب جديدة"], "said")):
    ok(W(msgs) == want, f"(3) {msgs} -> {want} ({W(msgs)})")
ok('add("pubyear", "صادرة " + (' in src, "(3) the badge reads «صادرة من ٢٠٢٣»")

# --- (2) the Goodreads line on the cards --------------------------------------------------------------------------------------
ok("function goodreadsBadge(book)" in js and "const GR_MIN_RATINGS = 5;" in js and CR.RATING_MIN_COUNT == 5,
   "(2) the rating shows from 5 ratings, the chat's floor (Ibrahim 11-01)")
ok("a.target = '_blank'; a.rel = 'noopener noreferrer';" in js and "a.addEventListener('click', event => event.stopPropagation());" in js,
   "(2) a new tab, rel noopener, the card's tap untouched")
ok("return withGoodreads(card, book, 'book');" in js and "row.appendChild(withGoodreads(card, book, 'pick'));" in js,
   "(2) on the chat cards, «more by» lists (bookCard) and the monthly picks")
ok("grRated: r => `★ ${r} · Goodreads`, grReviews: 'آراء القرّاء على Goodreads'," in js and "grReviews: 'Readers’ reviews on Goodreads'," in js
   and "تقييم القرّاء على Goodreads ${r} من ${five}، يفتح في صفحة جديدة" in js and "opens in a new page" in js, "(2) the words and the names a screen reader hears, AR and EN")
ok(re.search(r"\.gr-badge\{[^}]*min-width:44px;min-height:44px", css) and "var(--coral)" in css.split("CHATFIX31 (2)")[1],
   "(2) a 44px target in the identity colours")
ok("goodreads_rating" in src.split("def _pick_book")[1].split("def ")[0] and "goodreads_ratings_count" in src.split("def _pick_book")[1].split("def ")[0],
   "(2) _pick_book carries the rating and its count")

# --- the whole chat with a faked model --------------------------------------------------------------------------------------
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
con.execute("UPDATE books SET publication_year=2001, original_year=NULL WHERE genre='علم نفس'")
PSY_NEW, PSY_OLD = pick("علم نفس"), pick("علم نفس")
setbook(PSY_NEW, title="عقل جديد تجربة", publication_year=2024)
setbook(PSY_OLD, title="عقل قديم تجربة", publication_year=2001)
AR_NOV, EN_NOV = pick("رواية"), pick("رواية")
setbook(AR_NOV, title="رواية عربية خفيفة تجربة", original_language="ar", page_count=150)
setbook(EN_NOV, title="An English Novel Test", original_language="en", page_count=150)
GR_B = pick()
setbook(GR_B, goodreads_rating=4.2, goodreads_ratings_count=900, goodreads_url="https://www.goodreads.com/book/show/1")
con.commit()
for fn in ("clear_cache", "invalidate_cache"):
    if hasattr(A, fn):
        getattr(A, fn)()
T._cache["index"] = None

pb = A._pick_book(dict(next(b for b in A.list_books(only_active=False) if b["book_id"] == GR_B)))
ok(pb["goodreads_rating"] == 4.2 and pb["goodreads_ratings_count"] == 900 and pb["goodreads_url"].endswith("/show/1"),
   f"(2) /api/picks' and the shelf's books carry the Goodreads fields ({ {k: pb[k] for k in pb if k.startswith('goodreads')} })")

BASE = {"intent": "recommend", "preferences": {}, "mentioned_titles": [], "title_variants": [], "wants_new_options": False,
        "reference_books": [], "author_request": {}, "avoid_topics": [], "order_by": "none", "reader_is_visitor": False,
        "reply_language": "ar", "whole_series": [], "first_publication": False, "publication_years": {"from": 0, "to": 0},
        "reader_age": 0, "physical_wishes": [], "author_countries": [], "about_shown": False, "copy_language": "any",
        "about_raising_child": False, "avoid_author_countries": [], "visitor_female": False}
def raw(**kw):
    r = json.loads(json.dumps(BASE)); r.update(kw); return r
_orig_cj, _interp, _resp = L.complete_json, C.interpret, C.respond
_rec, _readings = A.open_questions.record, A.second_look.readings
seen, answer = {}, {"ids": None, "reply": "رد تجربة.", "suggestions": []}
def _fake_resp(conv, facts, guidance, active, *a, **k):
    seen["facts"], seen["guidance"] = json.loads(facts), guidance
    ids = answer["ids"] if answer["ids"] is not None else [f["id"] for f in seen["facts"]][:4]
    return {"reply": answer["reply"], "show_book_ids": ids, "suggestions": list(answer["suggestions"]), "unanswered": "", "excluded_book_ids": []}
def run(r, users, language="ar", drop=None):
    try:
        L.complete_json = lambda *a, **k: json.loads(json.dumps(r))
        it = _interp([{"role": "user", "content": "x"}], active=("fake", "fake"), language=language)
    finally:
        L.complete_json = _orig_cj
    C.interpret = lambda conv, active, *a, **k: json.loads(json.dumps(it, default=list))
    C.respond = _fake_resp
    seen.clear()
    msgs = []
    for u in ([users] if isinstance(users, str) else users):
        if msgs:
            msgs.append({"role": "assistant", "content": "تفضل."})
        msgs.append({"role": "user", "content": u})
    out = A.run_chat(msgs, active=("fake", "m"), language=language, drop_context=drop)
    return out, [f["id"] for f in seen.get("facts") or []], [b["book_id"] for b in out["books"]]
def OLD_IN(facts):
    """a psychology book older than the new-release years is among the facts (all but PSY_NEW were set to 2001)"""
    return any(f != PSY_NEW for f in facts)
def badge(out, key):
    return next((x["label"] for x in out.get("context") or [] if x["key"] == key), None)

A.open_questions.record = lambda *a, **k: None
A.second_look.readings = lambda *a, **k: []
try:
    PSY = raw(preferences={"genres": ["علم نفس"]})
    out, facts, cards = run(PSY, ["روايات جديدة", "شي في علم النفس"])
    _lo = A.time.localtime().tm_year - A.NEW_RELEASE_YEARS
    ok(PSY_NEW in facts and PSY_OLD not in facts, f"(3) «جديدة» carried into a new subject: the new psychology book, not the old ({facts[:6]})")
    ok(badge(out, "pubyear") == "صادرة من " + A.fmt_num(_lo), f"(3) its badge stays: {badge(out, 'pubyear')}")
    ok("ما زال قائمًا" in seen["guidance"] and f"«من الكتب الصادرة من {A.fmt_num(_lo)}»" in seen["guidance"], "(3) the reply is asked to say the filter is on")
    out, facts, cards = run(PSY, ["روايات جديدة", "شي في علم النفس"], drop=["pubyear"])
    ok(badge(out, "pubyear") is None and OLD_IN(facts), f"(3) ✕ on its badge lets it go: older books come back ({badge(out, 'pubyear')}, {facts[:6]})")
    out, facts, cards = run(PSY, ["روايات جديدة", "مو لازم جديدة، شي في علم النفس"])
    ok(badge(out, "pubyear") is None and OLD_IN(facts), "(3) «مو لازم جديدة» lets it go")
    out, facts, cards = run(PSY, ["روايات جديدة", "روايه رومنسيه", "شي في علم النفس"])
    ok(badge(out, "pubyear") and PSY_OLD not in facts, "(3) still on two requests later")
    # none that new for this request: the wish stays and the reply says so
    setbook(PSY_NEW, publication_year=2001); con.commit()
    for fn in ("clear_cache", "invalidate_cache"):
        if hasattr(A, fn):
            getattr(A, fn)()
    out, facts, cards = run(PSY, ["روايات جديدة", "شي في علم النفس"])
    _all_new = facts and all((A.year_for_new(dict(b)) or 0) >= _lo for b in A.list_books(only_active=False) if b["book_id"] in facts)
    ok(badge(out, "pubyear") and (_all_new or "ما عندنا لهذا الطلب كتاب صادر من" in seen["guidance"]),
       f"(3) none that new: the badge stays, and the reply says so ({badge(out, 'pubyear')}, all new {bool(_all_new)})")
    setbook(PSY_NEW, publication_year=2024); con.commit()

    # (1c) the chips under a chat answer
    answer.update(ids=None, reply="عندك كتابين.", suggestions=["روايات خفيفة", "أبغى أحجزه", "ورّني غيرها"])
    out, facts, cards = run(raw(preferences={"genres": ["رواية"], "weight": "light"}), "روايات خفيفة")
    s = out["suggestions"]
    ok(len(cards) >= 2 and "روايات خفيفة" not in s and "أبغى أحجزه" not in s and s[0] in ("أي واحد أقصر؟", "أي واحد أخف؟") and len(s) >= 5,
       f"(1c) cleaned at the server, one tied first, padded to five ({s})")
    ok(out["_debug"].get("chips_cleaned") == ["روايات خفيفة", "أبغى أحجزه", "ورّني غيرها"], "(1c) what the model gave is kept in the debug")

    # (1b) a reply far over
    answer.update(ids=None, reply=LONG, suggestions=[])
    out, facts, cards = run(raw(preferences={"genres": ["رواية"]}), "رواية خفيفة")
    ok(CR._words(out["reply"]) <= CR.TRIM_TO and out["reply"].endswith("أي واحد يشدك أكثر؟") and out["_debug"].get("trimmed"),
       f"(1b) trimmed in the chat ({CR._words(out['reply'])} words)")

    # (1d) "a light Arabic novel, under 200 pages": the interpreter took «Arabic» for the copy's language
    answer.update(ids=None, reply="Here you go.", suggestions=[])
    out, facts, cards = run(raw(reply_language="en", copy_language="ar", preferences={"genres": ["رواية"], "length": "short"}),
                            "a light Arabic novel, under 200 pages", language="en")
    ok(EN_NOV not in facts and any(str(x["key"]).startswith("lang") for x in out.get("context") or []),
       f"(1d) a novel first written in Arabic, not one in an Arabic copy ({[x['key'] for x in out.get('context') or []]})")
finally:
    C.interpret, C.respond, L.complete_json = _interp, _resp, _orig_cj
    A.open_questions.record, A.second_look.readings = _rec, _readings

con.close()
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
