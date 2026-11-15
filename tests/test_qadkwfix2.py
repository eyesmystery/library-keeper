"""[QADKWFIX2] Ibrahim's answers on QADKWFIX (2026-11-15):
(a) «يوضح اول جملة ان النموذج متعطل ولقد فهمت»: an answer given without the model opens with one gentle sentence saying the smart assistant
    is paused (Arabic; English on the English page), then «فهمت: …» as before; a model answer has no such sentence;
(b) «نعم يجيب اقصر منها»: a bare «أقصر؟» keeps both — the books shown ranked by length and new shorter books;
(c) «نعم انقل نظام الكلمات الموسع وسو اختبارات»: the model path's checks of the visitor's words (app.model_understand, used in
    _chat_audience and _run_chat_once where local_understand was) read with the wider reader; local_understand itself is unchanged
    for /api/recommend and the model-free likeness answer.
Round wording and fresh wording, with negatives."""
import inspect, json, os, re, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import honors
import keyword_prefs as K
import kw_followup as KF
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

_NOTE = getattr(A, "MODEL_DOWN_NOTE", None) or {"ar": "المساعد الذكي متوقف مؤقتًا، فأرد عليك الآن بالبحث المباشر في كتبنا. ",
                                               "en": "Our smart assistant is paused for the moment, so I'm answering with a direct search of our books. "}
AR, EN = _NOTE["ar"], _NOTE["en"]   # (the fallback only lets this file run on the code before, to see what it catches)

# ---------------------------------------------------------------- (a) the wording itself
ok(AR == "المساعد الذكي متوقف مؤقتًا، فأرد عليك الآن بالبحث المباشر في كتبنا. ", f"a: the Arabic sentence ({AR})")
ok(EN == "Our smart assistant is paused for the moment, so I'm answering with a direct search of our books. ", f"a: the English sentence ({EN})")
for w in ("نموذج", "النظام البديل", "خطأ", "عطل", "API", "model", "fallback", "keyword", "error", "LLM", "offline"):
    ok(w.lower() not in (AR + EN).lower(), f"a: no technical word «{w}» in the sentence")
ok(len(AR.split()) <= 14 and len(EN.split()) <= 18, "a: one short sentence")

# ---------------------------------------------------------------- synthetic books (as tests/test_qadkwfix.py)
base = [dict(b) for b in A.list_books(only_active=False)][:80]
A.get_profile_cards = lambda: {}
A.embeddings.embed = lambda *a, **k: None
A.embeddings.search = lambda *a, **k: {}
A.embeddings.similar_to = lambda *a, **k: []
A.embeddings.similar_to_book = lambda *a, **k: []
A.llm.complete_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected live-model call"))
MYS = '["غموض"]'
SPEC = [
    dict(title="رواية طويلة", page_count=480, price=60.0),                                                          # 0
    dict(title="رواية متوسطة", page_count=260, price=45.5),                                                         # 1
    dict(title="رواية قصيرة جدا", page_count=110, price=22.0),                                                      # 2
    dict(title="رواية أقصر منها", page_count=90, price=18.0),                                                       # 3
    dict(title="رواية أقصر بكثير", page_count=70, price=25.0),                                                      # 4
    dict(title="عالم التنانين", subgenres='["فانتازيا"]', page_count=300, summary="ممالك وسحر"),                      # 5
    dict(title="The Silent Clue", language="en", subgenres=MYS, page_count=280, summary="A detective in a village"),  # 6 English mystery
    dict(title="Cooking at Home", language="en", genre="طبخ", page_count=150, summary="Recipes for every day"),       # 7 English cookbook
    dict(title="Garden Basics", language="en", genre="علوم", page_count=150, summary="How plants grow"),              # 8 English science
    dict(title="لغز البيت القديم", subgenres=MYS, page_count=240, summary="تحقيق في بيت مهجور"),                    # 9 Arabic mystery
    dict(title="The Road Home", language="en", page_count=300, summary="A long walk home"),                          # 10 English novel
    dict(title="The Prize Novel 2019", language="en", page_count=320, summary="A family over three generations"),    # 11 Pulitzer 2019
    dict(title="The Prize Novel 2010", language="en", page_count=300, summary="A town after the war"),               # 12 Pulitzer 2010
    dict(title="رواية البوكر العربية", page_count=300, summary="رواية عن مدينة"),                                    # 13 ipaf 2015
    dict(title="رواية البوكر الإنجليزية مترجمة", page_count=300, summary="رواية عن قرية"),                           # 14 booker 2016
] + [dict(title=f"رواية عادية {n}", summary="رواية عن الحياة اليومية") for n in range(40)]
books = []
for b, sp in zip([dict(x) for x in base], SPEC):
    b.update(quantity=3, active=1, author="كاتب " + str(b["book_id"]), original_year=None, summary="", hook="", keywords="", subjects="",
             subgenres=None, series_name=None, series_position=None, goodreads_url=None, language="ar", age_min=None, age_max=None,
             original_language=None, page_count=200, price=40.0, genre="رواية", publication_year=2010, setting=None, volumes=None,
             set_kind=None)
    b.update(sp)
    books.append(b)
assert len(books) == len(SPEC), (len(books), len(SPEC))
A.list_books = lambda *a, **k: [dict(x) for x in books]
A.topics._cache.update(key=None)
I = {k: books[k]["book_id"] for k in range(len(SPEC))}
_REV = {v: k for k, v in I.items()}
ix = lambda ids: [_REV.get(i, i) for i in ids]
conn = models.get_connection()
honors.ensure_table(conn)
conn.execute("DELETE FROM book_awards")
for k, key, y in ((11, "pulitzer", 2019), (12, "pulitzer", 2010), (13, "ipaf", 2015), (14, "booker", 2016)):
    conn.execute("INSERT INTO book_awards (book_id, award_key, result, year, source, created_at) VALUES (?, ?, 'winner', ?, 'test', 'now')",
                 (I[k], key, y))
conn.commit(); conn.close()
honors.clear_cache()

def msgs_of(turns):
    return [{"role": "user", "content": x} if isinstance(x, str) else x for x in turns]

def shown(*ks, text="هذي الكتب"):
    return {"role": "assistant", "content": text, "book_ids": [I[k] for k in ks]}

def kw(*turns, language="ar"):
    """The whole turn without the model (run_chat, as /api/chat answers when the model is down)."""
    r = A.run_chat(msgs_of(turns), active=None, language=language)
    return r, [b["book_id"] for b in r.get("books") or []], (r.get("_debug") or {})

# ---------------------------------------------------------------- (a) on the model-free answers
_r, _b, _d = kw("ابي رواية رعب")
ok(_r["understanding"] == "keywords" and _r["reply"].startswith(AR + "فهمت: رواية، رعب"),
   f"a: the sentence first, then «فهمت: …» ({_r['reply'][:110]})")
_r, _b, _d = kw("فانتازيا")
ok(_r["reply"].startswith(AR + "فهمت: رواية، فانتازيا.") and I[5] in _b, f"a (fresh): «فانتازيا» ({_r['reply'][:100]}; {ix(_b)})")
_r, _b, _d = kw("sci-fi pls", language="en")
ok(_r["reply"].startswith(EN + "Understood: novel, science fiction."), f"a en: the English page gets the English sentence ({_r['reply'][:130]})")
ok("المساعد" not in _r["reply"], "a en: no Arabic sentence on the English page")
_r, _b, _d = kw("هلا")
ok(_r["reply"].startswith(AR + "هلا والله"), f"a: a greeting too ({_r['reply'][:90]})")
_r, _b, _d = kw("hello", language="en")
ok(_r["reply"].startswith(EN + "Hello!"), f"a en: a greeting too ({_r['reply'][:120]})")
_r, _b, _d = kw("ابي رواية", shown(0, 1, 2), "كم صفحة الأولى؟")
ok(_r["reply"].startswith(AR) and "٤٨٠" in _r["reply"], f"a: an answer from the book's record too ({_r['reply'][:110]})")
ok(_r["reply"].count(AR.strip()) == 1, "a: said once")
# a model answer has no such sentence; an answer already holding it is not given it twice
_keep = A._run_chat_looked
A._run_chat_looked = lambda *a, **k: {"reply": "أرشح لك «رواية طويلة».", "books": [], "suggestions": [], "understanding": "ai", "_debug": {}}
ok(A.run_chat(msgs_of(["ابي رواية"]), language="ar")["reply"] == "أرشح لك «رواية طويلة».", "a negative: a model answer is left as it is")
A._run_chat_looked = lambda *a, **k: {"reply": AR + "فهمت: رواية.", "books": [], "suggestions": [], "understanding": "keywords", "_debug": {}}
ok(A.run_chat(msgs_of(["ابي رواية"]), language="ar")["reply"] == AR + "فهمت: رواية.", "a: never twice")
A._run_chat_looked = lambda *a, **k: {"reply": "These books are available now.", "books": [], "suggestions": [], "understanding": "keywords",
                                      "_debug": {}}
ok(A.run_chat(msgs_of(["any novel"]), language="ar")["reply"].startswith(EN), "a: an English answer on the Arabic page gets the English sentence")
A._run_chat_looked = lambda *a, **k: {"reply": "", "books": [], "suggestions": [], "understanding": "keywords", "_debug": {}}
ok(A.run_chat(msgs_of(["ابي رواية"]), language="ar")["reply"] == "", "a negative: an empty answer stays empty")
A._run_chat_looked = _keep

# ---------------------------------------------------------------- (b) a bare «أقصر؟»
ok(KF.classify("أقصر؟") == ("both", "shortest") and KF.classify("اقصر؟") == ("both", "shortest") and KF.classify("أرخص؟") == ("both", "cheapest"),
   "b: a bare «أقصر؟ / اقصر؟ / أرخص؟» asks for both")
ok(KF.classify("أيهم أقصر؟") == ("order", "shortest") and KF.classify("فيه شي أقصر؟") == ("more", "shortest"),
   "b negative: «أيهم أقصر؟» is the order only, «فيه شي أقصر؟» new books only")
_r, _b, _d = kw("ابي رواية", shown(0, 1, 2), "أقصر؟")
_rest = _r["reply"][len(AR):] if _r["reply"].startswith(AR) else ""
ok(_rest.startswith("الأقصر بين الكتب اللي عرضتها «رواية قصيرة جدا» (١١٠ صفحة)"), f"b (round): the shown books ranked first ({_r['reply'][:170]})")
ok(_b and not set(ix(_b)) & {0, 1, 2} and all(next(x for x in books if x["book_id"] == i)["page_count"] < 110 for i in _b)
   and {3, 4} <= set(ix(_b)), f"b: and new books shorter than 110 pages ({ix(_b)})")
ok("أقصر من «رواية قصيرة جدا»" in _r["reply"], f"b: the new books said as shorter than the shortest shown ({_r['reply'][-160:]})")
_r, _b, _d = kw("ابي رواية", shown(0, 1, 2), "اقصر؟")
ok(_r["reply"][len(AR):].startswith("الأقصر بين الكتب اللي عرضتها") and {3, 4} <= set(ix(_b)), "b (fresh, no hamza): the same")
_r, _b, _d = kw("ابي رواية", shown(0, 1, 2), "أيهم أقصر؟")
ok(ix(_b) == [2, 1, 0], f"b negative: «أيهم أقصر؟» → the shown books only ({ix(_b)})")
_r, _b, _d = kw("ابي رواية", shown(0, 1, 2), "فيه شي أقصر؟")
ok(_b and not set(ix(_b)) & {0, 1, 2} and "الأقصر بين الكتب اللي عرضتها" not in _r["reply"], "b negative: «فيه شي أقصر؟» → new books only")

# ---------------------------------------------------------------- (c) the wider reader in the model path
M = getattr(A, "model_understand", A.local_understand)
for q in ("بنتي ١٥ سنة تحب الفانتازيا بس ما تبي رومانسية أبد", "أقصر", "ابغى رواية فازت بالبوكر العربية", "any Pulitzer winners?",
          "ولدي عمره ١٦ سنة ويبغى شي رعب", "ودي برواية تاريخية", "عندكم مئة عام من العزلة بالانجليزي؟", "a cozy mystery"):
    _m, _w = M([q]), A.fallback_understand([q])
    ok({k: v for k, v in _m.items() if k != "genres"} == {k: v for k, v in _w.items() if k != "genres"},
       f"c: the model path reads «{q}» as the model-free answer does (but for an inferred form)")
ok(M(["أقصر"])["length"] == "short" and A.local_understand(["أقصر"])["length"] is None, "c: «أقصر» is a length wish now (was none)")
ok(M(["فانتازيا"])["genres"] == [] and A.fallback_understand(["فانتازيا"])["genres"] == ["رواية"] and M(["فانتازيا"])["subgenres"] == ["فانتازيا"],
   "c (targeted 19.2): «فانتازيا» alone names no form in the model path (the model-free answer still takes it for a novel)")
ok(M(["ولدي عمره ١٦ سنة ويبغى شي رعب"])["genres"] == [] and M(["رواية فانتازيا"])["genres"] == ["رواية"]
   and M(["قصة فانتازيا لبنتي ست سنوات"])["genres"] in (["أطفال"], ["رواية"]),
   "c: «شي رعب» names no form; «رواية فانتازيا» / «قصة … لبنتي» do")
ok(M(["ابغى رواية فازت بالبوكر العربية"])["awards"] == ["ipaf"] and A.local_understand(["ابغى رواية فازت بالبوكر العربية"])["awards"] == ["ipaf", "booker"],
   "c: «البوكر العربية» is the Arabic Booker only (was the Arabic and the English Booker)")
ok(M(["ودي برواية تاريخية"])["genres"] == ["رواية"] and M(["ودي برواية تاريخية"])["subgenres"] == ["تاريخية"]
   and "تاريخ" in A.local_understand(["ودي برواية تاريخية"])["genres"], "c: «رواية تاريخية» a kind of novel, not the history shelf (was)")
ok(M(["اخوي عمره ١٤ ويحب المغامرات والخيال العلمي"])["genres"] != ["علوم"], "c: «الخيال العلمي» is no science shelf")
ok(M(["any Pulitzer winners?"])["awards"] == ["pulitzer"] and not A.local_understand(["any Pulitzer winners?"]).get("awards") == ["pulitzer"],
   "c: 'any Pulitzer winners?' is the Pulitzer (was not read)")
ok(M(["a cozy mystery"]).get("subgenres") == ["غموض"] and "subgenres" not in A.local_understand(["a cozy mystery"]), "c: 'a cozy mystery' has its kind")
# where: no model-path check reads with the old reader; the old reader is kept where it was meant to stay
_src = {n: inspect.getsource(getattr(A, n)) for n in ("_run_chat_once", "_chat_audience", "recommend", "fallback_similar_conditions")}
ok("local_understand(" not in _src["_run_chat_once"] and "local_understand(" not in _src["_chat_audience"]
   and _src["_run_chat_once"].count("model_understand(") >= 11 and "model_understand(" in _src["_chat_audience"],
   "c: _run_chat_once and _chat_audience read with model_understand only")
ok("local_understand(" in _src["recommend"] and "local_understand(" in _src["fallback_similar_conditions"],
   "c: /api/recommend and the model-free likeness answer keep local_understand")
ok(A.local_understand(["رواية غموض"])["moods_include"] == ["mysterious"] and "subgenres" not in A.local_understand(["رواية غموض"]),
   "c negative: local_understand itself reads as before")

# the model path on the synthetic books, the model's interpretation and reply faked (no network)
CUR = {}
def interp(*a, **k):
    it = {"intent": "recommend", "mentioned_titles": [], "title_variants": [], "wants_new_options": False, "references": [],
          "author_request": {}, "order_by": None, "about_shown": False, "reader_age": None}
    s = dict(CUR)
    it["prefs"] = A.llm.clean_prefs(s.pop("prefs", {}))
    it.update(s)
    return it
captured = []
def respond(conv, facts_json, guidance, *a, **k):
    facts = json.loads(facts_json); captured.append({"facts": facts, "guidance": guidance})
    return {"reply": "ok", "show_book_ids": [f["id"] for f in facts][:2], "suggestions": [], "unanswered": ""}
A.chat.interpret, A.chat.respond = interp, respond

def run(turns, language="ar", **it):
    CUR.clear(); CUR.update(it)
    captured.clear()
    r = A._run_chat_once(msgs_of(turns), active=("synthetic", "no-network"), language=language)
    c = captured[0] if captured else {"facts": [], "guidance": ""}
    return r, [f["id"] for f in c["facts"]], c["guidance"], (r.get("_debug") or {})

# 7378: «ومين فاز فيها؟» after a prize the model did not carry — the prize said before, now English prizes too
_a = {"role": "assistant", "content": "عندنا روايات فازت بجائزة بوليتزر", "book_ids": [I[12]]}
_r, _f, _g, _d = run(["any Pulitzer winners?", _a, "who won it in 2019?"], language="en", prefs={"genres": ["رواية"]})
ok(_f[:1] == [I[11]], f"c (7378): 'who won it in 2019?' after 'any Pulitzer winners?' → the 2019 Pulitzer winner ({ix(_f[:3])})")
_r, _f, _g, _d = run(["أبي رواية بوليتزر", _a, "ومين فاز فيها ٢٠١٩؟"], prefs={"genres": ["رواية"]})
ok(_f[:1] == [I[11]], f"c (fresh, Arabic): «ومين فاز فيها ٢٠١٩؟» after «بوليتزر» → the same ({ix(_f[:3])})")
_r, _f, _g, _d = run(["ابي رواية فازت بالبوكر العربية", {"role": "assistant", "content": "…", "book_ids": [I[13]]}, "ومين فاز فيها ٢٠١٦؟"],
                     prefs={"genres": ["رواية"]})
ok(I[14] not in _f[:1], f"c (7378): «البوكر العربية» carried is the Arabic Booker, not the English Booker's 2016 winner ({ix(_f[:3])})")
_r, _f, _g, _d = run(["ابي رواية بالإنجليزي", {"role": "assistant", "content": "…", "book_ids": [I[10]]}, "ومين فاز فيها ٢٠١٩؟"],
                     prefs={"genres": ["رواية"]})
ok(any(i not in (I[11], I[12], I[13], I[14]) for i in _f), f"c negative: no prize said before → not only prize winners ({ix(_f[:4])})")

# 7270: «English books only please» after a kind of book the model did not carry — the kind said before, now with its subgenre
_a = {"role": "assistant", "content": "…", "book_ids": [I[9]]}
_r, _f, _g, _d = run(["a cozy mystery please", _a, "English books only please"], language="en", prefs={})
ok(_f[:1] == [I[6]] and I[7] not in _f[:2] and I[8] not in _f[:2],
   f"c (7270): 'English books only please' after 'a cozy mystery' → the English mystery first, no cookbook ({ix(_f[:4])})")
_r, _f, _g, _d = run(["ابي رواية غموض", _a, "بالإنجليزي بس"], prefs={})
ok(_f[:1] == [I[6]], f"c (fresh, Arabic): «بالإنجليزي بس» after «رواية غموض» → the English mystery ({ix(_f[:4])})")
_r, _f, _g, _d = run(["a cozy mystery please", _a, "English books only please"], language="en",
                     prefs={"genres": ["علوم"]})
ok(_f[:1] == [I[8]], f"c negative: the model's own kind for the latest message wins ({ix(_f[:3])})")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
