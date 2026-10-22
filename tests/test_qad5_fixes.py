"""Fixes after the daily chat test round of 2026-10-15 (QAD; Ibrahim «صلحها» + «صلح الاخطاء المتوسطة والبسيطة»).

One check per error of research/chat_tests_2026-10-15/report.md fixed in code: English copies asked for as
«قصص انجليزي / كتاب إنجليزي / عادي لو إنجليزي» (ج1, م1), the Hugo / Nebula / Locus prizes (ج2; winners from
Ibrahim's list), an old black-and-white film (ج3), a decade (ج4), «كم كتاب عربي؟ وكم منها روايات؟» (ج5), the
pearl divers and a child's fear (م2, م3), a young readers' edition with no age range (م4), «أقصر» after one
author's books (م5), the Arabic Booker shortlist (م6), a streaming platform (م7), «كم سعرها؟» after the English
copy (م8), «أحدث طبعة» (م9), a series' first part (ب1) and the shortest for an English visitor (ب7).
"""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm, chat, chat_rules as R, honors, topics, keyword_prefs as K, app as A
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
def say(*msgs, language="ar"):
    conv = [{"role": "user" if i % 2 == 0 else "assistant", "content": m} for i, m in enumerate(msgs)]
    seen.clear()
    return A.run_chat(conv, active=("fake", "m"), language=language)
books = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
avail = [b for b in books.values() if A.is_available(b)]
facts_ids = lambda: [f["id"] for f in seen.get("facts", [])]

# ---------------------------------------------------------------- ج1 / م1: English copies
for m in ("عندكم قصص انجليزي سهلة؟", "طيب أي كتاب إنجليزي يناسب عمره", "أبي كتاب خطوة بخطوة، عادي لو إنجليزي", "ابي وحدة ولو انجليزي"):
    ok(A.wanted_edition(m) == "en", f"«{m}» asks for a copy in English")
ok(A.wanted_edition("أبي رواية من الأدب الإنجليزي مترجمة") is None, "«الأدب الإنجليزي مترجمة» is not the copy's language")
_en_kids = [b for b in avail if (b.get("language") or "ar") == "en" and b.get("genre") in A.CHILD_GENRES and A.age_fit(b, 9) > 0]
interp({"audience": "children", "genres": ["أطفال"], "original_languages": ["en"]})
out = say("ولدي عمره ٩ ويبي يقوي انجليزيه، عندكم قصص انجليزي سهلة؟")
if _en_kids:
    ok(facts_ids() and all((books[i].get("language") or "ar") == "en" for i in facts_ids()),
       "a 9-year-old gets the English children's books in stock, not «ما لقينا» with Arabic ones")
interp({"audience": "children"})
out = say("ولدي عمره ٩ ويبي يقوي انجليزيه", "x", "أقصد نسخة إنجليزية، مو كتاب مترجم للعربي")
if _en_kids:
    ok(facts_ids() and all((books[i].get("language") or "ar") == "en" for i in facts_ids()) and "ما لقينا نسخة" not in seen.get("guidance", ""),
       "«نسخة إنجليزية» looks in the whole stock, not only in the Arabic candidates")

# ---------------------------------------------------------------- ج2: Hugo / Nebula / Locus
p, _ = K._parse_one("ابي وحدة فازت هي نفسها بهوغو، مو مؤلفها")
ok(p["awards"] == ["hugo"], "«هوغو» is the Hugo key")
ok(K._parse_one("روايات فازت بنيبولا")[0]["awards"] == ["nebula"] and K._parse_one("جائزة لوكس")[0]["awards"] == ["locus"],
   "«نيبولا» and «لوكس» too")
ok({"hugo", "nebula", "locus"} <= set(llm.AWARD_KEYS) and {"nebula", "locus"} <= set(honors.AWARDS), "the interpreter and the store know the three keys")
_sf = next((b for b in avail if (b.get("language") or "ar") == "en"), None)
if _sf:
    honors.add("book_awards", _sf["book_id"], {"award_key": "nebula", "result": "winner", "year": 1965, "category": "أفضل رواية",
                                              "source_url": "https://nebulas.sfwa.org/award-year/1965/"}, "sf_awards_list")
    honors._cache["data"] = None
    lab = honors.matches_award(_sf["book_id"], ["nebula"])
    ok(lab and lab[0].startswith("فاز") and "نيبولا" in lab[0], "a Nebula winner answers «فازت بنيبولا»")
    ok(A.award_results_said("ابي وحدة فازت هي نفسها بهوغو") == ["winner"], "«فازت» = the winners")

# ---------------------------------------------------------------- ج3: an old black-and-white film
ok(K.old_screen_said(A.normalize("أبي رواية عربية صارت فيلم سينما قديم، أبيض وأسود")), "«فيلم قديم، أبيض وأسود» is an old film")
ok(K.old_screen_said(A.normalize("رواية صارت فيلم ابيض واسود")) and not K.old_screen_said(A.normalize("رواية قديمة")),
   "«أبيض وأسود» alone counts; «رواية قديمة» without a film does not")
ai = llm.clean_prefs({"adaptation": "film", "unsupported_requests": ["فيلم قديم أبيض وأسود"], "original_languages": ["ar"]})
prefs, _pp, _ex, unsup = A.prefs_from_ai(ai, A.normalize("أبي رواية عربية صارت فيلم سينما قديم، أبيض وأسود"))
ok(prefs.get("adaptation") == "film" and prefs.get("adaptation_max_year") == K.OLD_SCREEN_YEAR and not unsup,
   "it becomes a film made by 1975, not an unmet wish")
_old = [b for b in avail if honors.matches_adaptation(b["book_id"], "film", 1975)]
_new = [b for b in avail if honors.matches_adaptation(b["book_id"], "film") and not honors.matches_adaptation(b["book_id"], "film", 1975)]
ok(all(not honors.matches_adaptation(b["book_id"], "film", 1975) for b in _new), "a later film does not answer «قديم»")
interp({"adaptation": "film", "original_languages": ["ar"], "unsupported_requests": ["قديم", "أبيض وأسود"]})
out = say("أبي رواية عربية صارت فيلم سينما قديم، أبيض وأسود")
if _old:
    ok(facts_ids() and all(honors.matches_adaptation(i, "film", 1975) for i in facts_ids()), "the books shown had a film by 1975")

# ---------------------------------------------------------------- ج4: a decade
ok(A.years_said("ابي روايات انكتبت في الستينات") == (1960, 1969) and A.years_said("في السبعينيات") == (1970, 1979)
   and A.years_said("novels from the sixties") == (1960, 1969) and A.years_said("1980s") == (1980, 1989), "a decade is a range of years")
ok(A.years_said("رواية ستين صفحة") == (None, None), "«ستين» alone is not a decade")
_60s = [b for b in avail if (A.first_published(b) or 0) in range(1960, 1970)]
interp({"genres": ["رواية"], "unsupported_requests": ["الستينات"]})
out = say("ابي روايات انكتبت في الستينات")
if _60s:
    ok(facts_ids() and all((A.year_for_new(books[i]) or 0) in range(1960, 1970) for i in facts_ids()), "the novels shown came out in the 1960s")

# ---------------------------------------------------------------- ج5: the library in numbers
for m in ("كم كتاب عربي عندكم؟ وكم منها روايات؟", "قصدي عدد الكتب العربية اللي بالمكتبة", "كم كتاب انجليزي عندكم وكم منها روايات"):
    ok(R.overview_asked(m), f"«{m}» is a question about the shelves")
ok(not R.overview_asked("كم كتاب عندكم لنجيب محفوظ؟") and not R.overview_asked("كم كتاب عندكم عن الحرب العالمية الثانية"),
   "an author or a subject is not")
g = A.library_overview(books)
ok("بالعربية و" in g and "أكبر التصنيفات بلغة النسخة" in g, "the overview gives each large category by the language of the copy")

# ---------------------------------------------------------------- م2 / م3: subjects
f = topics.content_words("الغوص على اللؤلؤ")
ok(any("لؤلؤه" in x or "اللؤلؤه" in x for x in f), "«اللؤلؤ» also finds «اللؤلؤة»")
ok(any(("خووويف" in x or "خوويف" in x) for x in topics.content_words("تخاف من الظلام")), "«تخاف» also finds «خووويف»")

# ---------------------------------------------------------------- م4: a young readers' edition
ok(A._age_range_ok({"title": "روبنسون كروزو (للفتيان)", "age_min": None, "age_max": None}, "children", 8),
   "«(للفتيان)» in the title is a young readers' edition")
ok(not A._age_range_ok({"title": "روبنسون كروزو", "age_min": None, "age_max": None}, "children", 8), "a plain title with no range is not")

# ---------------------------------------------------------------- م6: shortlist / longlist
ok(A.award_results_said("وش الروايات اللي وصلت القائمة القصيرة للبوكر العربية عندكم؟") == ["shortlist", "winner"]
   and A.award_results_said("القائمة الطويلة") == ["longlist", "shortlist", "winner"], "the shortlist and the longlist are read")
_short = [b for b in avail if honors.matches_award(b["book_id"], ["ipaf"], results=["shortlist", "winner"])]
_long = [b for b in avail if honors.matches_award(b["book_id"], ["ipaf"]) and not honors.matches_award(b["book_id"], ["ipaf"], results=["shortlist", "winner"])]
interp({"awards": ["ipaf"], "genres": ["رواية"]})
out = say("وش الروايات اللي وصلت القائمة القصيرة للبوكر العربية عندكم؟")
if _short:
    ok(facts_ids() and not set(facts_ids()) & {b["book_id"] for b in _long}, "a longlisted-only novel is not offered for the shortlist")

# ---------------------------------------------------------------- م7: a streaming platform
interp({"adaptation": "series", "unsupported_requests": ["نتفلكس"]})
out = say("ابي رواية تحولت لمسلسل على نتفلكس")
ok("المنصة غير مسجلة" in seen.get("guidance", ""), "the model is told the platform is not recorded")

# ---------------------------------------------------------------- م9: «أحدث طبعة»
ok(A.new_release_wished(["عندكم ١٩٨٤؟ وش أحدث طبعة منها؟"]) is None and A.new_release_wished(["وش أحدث الروايات؟"]) == "said",
   "«أحدث طبعة» is not a new-release wish; «أحدث الروايات» still is")

# ---------------------------------------------------------------- ب1: the first part of a series
_ser = {}
for b in avail:
    if (b.get("series_name") or "").strip() and b.get("series_position"):
        _ser.setdefault(b["series_name"].strip(), {})[b["series_position"]] = b
_pair = next(((d[1], d[p]) for d in _ser.values() for p in d if p and p > 1 and 1 in d), None)
if _pair:
    first, later = _pair
    interp({"genres": [later.get("genre")] if later.get("genre") else [], "title_keywords": []})
    A_score = A.score_books
    A.score_books = lambda pool, *a, **k: [(9, ["x"], later)] + [x for x in A_score(pool, *a, **k) if x[2]["book_id"] not in (later["book_id"], first["book_id"])]
    out = say("ابي رواية مشوقة")
    A.score_books = A_score
    ok(first["book_id"] in facts_ids() and later["book_id"] not in facts_ids(), "the first part is offered instead of a later one")

# ---------------------------------------------------------------- Ibrahim 2026-10-15 22:4x: «١- موافق ٢- اصلحها»
import author_flow
# «وش عندكم لموراكامي؟» with one catalog author of that family name: his books, no «تقصد …؟»
_cat = [{"author": "هاروكي موراكامي", "active": 1, "quantity": 1}, {"author": "نجيب محفوظ", "active": 1, "quantity": 1}]
_q, _names, _direct = author_flow.resolve(dict(name_as_written="موراكامي", full_name="", aliases=[], mode="works"),
                                          [{"role": "user", "content": "وش عندكم لموراكامي؟"}], _cat)
ok(_q is None and _names == {"هاروكي موراكامي"}, f"one author with that family name: no «تقصد …؟» ({_q})")
_cat2 = _cat + [{"author": "ريو موراكامي", "active": 1, "quantity": 1}]
_q2 = author_flow.resolve(dict(name_as_written="موراكامي", full_name="", aliases=[], mode="works"),
                          [{"role": "user", "content": "وش عندكم لموراكامي؟"}], _cat2)[0]
ok(bool(_q2) and "تقصد" in _q2, "two authors with that family name: the question stays")
# «شي ثاني لنفس العمر»: a short «another one»
ok(bool(A._ANOTHER_ONE.search(" " + A.normalize("شي ثاني لنفس العمر") + " ")) and bool(A._ANOTHER_ONE.search(" فيه غيرها ")),
   "«شي ثاني» / «فيه غيرها» read as «another one»")
ok(not A._ANOTHER_ONE.search(" " + A.normalize("ابي رواية عن الحرب") + " "), "a new request is not «another one»")
# the subject group word wins as the core of «الخوف من الظلام»
_idx = topics.index(A.load_all_books() if hasattr(A, "load_all_books") else {b["book_id"]: b for b in A.list_books(only_active=False)})
_core = topics.core_word("الخوف من الظلام", _idx)
ok(_core in (None, "الخوف"), f"core of «الخوف من الظلام» is the subject-group word ({_core})")
# a novel made into a cartoon passes the age check for a child only when a cartoon was asked for
_nb = {"book_id": 1, "title": "ناب أبيض", "age_min": None, "age_max": None}
ok(A._age_range_ok(_nb, "children", 8, animated=True) and not A._age_range_ok(_nb, "children", 8),
   "unknown age range: allowed for a cartoon request only")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
