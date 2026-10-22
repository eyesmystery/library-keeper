"""[QADFIX12] Fixes for the QAD round 2026-10-22 (Ibrahim 2026-10-22 «حلها … حل جذري»; «المفروض النموذج يفهم هالشيء ومو لازم
نفصل كل الصيغ»). The visitor's meaning is read by the model (new interpreter fields), the code applies it; the code's own
fallbacks obey one general negation rule. Pure checks (no model, no live database)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat_rules as R, chat as C, llm as L
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

_src = open(A.__file__, encoding="utf-8").read()

# --- the interpreter reads the meaning (schema + cleaning)
req = C.INTERPRET_SCHEMA["required"]
ok(all(k in req for k in ("avoid_topics", "order_by", "reader_is_visitor", "reply_language", "whole_series", "first_publication")),
   "the interpreter returns avoid_topics, order_by, reader_is_visitor, reply_language, whole_series, first_publication")
ok("relation" in C.REFERENCE_SCHEMA["required"] and C.REFERENCE_SCHEMA["properties"]["relation"]["enum"] == ["same_book", "similar", "same_author"],
   "#8 a reference book says what the visitor wants from it (the book itself / books like it / its author)")
_raw = {"intent": "recommend", "preferences": {}, "mentioned_titles": ["ظل الريح"], "title_variants": [], "wants_new_options": False,
        "reference_books": [{"title": "ظل الريح", "author": "كارلوس زافون", "format": "novel", "moods": [], "themes": [], "subgenres": [],
                             "known": True, "relation": "similar"}],
        "author_request": {}, "avoid_topics": [" كرة القدم ", ""], "order_by": "oldest", "reader_is_visitor": True,
        "reply_language": "en", "whole_series": ["A Song of Ice and Fire"], "first_publication": True}
_orig = L.complete_json
try:
    L.complete_json = lambda *a, **k: dict(_raw)
    it = C.interpret([{"role": "user", "content": "x"}], active=("fake", "fake"))
finally:
    L.complete_json = _orig
ok(it["avoid_topics"] == ["كرة القدم"] and it["order_by"] == "oldest" and it["reader_is_visitor"] is True
   and it["reply_language"] == "en" and it["whole_series"] == ["A Song of Ice and Fire"] and it["first_publication"] is True,
   "the new fields are cleaned and passed on")
ok(it["references"][0]["relation"] == "similar", "#8 the reference keeps its relation")
_raw.update(order_by="sideways", reply_language="fr", reader_is_visitor="yes")
try:
    L.complete_json = lambda *a, **k: dict(_raw)
    it2 = C.interpret([{"role": "user", "content": "x"}], active=("fake", "fake"))
finally:
    L.complete_json = _orig
ok(it2["order_by"] is None and it2["reply_language"] == "ar" and it2["reader_is_visitor"] is False, "unknown values fall back safely")
for k in ("avoid_topics", "order_by", "reader_is_visitor", "reply_language", "whole_series", "first_publication"):
    ok(k in C.INTERPRET_SYSTEM, f"the interpreter prompt explains {k}")
ok("«بس مو عن الكورة»" in C.INTERPRET_SYSTEM and "«أقدمها»" in C.INTERPRET_SYSTEM and "«1984»" in C.INTERPRET_SYSTEM,
   "the prompt carries the cases of the round as examples, not as the only wordings")

# --- #1 negation: one general rule for the subjects the code takes from the words, and the model's avoid_topics
N = A.negated_in
ok(N("شي ثاني لنفس العمر بس مو عن الكورة", "الكوره"), "20.2 «بس مو عن الكورة» rejects «الكورة»")
ok(N("ما ابي شي عن الحرب", "الحرب") and N("ولا فيها حروب", "حروب") and N("رواية بدون سياسة", "سياسه"),
   "«ما أبي شي عن …»، «ولا فيها …»، «بدون …» reject")
ok(N("I dont want anything about war", "war") and N("a book about leadership, but not about business", "business"), "English negation rejects")
ok(not N("ما عندكم كتب عن الكورة؟", "الكوره"), "«ما عندكم كتب عن …؟» asks for it")
ok(not N("لا، أبي عن الفضاء", "الفضاء") and not N("لا أبي عن الفضاء", "الفضاء"), "«لا أبي عن …» wants it")
ok(not N("أبي رواية مو طويلة عن الحرب", "الحرب"), "«مو طويلة عن الحرب»: the negation is of the length, the war is wanted")
ok(not N("أبي رواية عن الحب", "الحب"), "no negation, no rejection")
ok("negated_in(user_messages[-1], phrase)" in _src and "_model_subjects" in _src, "the rule is applied to every subject the code itself took")
ok('_avoid_f = [topics.content_words(a) for a in it.get("avoid_topics") or []]' in _src, "the model's avoid_topics are never searched for")
ok('_rejected = {b["book_id"] for b in pool_before if topics.match(_avoid_p, b["book_id"], topic_index)[0]}' in _src,
   "books about a rejected subject are not offered either")
ok("if _w in _NEG_STOP and _n > 0:" in _src, "«مو عن …»: «عن» right after the negation no longer ends what is rejected")

# --- #2 a label that only describes the kind asked for never empties the answer
ok("_describes_kind" in _src and "(bool(hard) or _describes_kind)" in _src, "46.1 «شعر حديث» is ranked and said unconfirmed, not «not found»")
ok("«شعر حديث»" in L.PREFS_RULES, "the interpreter keeps «شعر حديث» a subgenre, never a subject")

# --- #3 the parent as reader, read by the model
R.set_model_parent(True)
ok(R.parent_reader(["أبي كتاب عن أي شي"]), "a turn the model read as «the book is for me, about my child» is a parent reader")
R.set_model_parent(False)
ok(not R.parent_reader(["أبي كتاب لولدي عمره ٤"]), "reset at every turn")
ok(R.parent_said("أبي كتاب لي أنا عن تربية طفل عمره سنتين"), "13.1 «تربية طفل» (singular) is a parent's wish")
ok('_model_parent = bool(it.get("reader_is_visitor")) and (it.get("prefs") or {}).get("audience") == "adult"' in _src
   and "chat_rules.set_model_parent(False)" in _src, "the model's reading is set per turn")

# --- #4 the order, read by the model
ok('_model_order == "oldest"' in _src and '_model_order == "cheapest"' in _src and '_model_order == "shortest"' in _src
   and '_model_order == "newest"' in _src, "1.1 «أقدمها»، «أرخصها»، «أقصر شوي»، «أحدثها» in any wording")
ok("_mt_compared" in _src, "1.2 «أقدم من الجريمة والعقاب»: the compared title does not stop the order")

# --- #5 a whole series by its name, in any language
_bk = {1: {"book_id": 1, "title": "A Game of Thrones", "series_name": "A Song of Ice and Fire", "series_position": 1, "quantity": 2},
       2: {"book_id": 2, "title": "A Clash of Kings", "series_name": "A Song of Ice and Fire", "series_position": 2, "quantity": 1},
       3: {"book_id": 3, "title": "الجليد", "quantity": 1}}
ok([b["book_id"] for b in R.series_books("A Song of Ice and Fire", _bk)] == [1, 2], "3.1 the series by its English name: its parts in order")
ok('for _sn in it.get("whole_series") or []:' in _src and 'it["whole_series_found"] = True' in _src,
   "3.1 «the whole series» brings every part we hold, before anything is called missing")
ok("never say you cannot confirm what is in facts" in _src, "3.3 the answer says plainly which parts we have")

# --- #6 the reply language
ok('if language != "en" and it.get("reply_language") == "en":' in _src, "32.1 a visitor writing in English is answered in English")
ok('if it["intent"] == "clarify" and (prefs.get("awards") or (last_batch and wanted_edition(user_messages[-1], language))):' in _src,
   "32.2 «In English please» after books: books, not one more question")

# --- #7 teenagers: every book that fits, with a warning
card_adult = {"format": "novel", "sensitive_content": ["sexual_content"], "severity": {"sexual_content": 3},
              "confidence": {("sensitive_content", "sexual_content"): (0.9, False)}}
ok(A.age_allowed(card_adult, "young_adult"), "47.2 a teenager is offered a novel for adults (the reply warns)")
ok(not A.age_allowed(card_adult, "young_adult", ("sexual_content",)), "content the visitor asked to avoid still keeps it out")
ok(not A.age_allowed(card_adult, "children"), "a child still gets only the children's shelf")
ok("إن فيه محتوى للكبار" in _src, "the reply is told to warn about adult content")
ok('if reader_age is not None and pprefs.get("audience") == "children":' in _src, "a teenager's books are not cut by age range")

# --- #8 «زي جو X»: X is the example, not the answer
ok('r.get("relation") in ("similar", "same_author")' in _src and "_like_ids" in _src, "5.2 the book named as an example is not a card")

# --- #9 a number that is a title
ok(A.years_said("similar to 1984 but more recent") == (1984, None) and "_title_nums" in _src,
   "6.1 «1984» named by the model as a title is taken out before the years are read")

# --- #10 price only from a number standing on its own
ok(R.price_wish(["a2sar shwaya"])[0] is None, "44.2 Arabizi «a2sar» is not a price of 2")
ok(R.price_wish(["رواية ما تتعدى ٢٥ ريال"])[0] == 25 and R.price_wish(["ابيها بـ٣٠ ريال"])[0] == 30
   and R.price_wish(["بعشرة ريال"])[0] == 10, "real prices still read")

# --- #11 the year a work first came out
ok('y = first_published(b) if it.get("first_publication") else year_for_new(b)' in _src,
   "40.2 «كتبت أول مرة سنة ٢٠٢٥، مو طبعة جديدة»: the edition year never stands in")
ok("ولا تقدّم سنة طبعة على أنها سنة صدور العمل أول مرة" in _src, "the reply is told so")

print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
