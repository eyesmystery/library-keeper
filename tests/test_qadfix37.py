"""[QADFIX37] Ibrahim's answer «ب» (2026-11-13) on QADFIX35's question 2 (QAD 2026-11-12 cases 31.1 / 49.2): a grown-up writing in
English asks for a mystery / crime novel («anything here that became a miniseries? dark crime vibe», "something in English only pls");
the English ones of that kind stand on the young readers' shelf (One of Us Is Lying, We Were Liars) and the adult shelf has one (Inferno).
When the adult books of the kind asked in the copy language wanted are fewer than two, the young readers' shelf books of that kind in
that language come after them, and the reply says they are from that shelf. Generalized to any kind and any copy language asked.
Round wording + fresh wording + negatives (children / teenagers keep their own paths, two adult books of the kind, adult books asked,
the content refused, the Arabic page with no copy language)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()

# ---------------------------------------------------------------- word readers
ok(A.kind_families37(["غموض"]) == {"mystery"} and A.kind_families37(["جريمة"]) == {"mystery"}
   and A.kind_families37(["بوليسي"]) == {"mystery"}, "the mystery / crime / detective wish is one family")
ok(A.kind_families37(["رومانسية"]) == {"romance"} and A.kind_families37(["ديستوبيا"]) == {"dystopia"}
   and A.kind_families37(["إثارة"]) == {"thriller"}, "fresh: romance, dystopia, thriller families")
ok(not A.kind_families37(["رواية"]) and not A.kind_families37(["قصص"]), "«رواية» / «قصص» name no kind")
_yb = {"title": "One of Us Is Lying", "keywords": '["YA", "Mystery", "Thriller", "High-School", "Murder"]', "subgenres": None,
       "subjects": "", "summary": "سايمون يموت أمام أربعة لكل منهم سر. من القاتل. تشويق لليافعين", "hook": ""}
_h = A.young_kind37(_yb, {}, {"subgenres": ["غموض"]}, A.kind_families37(["غموض"]))
ok(_h is not None and _h[1] == 0 and _h[2] >= 2, f"5075's record (keywords Mystery, Murder) shows the kind ({_h})")
ok(A.young_kind37(dict(_yb, keywords='["YA", "Romance"]', summary="قصة حب في باريس", title="Anna"), {}, {"subgenres": ["غموض"]},
                  {"mystery"}) is None, "a YA romance is no mystery")
ok(A.young_kind37(dict(_yb, keywords='["YA"]', summary="فتاة غامضة تصل المدينة", title="X"), {}, {"subgenres": ["غموض"]},
                  {"mystery"}) is None, "«غامضة» alone in a summary proves nothing")
_ao = lambda t: bool(A._ADULT_ONLY37.search(" " + " ".join(A.normalize(t).split()) + " "))
ok(_ao("an adult mystery in English please") and _ao("mystery in english, not YA") and _ao("ابي رواية غموض للكبار بالانجليزي")
   and not _ao("recomend me a good mistery novel plz") and not _ao("any young adult mysteries in English?"),
   "a grown-up who asks for adult books / refuses YA is read; «young adult» is not «adult»")

# ---------------------------------------------------------------- whole path on synthetic books
base = [dict(b) for b in A.list_books(only_active=False)][:40]
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
REPLY = {"text": "ok", "ids": None}
def respond(conv, facts_json, guidance, *a, **k):
    facts = json.loads(facts_json); captured.append({"facts": facts, "guidance": guidance})
    return {"reply": REPLY["text"], "show_book_ids": REPLY["ids"] if REPLY["ids"] is not None else [f["id"] for f in facts][:3],
            "suggestions": [], "unanswered": ""}
A.chat.respond = respond
EN = dict(language="en")
YA = dict(genre="يافعين", language="en")
SPEC = [
    dict(EN, title="Inferno", subgenres='["إثارة", "غموض", "مغامرة"]', summary="أستاذ الرموز يتتبع ألغازًا في فن النهضة"),           # 0
    dict(YA, title="One of Us Is Lying", keywords='["YA", "Mystery", "Thriller", "Murder"]', summary="من القاتل بين أربعة طلاب. تشويق لليافعين"),  # 1
    dict(YA, title="We Were Liars", keywords='["YA", "Thriller", "Mystery"]', summary="إصابة غامضة على جزيرة. تشويق لليافعين"),        # 2
    dict(YA, title="The Fault in Our Stars", keywords='["YA", "Romance"]', summary="قصة حب بين مراهقين"),                              # 3
    dict(YA, title="Anna and the French Kiss", keywords='["YA", "Romance"]', summary="قصة حب في باريس"),                               # 4
    dict(EN, title="Pride and Prejudice", subgenres='["رومانسية", "كلاسيكيات"]', summary="إليزابيث بينيت والسيد دارسي"),             # 5
    dict(EN, title="Me Before You", subgenres='["رومانسية"]', summary="قصة حب مؤثرة"),                                                # 6
    dict(title="جريمة في القطار", subgenres='["غموض", "جريمة"]', summary="محقق يحل جريمة قتل في قطار"),                               # 7
    dict(genre="يافعين", title="لغز البيت المهجور", keywords='["لغز"]', summary="أصدقاء يحلون لغز البيت"),                            # 8
    dict(EN, title="Great Expectations", subgenres='["كلاسيكيات"]', summary="بيب اليتيم وأحلامه"),                                   # 9
    dict(YA, title="Island of Secrets", keywords='["YA", "Mystery"]', summary="جزيرة فيها قتل ودماء وأسرار"),                        # 10
    dict(genre="أطفال", language="en", title="Nate the Great", keywords='["Mystery", "Detective"]', summary="محقق صغير"),               # 11
    dict(EN, title="1984", subgenres='["ديستوبيا"]', summary="الأخ الأكبر يراقبك"),                                                   # 12
    dict(YA, title="The Maze Runner", keywords='["YA", "Dystopia", "Survival"]', summary="فتى في متاهة", series_name="The Maze Runner",
         series_position=1),                                                                                                       # 13
    dict(YA, title="Legend", keywords='["YA", "Dystopia"]', summary="جمهورية عسكرية في المستقبل"),                                    # 14
    dict(YA, title="The Death Cure", keywords='["YA", "Dystopia"]', summary="الجزء الثالث", series_name="The Maze Runner",
         series_position=3),                                                                                                       # 15
    dict(EN, title="Gone Girl", subgenres='["جريمة", "غموض", "إثارة نفسية"]', summary="زوجة تختفي يوم ذكرى زواجها"),                           # 16
] + [dict(title=f"رواية عادية {n}", summary="رواية عن الحياة اليومية") for n in range(8)]                                             # 17–24
books = []
for b, sp in zip([dict(x) for x in base], SPEC):
    b.update(quantity=3, active=1, author="كاتب " + str(b["book_id"]), original_year=None, summary="", hook="", keywords="", subjects="",
             subgenres=None, series_name=None, series_position=None, goodreads_url=None, language="ar", age_min=None, age_max=None,
             page_count=300, genre="رواية", publication_year=2015, setting=None)
    b.update(sp)
    books.append(b)
A.list_books = lambda *a, **k: books
A.topics._cache.update(key=None)
I = {k: books[k]["book_id"] for k in range(len(SPEC))}
ADAPT = {I[1], I[2]}
A.honors.matches_adaptation = lambda bid, kind, *a, **k: (["مسلسل"] if bid in ADAPT and kind in ("series", "screen", "tv") else [])
books[16]["quantity"] = 0   # «Gone Girl» (an adult English crime novel) is in stock only for one check

def run(msgs, reply="ok", ids=None, language="ar", **it):
    CUR.clear(); CUR.update(it)
    captured.clear(); REPLY.update(text=reply, ids=ids)
    m = [{"role": "user", "content": x} if isinstance(x, str) else x for x in msgs]
    r = A._run_chat_once(m, active=("synthetic", "no-network"), language=language)
    c = captured[0] if captured else {"facts": [], "guidance": ""}
    return r, [f["id"] for f in c["facts"]], c["guidance"], [b["book_id"] for b in r.get("books") or []]
YOUNG = {I[1], I[2], I[3], I[4], I[8], I[10], I[11], I[13], I[14], I[15]}
G37 = "young readers' shelf (written for teenagers)"
G37AR = "(مكتوبة لليافعين)"

# 49.1 / 49.2 (round wording)
P49 = {"genres": ["رواية"], "subgenres": ["غموض"]}
_r, _f, _g, _c = run(["recomend me a good mistery novel plz"], prefs=P49, language="en")
ok(_f[:1] == [I[0]] and I[1] in _f[:3], f"49.1: Inferno first, then the young readers' shelf mysteries in English ({_f[:5]})")
ok(I[1] in _f and (I[7] not in _f or _f.index(I[1]) < _f.index(I[7])), "49.1: the English YA mystery before the Arabic adult one")
ok(G37 in _g and I[1] in _c, f"49.1: the guidance says they are from the young readers' shelf; card shown ({_c})")
_a49 = {"role": "assistant", "content": "Inferno by Dan Brown… «جريمة في القطار», our copy is in Arabic.", "book_ids": [I[0], I[7]]}
_r, _f, _g, _c = run(["recomend me a good mistery novel plz", _a49, "something in English only pls"], prefs=P49, language="en")
ok(I[1] in _f and I[2] in _f, f"49.2: «something in English only» brings One of Us Is Lying and We Were Liars ({_f})")
ok(not set(_f) & {I[3], I[4], I[8], I[11]} and I[2] in _f and (I[9] not in _f or _f.index(I[9]) > _f.index(I[2])),
   "49.2: no YA romance, no Arabic YA, no English children's book; a non-mystery adult novel not before them")
ok({I[1], I[2]} <= set(_c) and G37 in _g, f"49.2: their cards are shown and the reply is told where they stand ({_c})")
_ya49 = (_r.get("_debug") or {}).get("ya_adult37")
ok(_ya49 and _ya49[0] == I[1], f"49.2: the clearest mystery of the shelf first ({_ya49})")

# 31.1 / 31.2 (round wording): a miniseries, crime
P31 = {"genres": ["رواية"], "subgenres": ["جريمة"], "moods_include": ["dark"], "adaptation": "series"}
_r, _f, _g, _c = run(["anything here that became a miniseries? dark crime vibe"], prefs=P31, language="en")
ok(I[1] in _f and I[2] in _f, f"31.1: the English YA crime books adapted into series ({_f})")
ok(I[10] not in _f, "31.1: a YA mystery not adapted for the screen does not come (the screen wish holds)")
ok(set(_c) & {I[1], I[2]} and G37 in _g, f"31.1: cards and the shelf said ({_c})")
_a31 = {"role": "assistant", "content": "One of Us Is Lying…", "book_ids": []}
_r, _f, _g, _c = run(["anything here that became a miniseries? dark crime vibe", _a31, "crime please, not horror, and in English"],
                     prefs=dict(P31, genres=[], subgenres_exclude=["رعب"], avoid_content=["horror"]), language="en")
ok(I[1] in _f and G37 in _g, f"31.2: «crime please, not horror, and in English» ({_f})")

# fresh wordings
_r, _f, _g, _c = run(["Any good detective novels in English? I love a proper whodunit"], prefs={"genres": ["رواية"], "subgenres": ["بوليسي"]},
                     language="en")
ok(I[1] in _f and G37 in _g, f"fresh: «detective novels in English» → the YA mysteries ({_f[:5]})")
_r, _f, _g, _c = run(["Any good detective novels in English? I love a proper whodunit"],
                     prefs={"genres": ["رواية"], "subgenres": ["تحقيقات", "من قتل"]}, language="en")
ok(I[1] in _f and G37 in _g, f"replay 3761: the same read as «تحقيقات»، «من قتل» (a detective story is a mystery) ({_f[:5]})")
_a49b = dict(_a49, book_ids=[I[0], I[1]])
_r, _f, _g, _c = run(["recomend me a good mistery novel plz", _a49b, "something in English only pls"], prefs=P49, language="en")
_ya = (_r.get("_debug") or {}).get("ya_adult37") or []
ok(I[1] not in _ya and I[2] in _ya, f"a YA book shown on the turn before is not brought in again; the next one comes ({_ya})")
_r, _f, _g, _c = run(["any dystopian novels in English?"], prefs={"genres": ["رواية"], "subgenres": ["ديستوبيا"]}, language="en")
ok(_f[:1] == [I[12]] and I[13] in _f and I[14] in _f, f"fresh (another kind): 1984 first, then The Maze Runner and Legend ({_f[:5]})")
ok(I[15] not in _f, "fresh: one book of a series (not part 3 of The Maze Runner)")
_r, _f, _g, _c = run(["ابي رواية غموض بالإنجليزي"], prefs=P49)
ok(I[1] in _f and G37AR in _g and "من رف اليافعين" in str(_r.get("books")) + str(_g),
   f"fresh (the Arabic page, English copies asked): the YA mysteries, said in Arabic ({_f[:5]})")
_r, _f, _g, _c = run(["a mystery novel in English, nothing violent please"], prefs=dict(P49, avoid_content=["violence"]), language="en")
ok(I[1] in _f and I[10] not in _f, f"the content refused holds: «Island of Secrets» (killing and blood in its summary) out ({_f[:5]})")

# negatives
_r, _f, _g, _c = run(["any romance novels in English?"], prefs={"genres": ["رواية"], "subgenres": ["رومانسية"]}, language="en")
ok(not set(_f) & {I[3], I[4]} and G37 not in _g, f"two adult English romances in stock: no YA romance ({_f[:5]})")
books[16]["quantity"] = 3
_r, _f, _g, _c = run(["something in English, a mystery please"], prefs=P49, language="en")
ok(not set(_f) & YOUNG and G37 not in _g, f"with a second adult English mystery in stock («Gone Girl»), the adult shelf only ({_f[:5]})")
books[16]["quantity"] = 0
_r, _f, _g, _c = run(["an adult mystery in English please, not YA"], prefs=P49, language="en")
ok(not set(_f) & YOUNG and G37 not in _g, f"adult books asked / YA refused: none from that shelf ({_f[:5]})")
_r, _f, _g, _c = run(["ابي رواية غموض"], prefs=P49)
ok(not (set(_f) & YOUNG) and G37AR not in _g, f"the Arabic page, no copy language asked: as before ({_f[:5]})")
_r, _f, _g, _c = run(["my son is 10 and likes mystery stories, in English"], prefs=dict(P49, audience="children"), language="en")
ok(G37 not in _g and I[0] not in _f, f"a child of 10: the children's path, never Inferno, no QADFIX37 line ({_f[:5]})")
_r, _f, _g, _c = run(["my daughter is 15 and loves mysteries, English books please"], prefs=dict(P49, audience="young_adult"), language="en")
ok(G37 not in _g and I[1] in _f, f"a girl of 15: the young path as before ({_f[:5]})")
_r, _f, _g, _c = run(["any history books in English?"], prefs={"genres": ["تاريخ"]}, language="en")
ok(not set(_f) & YOUNG and G37 not in _g, "a non-fiction shelf asked: nothing from the young readers' shelf")
ok("QADFIX37" in src and "or (f[\"id\"] in {b[\"book_id\"] for _r, b in ya_adult37}" in src, "source: the card check keeps them")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
