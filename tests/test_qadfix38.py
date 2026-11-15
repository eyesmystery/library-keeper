"""[QADFIX38] Fixes for the QAD round 2026-11-13 (Ibrahim's standing word 2026-11-06: fixes go ahead, doubtful ones are listed):
(1) grave 15.1 «رواية حب راقية، لغتها فصيحة متينة» → the subject «فصيحه» → «لم أجد رواية حب»: a wish on the language / style is no subject;
(2) 7.2 «شي ثاني له غير جورج؟» for a boy of 12 → «جيم الزر» (6+) alone: QADFIX36 «ج» (ya_kid36) holds on the follow-up too;
(3) 1.2–1.3 the newest Pulitzer is «Trust» (an English copy) in the guidance but not in facts on the Arabic page; «دوري زين» keeps it;
(4) 2.2 «رواية لي عن الحرب العالمية» → «ما لقيت»: a world war is one word of a subject (Arabic / English, with its order);
(5) 13.2–13.3 the set record «سلسلة لوسي وستيفن هوكينج (1-6)» named by its title, or «المجموعة» after its series, is in facts;
(6) 43.1 a girl of 3 → «حكايات القط الشقي» (672 pages, no age): a long book with no recorded age is not a small child's.
Each check uses the round wording and a fresh one, with negatives."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import honors
import topics
from keyword_prefs import normalize
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# ---------------------------------------------------------------- word readers
for s in ("لغة فصيحة متينة", "لغتها جميلة", "أسلوب سلس", "بالفصحى", "فصيحة", "لغة رصينة وأسلوب بليغ", "beautiful prose", "لغة فصيحة وبعيدة عن الابتذال"):
    ok(A.style_wish38(s), f"1: «{s}» is a wish on the writing, no subject")
for s in ("أسلوب الكتابة", "تعلم الفصحى", "اللغة العربية", "الحب", "قواعد اللغة", "لغة الجسد", "الشخصية القوية"):
    ok(not A.style_wish38(s), f"1: «{s}» stays a subject")
ok(topics.ww_tokens(normalize("الحرب العالمية")) == {topics.WW_TOKEN}
   and topics.ww_tokens(normalize("خلال الحرب العالمية الثانية")) == {topics.WW_TOKEN, topics.WW_TOKEN + "2"}
   and topics.ww_tokens(normalize("للحرب العالمية الأولى")) == {topics.WW_TOKEN, topics.WW_TOKEN + "1"}
   and topics.ww_tokens(normalize("a WWII love story")) == {topics.WW_TOKEN, topics.WW_TOKEN + "2"}
   and topics.ww_tokens(normalize("the Second World War")) == {topics.WW_TOKEN, topics.WW_TOKEN + "2"},
   "4: «الحرب العالمية (الأولى / الثانية)», «WWII», «the Second World War» are one word with their order")
ok(not topics.ww_tokens(normalize("حرب الخليج")) and not topics.ww_tokens(normalize("الأدب العالمي والحرب")),
   "4: «حرب الخليج», «الأدب العالمي والحرب» are no world war")
ok(topics.content_words("الحرب العالمية الثانية") == [{topics.WW_TOKEN + "2"}]
   and topics.core_word("الحرب العالمية الثانية", ({}, {}, 1)) == "الحرب العالميه",
   "4: the subject is that one word; with its order found nowhere, it falls back to the war itself (never «العالمية»)")
NEW = A.chat_rules.newest_asked33
ok(A.prize_order_kept38(["أول كتاب حصل على البوليتزر؟", "والأحدث فوزًا بينها؟", "يعني ما تعرفين اسمه؟ دوري زين"], NEW)
   and A.prize_order_kept38(["والأحدث فوزًا بينها؟", "what's its name?"], NEW)
   and A.prize_order_kept38(["وش آخر فائزة بالغونكور؟", "متأكدة؟ وش اسمها؟"], NEW),
   "3: «يعني ما تعرفين اسمه؟ دوري زين» / «وش اسمها؟» after the newest asked keeps it")
ok(not A.prize_order_kept38(["والأحدث فوزًا بينها؟", "طيب وش أرخصها؟ وش اسمه؟"], NEW)
   and not A.prize_order_kept38(["والأحدث فوزًا بينها؟", "ابي رواية رعب"], NEW)
   and not A.prize_order_kept38(["ابي رواية رعب", "وش اسمه؟"], NEW),
   "3: another order, another wish, or no newest asked before: not kept")
ok(A.too_long_for_small38({"page_count": 672}, 3) and not A.too_long_for_small38({"page_count": 32}, 3)
   and not A.too_long_for_small38({"page_count": 672, "age_min": 3, "age_max": 6}, 3) and not A.too_long_for_small38({"page_count": 672}, 7),
   "6: 672 pages with no recorded age is not a 3-year-old's; 32 pages, a recorded 3–6, or a child of 7 are left as before")

# ---------------------------------------------------------------- whole path on synthetic books
base = [dict(b) for b in A.list_books(only_active=False)][:90]
A.get_profile_cards = lambda: {}
A.embeddings.embed = lambda *a, **k: None
A.embeddings.search = lambda *a, **k: {}
A.embeddings.similar_to = lambda *a, **k: []
A.llm.complete_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected live-model call"))
CUR = {}
def interp(*a, **k):
    it = {"intent": "recommend", "mentioned_titles": [], "title_variants": [], "wants_new_options": False, "references": [],
          "author_request": {}, "order_by": None, "about_shown": False, "reader_age": None}
    s = dict(CUR)
    it["prefs"] = A.llm.clean_prefs(s.pop("prefs", {}))
    it.update(s)
    return it
A.chat.interpret = interp
captured = []
def respond(conv, facts_json, guidance, *a, **k):
    facts = json.loads(facts_json); captured.append({"facts": facts, "guidance": guidance})
    return {"reply": "ok", "show_book_ids": [f["id"] for f in facts][:2], "suggestions": [], "unanswered": ""}
A.chat.respond = respond
ROM = '["رومانسية"]'
SPEC = [
    dict(title="عشاق الربيع", subgenres=ROM, summary="قصة حب بين شابين في مدينة ساحلية"),                                     # 0
    dict(title="قلب في الريح", subgenres=ROM, summary="قصة حب وفراق"),                                                       # 1
    dict(title="جميلة", subgenres=ROM, summary="قصة حب في قرية زمن الحرب"),                                                  # 2
    dict(title="فصيح الكلام", genre="أدب", summary="مقالات في البلاغة والفصاحة"),                                           # 3
    dict(title="قواعد اللغة العربية الميسرة", genre="تعليم", summary="كتاب في قواعد اللغة العربية للمبتدئين"),               # 4
    dict(title="وقت للحب ووقت للموت", subjects='["جندي ألماني في إجازة", "الحرب العالمية الثانية"]', summary="جندي يعود إلى مدينته"),  # 5
    dict(title="الجندي الطيب في الحرب العالمية", subjects='["جندي تشيكي في الحرب العالمية الأولى"]', summary="رواية ساخرة"),  # 6
    dict(title="العندليب", summary="أختان في فرنسا المحتلة خلال الحرب العالمية الثانية"),                                 # 7
    dict(title="ليالي الخليج", summary="رواية عن حرب الخليج والعائلة والحرب"),                                            # 8
    dict(title="الأدب العالمي الحديث", genre="نقد أدبي", summary="مختارات من الأدب العالمي"),                              # 9
    dict(title="The Long Night", language="en", summary="A love story during World War II in London"),                      # 10
    dict(title="سلسلة لوسي وستيفن هوكينج (1-6)", genre="أطفال", author="لوسي هوكينج وستيفن هوكينغ", series_name="سلسلة جورج",
         volumes=6, set_kind="collection", price=308.36, age_min=8, age_max=12, summary="مجموعة من ست روايات"),            # 11
] + [dict(title=t, genre="أطفال", author="لوسي هوكينج وستيفن هوكينج", series_name="سلسلة جورج", series_position=n + 1, price=30.0,
          age_min=8, age_max=12, summary="شرح علمي مبسط للكون")
     for n, t in enumerate(("جورج ومفتاح الكون", "جورج والبحث عن الكنز", "جورج والانفجار", "جورج والشفرة"))] + [   # 12–15
    dict(title="حكايات القط الكبيرة", genre="أطفال", page_count=672, summary="حكايات مرحة عن قط شقي وصديقاته القطط"),     # 16
    dict(title="القط الصغير", genre="أطفال", age_min=3, age_max=6, page_count=24, summary="قصة مصورة عن القط الصغير وأمه"),  # 17
    dict(title="قمر وقطط الحي", genre="أطفال", age_min=9, age_max=12, page_count=150, summary="قصة عن القطط في الحي"),      # 18
    dict(title="جيم والقطار", genre="أطفال", age_min=6, subgenres='["مغامرة"]', summary="مغامرة جيم وصديقه"),              # 19
    dict(title="جورج وكنز الكون", genre="أطفال", age_min=8, age_max=12, subgenres='["مغامرة"]', summary="مغامرة في الفضاء"),  # 20
    dict(title="رحلة الكنز البعيد", genre="يافعين", age_min=9, age_max=12, subgenres='["مغامرات"]', summary="رواية لليافعين عن مغامرة"),  # 21
    dict(title="سر الجزيرة", genre="يافعين", age_min=10, keywords='["مغامرة"]', summary="مغامرة على جزيرة"),               # 22
    dict(title="Trust", language="en", summary="A novel about a financier in New York"),                                  # 23 pulitzer 2023
    dict(title="عصر البراءة", summary="رواية عن نيويورك القديمة"),                                                        # 24 pulitzer 1921
    dict(title="الأرض الطيبة", summary="رواية عن أسرة ريفية في الصين"),                                                   # 25 pulitzer 1932
    dict(title="الحسون", summary="رواية عن لوحة"),                                                                         # 26 pulitzer 2014
    dict(title="الثقة بالمعلمين", genre="فكر", summary="كتاب عن التعليم في فنلندا"),                                        # 27 no prize
] + [dict(title=f"رواية عادية {n}", summary=("رواية عن الحرب والحب في المدينة" if n < 8 else "رواية من الأدب العالمي الحديث" if n < 16
                                             else "رواية عن الحياة اليومية")) for n in range(60)]   # «الحرب» / «العالمي» are common words here too
books = []
for b, sp in zip([dict(x) for x in base], SPEC):
    b.update(quantity=3, active=1, author="كاتب " + str(b["book_id"]), original_year=None, summary="", hook="", keywords="", subjects="",
             subgenres=None, series_name=None, series_position=None, goodreads_url=None, language="ar", age_min=None, age_max=None,
             page_count=200, genre="رواية", publication_year=2015, setting=None, volumes=None, set_kind=None)
    b.update(sp)
    books.append(b)
assert len(books) == len(SPEC), (len(books), len(SPEC))
A.list_books = lambda *a, **k: books
A.topics._cache.update(key=None)
I = {k: books[k]["book_id"] for k in range(len(SPEC))}
conn = models.get_connection()
honors.ensure_table(conn)
conn.execute("DELETE FROM book_awards")
for k, y in ((23, 2023), (24, 1921), (25, 1932), (26, 2014)):
    conn.execute("INSERT INTO book_awards (book_id, award_key, result, year, source, created_at) VALUES (?, 'pulitzer', 'winner', ?, 'test', 'now')",
                 (I[k], y))
conn.commit(); conn.close()
honors.clear_cache()

def run(msgs, language="ar", **it):
    CUR.clear(); CUR.update(it)
    captured.clear()
    m = [{"role": "user", "content": x} if isinstance(x, str) else x for x in msgs]
    r = A._run_chat_once(m, active=("synthetic", "no-network"), language=language)
    c = captured[0] if captured else {"facts": [], "guidance": ""}
    return r, [f["id"] for f in c["facts"]], c["guidance"], (r.get("_debug") or {})

# 1 (15.1, grave)
Q151 = "أبحث عن رواية حب راقية، لغتها فصيحة متينة وبعيدة عن الابتذال"
P151 = {"genres": ["رواية"], "subgenres": ["رومانسية"], "moods_include": ["romantic"], "unsupported_requests": ["لغة فصيحة متينة"]}
_r, _f, _g, _d = run([Q151], prefs=P151)
ok((_d.get("reference") or {}).get("topic_phrases") == [], f"15.1: «لغة فصيحة متينة» is no subject ({(_d.get('reference') or {}).get('topic_phrases')})")
ok("ولا تعرض أي كتاب" not in _g and {I[0], I[1]} <= set(_f[:3]), f"15.1: romances are offered, not «show no book» ({_f[:4]})")
ok("وصفٌ للغة الكتابة" in _g and I[3] not in _f[:3], "15.1: the guidance says the language is not measured; «فصيح الكلام» is not the answer")
_r, _f, _g, _d = run(["طيب رواية حب وبس، أهم شي لغتها جميلة"], prefs=dict(P151, unsupported_requests=["لغة جميلة"]))
ok((_d.get("reference") or {}).get("topic_phrases") == [] and "عنوانه عن «جميله»" not in str(_d.get("candidates")),
   "15.2: «لغة جميلة» is no subject either (the title «جميلة» is not found «about» it)")
_r, _f, _g, _d = run(["أبي رواية رومانسية أسلوبها سلس ولغتها رصينة"], prefs=dict(P151, unsupported_requests=["أسلوب سلس", "لغة رصينة"]))
ok((_d.get("reference") or {}).get("topic_phrases") == [] and "ولا تعرض أي كتاب" not in _g and I[0] in _f,
   f"fresh: «أسلوبها سلس ولغتها رصينة» → romances, no «show no book» ({_f[:3]})")
_r, _f, _g, _d = run(["أبي كتاب عن قواعد اللغة العربية"], prefs={"unsupported_requests": ["قواعد اللغة العربية"]})
ok((_d.get("reference") or {}).get("topic_phrases") == ["قواعد اللغة العربية"] and _f[:1] == [I[4]],
   f"negative: «قواعد اللغة العربية» stays a subject ({_f[:2]})")

# 4 (2.2, medium)
_r, _f, _g, _d = run(["أدور قصة لحفيدتي", {"role": "assistant", "content": "…", "book_ids": [I[17]]},
                      "زين، وأنا جدتها ودي برواية لي عن الحرب العالمية"],
                     prefs={"genres": ["رواية"], "unsupported_requests": ["الحرب العالمية"], "audience": "adult"})
ok({I[5], I[6], I[7]} <= set(_f[:4]) and I[8] not in _f[:3] and I[9] not in _f[:3],
   f"2.2: «عن الحرب العالمية» → the world-war novels first, not «حرب الخليج» / «الأدب العالمي» ({_f[:5]})")
ok((_d.get("reference") or {}).get("topic_phrases") == ["الحرب العالمية"], "2.2: the subject is searched")
_r, _f, _g, _d = run(["ابي رواية عن الحرب العالمية الثانية"], prefs={"genres": ["رواية"], "unsupported_requests": ["الحرب العالمية الثانية"]})
ok(set(_f[:2]) == {I[5], I[7]} and I[6] not in _f[:2], f"fresh: «الثانية» → the novels of the second, before the first one ({_f[:4]})")
_r, _f, _g, _d = run(["any novel set in World War II?"], language="en", prefs={"genres": ["رواية"], "unsupported_requests": ["World War II"]})
ok(I[10] in _f[:4] and I[5] in _f[:4], f"fresh (English): «World War II» finds the English copy and the Arabic ones ({_f[:4]})")
_r, _f, _g, _d = run(["ابي رواية عن حرب الخليج"], prefs={"genres": ["رواية"], "unsupported_requests": ["حرب الخليج"]})
ok(_f[:1] == [I[8]] and I[5] not in _f[:2], f"negative: «حرب الخليج» is not a world war ({_f[:3]})")

# 3 (1.2–1.3, medium)
Q11, Q12, Q13 = "أول كتاب حصل على البوليتزر من اللي على رفوفكم، وش هو؟", "والأحدث فوزًا بينها؟", "يعني ما تعرفين اسمه؟ دوري زين"
_a11 = {"role": "assistant", "content": "أقدم كتاب فاز ببوليتزر عندنا «عصر البراءة» ١٩٢١", "book_ids": [I[24], I[25]]}
_a12 = {"role": "assistant", "content": "أحدث فوز سنة ٢٠٢٣، لكن ما أقدر أؤكد اسم الكتاب", "book_ids": []}
_r, _f, _g, _d = run([Q11, _a11, Q12], prefs={"awards": ["pulitzer"]}, intent="compare")
ok(_f[:1] == [I[23]], f"1.2: «Trust» (2023, English copy) is the first book in facts on the Arabic page ({_f[:4]})")
ok("الأحدث فوزًا هو «Trust»" in _g and "نسخته عندنا بالإنجليزية" in _g, "1.2: the guidance names it, in facts, as an English copy")
_r, _f, _g, _d = run([Q11, _a11, Q12, _a12, Q13], prefs={"awards": ["pulitzer"]}, intent="about_book")
ok(_f[:1] == [I[23]] and "الأحدث فوزًا هو «Trust»" in _g, f"1.3: «دوري زين» keeps the newest and its book ({_f[:3]})")
_r, _f, _g, _d = run([Q11, _a11, Q12, _a12, Q13], prefs={"awards": ["pulitzer"]}, intent="about_book", mentioned_titles=["الثقة"])
ok(_f[:1] == [I[23]] and I[27] not in _f and I[27] not in [b["book_id"] for b in _r.get("books") or []],
   f"replay 1.3: «Trust» read as «الثقة» → still «Trust», never «الثقة بالمعلمين» (no prize) as the book asked ({_f[:3]})")
_r, _f, _g, _d = run(["وش آخر رواية فازت بالبوليتزر عندكم؟"], prefs={"awards": ["pulitzer"]})
ok(_f[:1] == [I[23]], f"fresh: «وش آخر رواية فازت…» → «Trust» first ({_f[:3]})")
_r, _f, _g, _d = run([Q11], prefs={"awards": ["pulitzer"]}, order_by="oldest")
ok(_f[:1] == [I[24]] and "الأحدث فوزًا" not in _g, f"negative: the oldest asked → «عصر البراءة» as before ({_f[:3]})")
_r, _f, _g, _d = run(["عندكم كتب فازت بالبوليتزر؟"], prefs={"awards": ["pulitzer"]})
ok(_f[:1] != [I[23]] and "الأحدث فوزًا" not in _g, f"negative: no order asked → the Arabic copies first as before ({_f[:3]})")

# 2 (7.2, medium; QADFIX36 «ج»)
Q71 = "ولد خالي عمره ١٢ يحب قصص الفضاء والمغامرات، وش يناسبه؟"
_a71 = {"role": "assistant", "content": "يناسبه «جورج وكنز الكون»", "book_ids": [I[20]]}
P72 = {"genres": ["أطفال"], "subgenres": ["مغامرة"], "unsupported_requests": ["الفضاء"], "audience": "children"}
_r, _f, _g, _d = run([Q71, _a71, "شي ثاني له غير جورج؟"], prefs=P72, wants_new_options=True, mentioned_titles=["جورج وكنز الكون"])
ok(I[21] in _f and I[22] in _f, f"7.2: «غير جورج» (the book shown) does not stop the young readers' shelf adventures ({_f})")
ok(I[19] not in _f, "7.2: the «6+» children's book is left out (QADFIX34's rule holds too)")
_r, _f, _g, _d = run([Q71, _a71, "عندك شي ثاني غير جورج وكنز الكون؟ هو بعمر ١٢"], prefs=P72, wants_new_options=True,
                     mentioned_titles=["جورج وكنز الكون"],
                     references=[{"title": "جورج وكنز الكون", "relation": "rejected", "known": True}])
ok(I[21] in _f and I[22] in _f and I[19] not in _f, f"fresh: the shown book ruled out by name → the same ({_f})")
_r, _f, _g, _d = run(["ولد خالي عمره ١٢، عندكم جيم والقطار؟"], prefs=P72, intent="about_book", mentioned_titles=["جيم والقطار"])
ok(not _d.get("ya_kid36"), "negative: a title asked for (not shown) — about that book, no young-shelf list")

# 5 (13.2–13.3, medium; SETS)
_a131 = {"role": "assistant", "content": "سلسلة جورج: …", "book_ids": [I[12], I[13], I[14]]}
_r, _f, _g, _d = run(["سلسلة لوسي وستيفن هوكينج كم كتاب فيها؟", _a131, "كم سعر المجموعة كاملة؟ وكم تقييمها؟"], intent="about_book")
ok(_f[:1] == [I[11]] and "يسأل عن «سلسلة لوسي وستيفن هوكينج (1-6)» نفسها" in _g and "ما لها تقييم واحد" in _g,
   f"13.2: «كم سعر المجموعة كاملة؟» after the series → the set record first, its price, no one rating ({_f[:3]})")
_r, _f, _g, _d = run(["سلسلة جورج كم كتاب فيها؟", _a131, "أقصد سلسلة لوسي وستيفن هوكينغ (1-6) نفسها، المجموعة"], intent="about_book",
                     mentioned_titles=["سلسلة جورج"])
ok(_f[:1] == [I[11]], f"13.3: the set named by its title (هوكينغ / هوكينج alike) → the set record first ({_f[:3]})")
_r, _f, _g, _d = run(["سلسلة جورج كم كتاب فيها؟", _a131, "and how much is the whole set?"], language="en", intent="about_book")
ok(_f[:1] == [I[11]] and "itself: it is one record in facts" in _g, f"fresh (English): «the whole set» after the series → the set ({_f[:3]})")
_r, _f, _g, _d = run(["سلسلة جورج كم كتاب فيها؟", _a131, "كم سعر الجزء الأول؟"], intent="about_book", mentioned_titles=["جورج ومفتاح الكون"])
ok(not _d.get("set_named38") and _f[:1] != [I[11]], f"negative: «كم سعر الجزء الأول؟» → the part, not the set ({_f[:2]})")
_r, _f, _g, _d = run(["عندكم المجموعة القصصية الكاملة لتشيخوف؟"], intent="about_book", mentioned_titles=["المجموعة القصصية لتشيخوف"])
ok(not _d.get("set_named38"), "negative: «المجموعة القصصية» with no series in context is no set of ours")
_r, _f, _g, _d = run(["سلسلة لوسي وستيفن هوكينج كم كتاب فيها؟ ومتى انكتبت؟"], intent="about_book",
                     author_request={"name_as_written": "لوسي وستيفن هوكينج", "full_name": "", "aliases": [], "mode": "works"})
ok(not _d.get("author_gate") and _f[:1] == [I[11]] and {I[12], I[13], I[14], I[15]} <= set(_f[:6]) and "مسجّلة عندنا منفردة" in _g,
   f"replay 13.1: the set's title read as an author → the set, then its parts with their years; no «تقصد لوسي هوكينج؟» ({_f[:6]})")
ok([b["book_id"] for b in A.sets_named38(["عندكم سلسلة لوسي وستيفن هوكينج؟"], {b["book_id"]: b for b in books})] == [I[11]],
   "13: the head of the set's title («سلسلة لوسي وستيفن هوكينج») names it")

# 6 (43.1, medium)
_r, _f, _g, _d = run(["قصة مصورة لبنت عمرها ثلاث تحب القطط"], prefs={"genres": ["أطفال"], "unsupported_requests": ["القطط"], "audience": "children"})
ok(I[16] not in _f and I[18] not in _f, f"43.1: not the 672-page book with no age, not the 9–12 one ({_f[:4]})")
ok(_f[:1] == [I[17]], f"43.1: the 3–6 picture book about a cat ({_f[:3]})")
_r, _f, _g, _d = run(["ولدي عمره أربع سنين يحب القطط"], prefs={"unsupported_requests": ["القطط"], "audience": "children"})
ok(I[16] not in _f and I[17] in _f, f"fresh: a boy of 4 → the same ({_f[:3]})")
_r, _f, _g, _d = run(["بنتي عمرها ثمان تحب حكايات القطط"], prefs={"genres": ["أطفال"], "unsupported_requests": ["القطط"], "audience": "children"})
ok(I[16] in _f, f"negative: a girl of 8 may still get the long book with no recorded age ({_f[:4]})")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
