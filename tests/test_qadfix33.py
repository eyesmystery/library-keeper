"""[QADFIX33] The errors of the QAD round 2026-11-10 (Ibrahim's standing word of 2026-11-06: QAD fixes go ahead): grave 5.1 «ولدي ١٤ …
التنانين والسحر، بس … قتل ولا دم» (the plural «تنانين», the young readers' shelf), 25.1 «بنت أخوي ١٦ تحب الجرايم الغامضة وتحليل الأدلة»,
33.1–33.2 «الغونكور، وش أحدث رواية فائزة؟» / «أبيها كلها», 49.2 «وأغلى واحد؟» after «أرخص ديوان»; medium 1.1–1.2 (series parts by number),
11.1 (a title said with its author), 32.2 («سمّها كلها»), re-run 11-09 16.2 («أقصر من كذا» for a grown-up); minor 8.1, 10.1, 13.2, 21.2,
24.2, 31.1, 40.1, 50.1. Each check uses the round's own wording and a fresh rewording."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import chat_rules as R
import topics as T
from keyword_prefs import normalize
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()
src_t = open(T.__file__, encoding="utf-8").read()


def bk(i, title, author="مؤلف", genre="رواية", q=2, **kw):
    return dict({"book_id": i, "title": title, "author": author, "genre": genre, "quantity": q, "active": 1, "language": "ar",
                 "series_name": None, "series_position": None}, **kw)


# --- 1 (case 5.1, grave): «التنانين» finds «تنين» in titles, summaries and keywords
_forms = set().union(*T.content_words("التنانين"))
ok("تنين" in _forms and "dragon" in _forms, "5.1: «التنانين» (broken plural) carries «تنين» and «dragon»")
_TB = {1: bk(1, "التنين الأبيض وشخصيات منسية أخرى", genre="يافعين"), 2: bk(2, "جيم الزر", genre="أطفال", summary="رحلة لإنقاذ أميرة من مدينة التنانين"),
       3: bk(3, "المعلم ومرغريتا", summary="الشيطان يزور موسكو"), 4: bk(4, "Wings of Fire", language="en", keywords='["Dragons", "Fantasy"]')}
_TB.update({i: bk(i, f"كتاب عام {i}") for i in range(10, 60)})
_idx = T.index(_TB)
ok(T.match(["تنانين"], 1, _idx)[1] and T.match(["تنانين"], 2, _idx)[0] and not T.match(["تنانين"], 3, _idx)[0],
   "5.1: «تنانين» finds «التنين الأبيض» (title) and «مدينة التنانين» (summary), not «المعلم ومرغريتا»")
ok(T.match(["التنين"], 2, _idx)[0] and T.match(["تنانين"], 4, _idx)[0], "fresh: the singular «التنين» finds the plural; «تنانين» finds Dragons")
_cr = set().union(*T.content_words("الجرايم"))
ok("جريمه" in _cr and "mystery" in _cr and "لغز" in _cr, "25.1: «الجرايم» carries «جريمة»، mystery، «لغز»")
ok(not ({"جريمه", "mystery"} & set().union(*T.content_words("تحقيق الأهداف"))), "«تحقيق الأهداف» (achieving) is no crime subject")
ok(src_t.index('("جريمه", "الجريمه"') < src_t.index('("الغاز", "لغز"'), "the crime group comes before the riddle group («لغز» keeps its own)")

# --- 2 (cases 5.1, 25.1, grave): the kind of story wished, found in a young-shelf book's own record
ok(A.young_kinds33(["سحر"]) == {"fantasy"} and A.young_kinds33(["تنانين"]) == {"fantasy"}, "5.1: «سحر»، «تنانين» are fantasy")
ok(A.young_kinds33(["جرائم", "غموض"]) == {"mystery"} and A.young_kinds33(["جريمة"]) == {"mystery"}, "25.1: «جرائم / غموض» are a mystery")
ok(A.young_kinds33(["الحكايات الخيالية والعوالم السحرية"]) == {"fantasy"} and A.young_kinds33(["قصص بوليسية"]) == {"mystery"},
   "fresh: «الحكايات الخيالية»، «قصص بوليسية»")
ok(A.young_kinds33(["الصداقة", "رواية واقعية"]) == set(), "a friendship or a realistic novel is neither")
ok(A.young_kind_word33({"title": "التنين الأبيض", "keywords": '["تنين"]'}, {"fantasy"})[:2] == ("التنين", 0),
   "5.1: «التنين الأبيض» — the word in the title, strength 0")
ok(A.young_kind_word33({"title": "One of Us Is Lying", "keywords": '["YA", "Mystery", "Murder"]', "summary": "من القاتل"}, {"mystery"})[1:] == (0, 3),
   "25.1: «One of Us Is Lying» — Mystery in its keywords, three such words")
ok(A.young_kind_word33({"title": "سكوت بيلجريم 1", "summary": "كوميديا شبابية"}, {"mystery"}) is None,
   "whole words only: «بيلجريم» is no «جريمة»")
ok(A.young_kind_word33({"title": "We Were Liars", "summary": "تعود بعد إصابة غامضة"}, {"mystery"})[1] == 2,
   "a summary that only says «غامضة» is the weakest sign")
ok(A.avoided_in_text33({"summary": "قصة مصورة فيها استغلال وعنف"}, ("violence",)) and not A.avoided_in_text33({"summary": "بكل عنفوان الشباب"}, ("violence",))
   and not A.avoided_in_text33({"summary": "فيها عنف"}, ()), "a young-shelf summary that speaks of violence is out when violence is refused («عنفوان» is not)")
ok("young_fit33 = [(r, b) for r, b, _k in _yf33[:6]]" in src and "or avoided_in_text33(b, _avoid30) or (reader_age is not None and age_fit(b, reader_age) < 0)" in src
   and "(_kid33 and not age_covers32(b, reader_age))" in src and 'ya_added30 = [x for x in ya_added30 if x[2]["book_id"] not in _yid33] + [(0.5, r, b) for r, b in young_fit33]' in src,
   "young-shelf fits: the age, the content to avoid, QADFIX32b's age cover for the children's shelf; they live through the later filters")
ok("    if young_fit33:\n        # QADFIX33 #1 / #2 (cases 5.1, 25.1): after the subject search" in src, "they come first again after the subject search")
ok("and (_yas30 or reader_age is None or 12 <= reader_age <= 17))" in src and "if reader_age is not None and 12 <= reader_age <= 15:" in src,
   "25.1: a reader of 16–17 is on the young-reader path (QADFIX32b's children's shelf stays 12–15)")
ok(A.crime_side33("تحليل الأدلة") and A.crime_side33("كشف القاتل") and A.crime_side33("حل الألغاز"), "25.1: «تحليل الأدلة»; fresh «كشف القاتل»، «حل الألغاز»")
ok(not A.crime_side33("تحليل البيانات") and not A.crime_side33("تحليل") and not A.crime_side33("تحليل نفسي"), "«تحليل البيانات»، «تحليل» alone are no crime story")
_n = lambda q: " ".join(normalize(q).split())
ok('elif pprefs.get("audience") in ("children", "young_adult") and any(' in src and A._GORE_NOT.search(_n("بس أمه ما تبي يكون فيها قتل ولا دم"))
   and A._BLOOD32.search(_n("بس أمه ما تبي يكون فيها قتل ولا دم")), "5.2: blood refused the message before holds for a young reader (horror out)")

# --- 3 (cases 33.1–33.2, grave; 32.2 medium): «أحدث» is an order, «كلها» the whole list
ok(R.newest_asked33("الغونكور، وش أحدث رواية فائزة فيها عندكم؟") and not R.recent_filter_said33("الغونكور، وش أحدث رواية فائزة فيها عندكم؟"),
   "33.1: «أحدث رواية فائزة» is the newest first, no span of recent years")
ok(R.newest_asked33("وش آخر رواية فازت بالبوكر عندكم؟") and R.newest_asked33("which is the latest Booker winner you have?"), "fresh: «آخر رواية فازت»، latest")
ok(R.recent_filter_said33("أبي رواية فازت بجائزة حديثًا") and R.recent_filter_said33("جوائز السنوات الأخيرة") and R.recent_filter_said33("won recently"),
   "«حديثًا»، «السنوات الأخيرة»، recently are a span of recent years")
ok(R.list_all_asked33("لا أبيها كلها، كل الفائزة بالغونكور عندكم") and R.list_all_asked33("سمّها كلها لو سمحت") and R.list_all_asked33("list them all please"),
   "33.2 / 32.2: «أبيها كلها»، «سمّها كلها»، list them all")
ok(not R.list_all_asked33("وش أحدث رواية فائزة؟") and not R.newest_asked33("رواية حديثة فازت"), "negative: the newest is no list; «حديثة» is no order")
ok("if prefs.get(\"awards\") and not prefs.get(\"award_year\") and wants_recent(user_messages[-2:]) and _span33:" in src
   and "if _list_all33 and not chat_rules.recent_filter_said33(user_messages[-1]):" in src,
   "a span only from the span words; «كلها» lifts a span carried from the message before")
ok("الزائر سأل عن الأحدث: الكتب المتوفرة اللي فازت هي نفسها ضمن طلبه" in src and "الزائر طلب القائمة كلها:" in src
   and '.replace("ثم اذكر بعضها أمثلة واعرض أن تذكر المزيد", "ثم سمّها كلها كما يلي")' in src,
   "the guidance orders the winners by the year they won; with the whole list asked, every one is named")
ok('"result") == "winner"' in src and "def own_win_year33(book_id, keys):" in src, "the year is the book's own win, not its author's prize")
ok("هذا هو العدد الصحيح للقائمة كلها" in src and "and own_win_year33(b[\"book_id\"], prefs[\"awards\"])):" in src,
   "replay 33.2: the whole list counts the winners shown the turn before too (seven, not five)")
ok('_skip33 = bool(it.get("list_all33") and len(it.get("own_winners33") or ()) >= len(_lib_won))' in src
   and 'if len(_lib_won) > len(_kinds["book"]) and not _skip33 and _n_people > 1 and not _women27:' in src,
   "replay 3 of fresh 333.2: no second «within your wishes» count beside the whole list")
ok("الأحدث فوزًا هو «" in src and "لكنه فاز بالجائزة نفسها فهو جواب سؤاله" in src,
   "replay 33.1: the newest is named («حياة متعجلة» 2022, on the autobiography shelf, is still the answer)")

# --- 4 (case 49.2, grave): the dearest after the cheapest is over the whole stock of the kind
ok(not R.among_shown_said("وأغلى واحد؟") and R.stock_order_asked("وأغلى واحد؟") == "dearest", "49.2: «وأغلى واحد؟» is no question among the shown")
ok(R.stock_order_asked("أبي أوفر، وش أرخص ديوان شعر عندكم؟") == "cheapest", "49.1: «وش أرخص ديوان شعر عندكم؟» is an order over the stock")
ok(R.stock_order_asked("وأطول واحد؟") == "longest" and R.stock_order_asked("وأنحف ديوان؟") == "shortest" and R.stock_order_asked("والأرخص؟") == "cheapest",
   "fresh: «وأطول واحد؟»، «وأنحف ديوان؟»، «والأرخص؟»")
ok(R.among_shown_said("أيهم أغلى؟") and R.among_shown_said("مين فيهم أقصر؟") and R.among_shown_said("وش أرخصها؟")
   and R.among_shown_said("Which of those is the shortest?") and R.stock_order_asked("أيهم أرخص؟") is None,
   "negative: «أيهم»، «مين فيهم»، «أرخصها»، which of those stay among the shown (QADFIX14 م4/م5, QADFIX15)")
ok(R.stock_order_asked("أقصر شوي") is None and R.stock_order_asked("كتاب عن الشعر") is None, "«أقصر شوي» (shorter) and no superlative are no stock order")
ok('it["stock_order33"] = _stock33' in src and "if (_dear30 or it.get(\"stock_order33\")) and not _shown_only:" in src
   and 'if it.get("about_shown") and last_batch and not it.get("stock_order33"):' in src,
   "the model's about_shown gives way; the whole stock of the kind is ordered (the longest too)")
ok('("(حسب أسعارنا لكل الكتب المتوفرة اللي تناسبه)" if _all33 else "(بين الكتب اللي عُرضت له قبل، كما سأل)")' in src,
   "«لكل الكتب المتوفرة» is said only when the order ran over them")

# --- 5 (case 10.1, minor): an English list, one title per numbered line
_F = [{"id": i, "available": True} for i in (1, 2, 3, 4, 5)]
_B5 = {1: bk(1, "A Game of Thrones"), 2: bk(2, "A Clash of Kings"), 3: bk(3, "A Storm of Swords"), 4: bk(4, "A Feast for Crows"),
       5: bk(5, "A Dance With Dragons")}
_r = "We have five parts of A Song of Ice and Fire in stock:\n1. A Game of Thrones\n2. A Clash of Kings\n3. A Storm of Swords\n4. A Feast for Crows\n5. A Dance With Dragons"
ok(A.five_list32(_F, _B5, [], _r) == [1, 2, 3, 4, 5], "10.1: five numbered lines, «five» said → five cards")
ok(A.five_list32(_F, _B5, [], "Our parts:\n- A Game of Thrones\n- A Clash of Kings\n- A Storm of Swords\n- A Feast for Crows\n- A Dance With Dragons\nall five here") != [],
   "fresh: a bulleted list")
ok(A.five_list32(_F, _B5, [], "\n".join(_r.splitlines()[:5])) == [], "negative: four lines are no complete list of five")
ok(A.five_list32(_F, _B5, [], "We have five parts in stock: 1. A Game of Thrones; 2. A Clash of Kings; 3. A Storm of Swords; 4. A Feast for Crows;"
                 " 5. A Dance With Dragons.") == [1, 2, 3, 4, 5], "replay 10.1: the numbered list on one line («1. …; 2. …») → five cards")
ok(A.five_list32(_F, _B5, [], "We have five parts in stock: 1. A Game of Thrones; 2. A Clash of Kings; 3. A Storm of Swords; 4. A Feast for Crows;"
                 " 5. A Dance With Dragons. Which would you like to explore?") == [1, 2, 3, 4, 5],
   "replay 2 of 10.1: a question after the last title («… Dragons. Which would you like…») → five cards")

# --- 6 (cases 1.1–1.2, medium): the parts of a series named by their number
_J = [bk(1104, "جهينة ج1"), bk(1113, "جهينة ج2"), bk(1112, "جهينة ج3")]
ok(sorted(A.series_parts_named33("إيه، ثلاثية جهينة لإسلام عبد الله كاملة ومتوفرة عندنا: الجزء الأول، والجزء الثاني، والجزء الثالث.", _J)) == [1104, 1112, 1113],
   "1.1: «ثلاثية جهينة … الجزء الأول، والجزء الثاني، والجزء الثالث» → three cards")
ok(A.series_parts_named33("ابدأ بجهينة ج1 لإسلام عبد الله، لأنه الجزء الأول من الثلاثية.", _J) == [1104], "1.2: «ابدأ بجهينة ج1» → its card")
ok(A.series_parts_named33("ابدأ بأول جزء من جهينة", _J) == [1104] and A.series_parts_named33("ابدأ بالجزء الأول", _J) == [],
   "fresh: «أول جزء من جهينة»; negative: a part number without the series name")
ok('ids = [i for i in ids if not (reason_by_id[i][1] == "asked" and not _named_in_reply(i) and i not in _sp33' in src,
   "«ما عندي عدد صفحاته» does not drop the part the reply named")

# --- 7 (case 31.1, minor): the number of books asked
ok(R.books_count_asked33("ناشر الكرمة، وش أحدث ثلاث كتب منه عندكم؟") == 3, "31.1: «أحدث ثلاث كتب» → 3")
ok(R.books_count_asked33("رشحي لي كتابين عن البحر") == 2 and R.books_count_asked33("أبي ٣ روايات قصيرة") == 3
   and R.books_count_asked33("give me three books on Rome") == 3, "fresh: «كتابين»، «٣ روايات»، three books")
ok(R.books_count_asked33("ثلاثية جهينة كاملة؟") is None and R.books_count_asked33("أبي كتاب عن البحر") is None, "negative: «ثلاثية»، «كتاب»")
ok('_cnt33 = chat_rules.books_count_asked33(user_messages[-1]) if it.get("intent") == "recommend" else None' in src, "the count is said in the guidance")

# --- 8 (case 11.1, medium; 49.2 class): a title said with its author, and «أطول» of two books named
ok(('_not33 = ([b for b in _hits if not (b.get("author") and author_matches(_said_by, b.get("author")))]' in src
    # QADFIX34 #9: the same author in other letters («غوركي» / «جوركي») is the author named too
    or '_not33 = ([b for b in _hits if not (b.get("author") and (author_matches(_said_by, b.get("author")) or (' in src)
   and "if title_author_said(_mt, user_messages[-1], all_books) else [])" in src,
   "11.1: «الأم لغوركي» — a copy of «الأم» with no author (5167) is not the book asked (the author said in the latest message:"
   " «بنفس الاسم لكاتب غيره» after «الجزيرة حقت هكسلي» keeps the other author's copy)")
ok("        _longest27 = None\n        it[\"compare_named31\"] = True" in src, "11.1: «أيهم أطول: … ولا …؟» is the comparison, not the stock's longest")

# --- 9 (cases 13.2, 21.2, 24.2, 50.1, minor): cards the reply does not name
ok('debug["same_title_other33"] = _drop33' in src, "13.2: Huxley's «الجزيرة» card goes when the reply names Jónasson for his «الجزيرة»")
ok('_cmp_names33 = list(it["mentioned_titles"]) + ([v for v in it.get("title_variants") or [] if v not in it["mentioned_titles"]]' in src
   and A.compared_title("something shorter than Foundation, a standalone", "Foundation"), "21.2: shorter than Foundation read through the title's variants")
ok("def _k33(s):" in src and '_tail33 = _k33(re.split(r"\\s*[:：]\\s*", all_books[i].get("title") or "", maxsplit=1)[-1])' in src,
   "24.2: «كيف أكون محترمًا» names «سِلْسِلَة القِيَم الإنْسَانِيَّة: كَيْفَ أكُون مُحْتَرَمًا» (diacritics out, the title after «:»)")
ok("if _k in _subj33 and _k not in _qk33" in src, "50.1: a card titled as the subject itself («الثقوب السوداء») is named only when quoted")
ok('return bool(_k) and bool(re.search(r"(?:^| )[وبلف]?" + re.escape(_k) + r"(?: |$)", _rk31))' in src,
   "replay 50.1: «… وتاريخ موجز للزمان» names the card (a title may carry «و / ب / ل»)")

# --- 10 (case 8.1, minor; 40.1; re-run 16.2)
ok('it["nobel_question33"] = True' in src and 'it["nobel_not33"] = _arec.get("author_id")' in src and "وما عندنا من كتبه شيء متوفر الآن" in src,
   "8.1: «عبدالرحمن منيف أخذ جائزة نوبل؟» — a reading question; «not in our list» with none of his books in stock")
ok('prefs["genres"] = set(prefs["genres"]) | {"سيرة ذاتية"}' in src and 'it["bio_shelf33"] = True' in src,
   "40.1: «سير الصحابة» keeps the biography shelf («الوعد الحق») beside «دين»")
ok("_grown33 = (pprefs.get(\"audience\") not in (\"children\", \"young_adult\") and not _lk33 & CHILD_GENRES)" in src
   and "and 1 <= len(_lg28) <= 2:" in src, "16.2: a grown-up's «أقصر من كذا» is no children's book, and keeps the kinds shown")


# --- 11: the whole path on synthetic books (no model): «وأغلى واحد؟» after «أرخص ديوان»; a 14-year-old who loves dragons
base = [dict(b) for b in A.list_books(only_active=False)][:8]
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
_ids = [b["book_id"] for b in base]
def _syn(specs):
    books = []
    for b, sp in zip([dict(x) for x in base], specs):
        b.update(quantity=3, active=1, author="شاعر " + str(b["book_id"]), original_year=None, summary="", hook="", keywords="", subjects="",
                 subgenres=None, series_name=None, series_position=None, goodreads_url=None, language="ar", age_min=None, age_max=None,
                 page_count=100)
        b.update(sp)
        books.append(b)
    A.list_books = lambda *a, **k: books
    T._cache.update(key=None)
    return books
_syn([dict(title="ديوان رخيص أ", genre="شعر", price=5.96), dict(title="ديوان رخيص ب", genre="شعر", price=6.57),
      dict(title="ديوان غال", genre="شعر", price=121.76), dict(title="ديوان وسط", genre="شعر", price=50),
      dict(title="رواية غالية", genre="رواية", price=300), dict(title="رواية ثانية", genre="رواية", price=20),
      dict(title="ديوان ثالث", genre="شعر", price=30), dict(title="ديوان رابع", genre="شعر", price=40)])
CUR.clear(); CUR.update(prefs={"genres": ["شعر"]}, about_shown=True)
captured.clear()
A._run_chat_once([{"role": "user", "content": "أبي أوفر، وش أرخص ديوان شعر عندكم؟"},
                  {"role": "assistant", "content": "أرخص ديوان «ديوان رخيص أ» ٥٫٩٦ ريال", "book_ids": [_ids[0], _ids[1]]},
                  {"role": "user", "content": "وأغلى واحد؟"}], active=("synthetic", "no-network"))
_f = [f["id"] for f in captured[0]["facts"]] if captured else []
ok(_f[:1] == [_ids[2]] and _ids[4] not in _f, f"49.2 (whole path): «وأغلى واحد؟» → the dearest poetry in stock first (121.76), not a novel ({_f[:4]})")
ok("لكل الكتب المتوفرة" in (captured[0]["guidance"] if captured else ""), "49.2: the guidance says the order ran over the whole stock")
CUR.clear(); CUR.update(prefs={"genres": ["شعر"]}, about_shown=True)
captured.clear()
A._run_chat_once([{"role": "user", "content": "أبي أوفر، وش أرخص ديوان شعر عندكم؟"},
                  {"role": "assistant", "content": "أرخص ديوان «ديوان رخيص أ» ٥٫٩٦ ريال", "book_ids": [_ids[0], _ids[1]]},
                  {"role": "user", "content": "أيهم أغلى؟"}], active=("synthetic", "no-network"))
_f = [f["id"] for f in captured[0]["facts"]] if captured else []
ok(_f[:1] == [_ids[1]] and _ids[2] not in _f[:2], f"negative: «أيهم أغلى؟» stays among the two shown ({_f[:3]})")

_syn([dict(title="التنين الأبيض", genre="يافعين", age_min=10, keywords='["تنين", "فنتازيا"]', summary="قصص في إطار سحري"),
      dict(title="المعلم ومرغريتا", genre="رواية", summary="رواية عن السحر والشيطان في موسكو"),
      dict(title="حكواتي الليل", genre="رواية", summary="حكايات تفك سحر الصمت"),
      dict(title="مومو", genre="أطفال", age_min=12, subgenres='["فانتازيا"]', summary="فتاة تواجه الرجال الرماديين"),
      dict(title="سر الغابة", genre="يافعين", age_min=12, summary="مغامرة فانتازيا فيها قتل ودماء"),
      dict(title="رواية واقعية", genre="يافعين", age_min=12, summary="حياة مدرسية"),
      dict(title="كتاب طبخ", genre="طبخ"), dict(title="ديوان", genre="شعر")])
CUR.clear(); CUR.update(prefs={"genres": ["رواية"], "subgenres": ["سحر"], "unsupported_requests": ["تنانين"], "audience": "young_adult",
                               "avoid_content": ["violence"]}, reader_age=14)
captured.clear()
A._run_chat_once([{"role": "user", "content": "ولدي عمره ١٤ ويموت على قصص التنانين والسحر، بس أمه ما تبي يكون فيها قتل ولا دم"}],
                 active=("synthetic", "no-network"))
_f = [f["id"] for f in captured[0]["facts"]] if captured else []
ok(_f[:2] == [_ids[0], _ids[3]], f"5.1 (whole path): «التنين الأبيض» (young shelf, «تنين») then «مومو» (children's shelf, 12+) first ({_f})")
ok(_ids[4] not in _f, "5.1: a young-shelf fantasy whose summary speaks of killing and blood is not offered")
ok("ما لقيت" not in (captured[0]["guidance"] if captured else "x") or "التنين" in json.dumps(captured[0]["facts"], ensure_ascii=False),
   "5.1: the dragon book is in facts, so «no book about dragons» has nothing to stand on")
_syn([dict(title="لغز اللافندر", genre="يافعين", age_min=13, summary="مغامرة خطرة وحل اللغز"),
      dict(title="One of Us Is Lying", genre="يافعين", age_min=14, language="en", keywords='["YA", "Mystery", "Murder"]'),
      dict(title="نسب قبيلة: دراسة تحليلية", genre="تاريخ"), dict(title="الجين الأناني: تحليل كتاب", genre="علوم"),
      dict(title="سكوت بيلجريم 1", genre="يافعين", age_min=13), dict(title="رواية حب", genre="رواية"),
      dict(title="كتاب طبخ", genre="طبخ"), dict(title="ديوان", genre="شعر")])
CUR.clear(); CUR.update(prefs={"genres": ["يافعين"], "subgenres": ["جرائم", "غموض"], "moods_include": ["mysterious"],
                               "unsupported_requests": ["تحليل الأدلة"], "audience": "young_adult"}, reader_age=16)
captured.clear()
A._run_chat_once([{"role": "user", "content": "بنت أخوي ١٦ سنة تحب الجرايم الغامضة وتحليل الأدلة"}], active=("synthetic", "no-network"))
_f = [f["id"] for f in captured[0]["facts"]] if captured else []
ok(_f[:2] == [_ids[0], _ids[1]] and _ids[2] not in _f[:2] and _ids[3] not in _f[:2],
   f"25.1 (whole path): «لغز اللافندر» then «One of Us Is Lying», not the «تحليل» studies ({_f})")
ok(_ids[4] not in _f[:2], "25.1: «سكوت بيلجريم» is no crime story")
CUR.clear(); CUR.update(prefs={"genres": ["رواية"], "subgenres": ["غموض"], "audience": "young_adult"}, reader_age=15)
captured.clear()
A._run_chat_once([{"role": "user", "content": "ولد أختي ١٥ سنة يحب القصص البوليسية، وش عندكم؟"}], active=("synthetic", "no-network"))
_f = [f["id"] for f in captured[0]["facts"]] if captured else []
ok(_f[:2] == [_ids[0], _ids[1]], f"fresh: «يحب القصص البوليسية» at 15 → the young shelf's mysteries first ({_f})")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
