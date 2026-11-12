"""[QADFIX32] The errors of the QAD round 2026-11-09 (Ibrahim's standing word of 2026-11-06: QAD fixes go ahead): grave 46.1 «عندكم
كل أجزاء ميلينيوم؟» (a one-word series name), 1.1 «أضخم كتاب تاريخ» (the longest in other words); medium 3.1 blood or killing refused
for a teenager (horror out), 5.2 «سيرة عالمة امرأة», 28.1 «رواية … فيه حصار وغزالة» (remembered title words); minor 20.1, 45.1, 15.1.
Each check uses the round's own wording and a fresh rewording."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import chat_rules as R
from keyword_prefs import normalize
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()
src_r = open(R.__file__, encoding="utf-8").read()


def bk(i, title, author="مؤلف", series=None, pos=None, q=2, **kw):
    return dict({"book_id": i, "title": title, "author": author, "series_name": series, "series_position": pos, "quantity": q,
                 "active": 1, "language": "ar", "genre": "رواية"}, **kw)


# a small catalog of its own: the tests run on the prototype catalog, which has no «ميلينيوم»
BOOKS = {b["book_id"]: b for b in [
    bk(2829, "فتاة لا يحبها الرجال", "ستيغ لارسون", "ميلينيوم", 1), bk(2823, "فتاة لعبت بالنار", "ستيغ لارسون", "ميلينيوم", 2),
    bk(4231, "فتاة في عش الدبابير", "ستيغ لارسون", "ميلينيوم", 3), bk(4209, "فتاة في شبكة العنكبوت", "ديفيد لاغركرانتز", "ميلينيوم", 4),
    bk(3864, "حروب الردة", "مؤلف أ", "حروب", 1), bk(3870, "حروب الفرنجة", "مؤلف ب", "حروب", 2),
    bk(1, "كينغسبريدج", "كين فوليت"), bk(2746, "أعمدة الأرض", "كين فوليت", "كينغسبريدج", 1),
    bk(2769, "عالم بلا نهاية", "كين فوليت", "كينغسبريدج", 2), bk(10, "موساشي", "إيجي يوشيكاوا", "موساشي", 1),
    bk(5534, "حصار الغزالة واو"), bk(4125, "حرير الغزالة"), bk(636, "قصة حصار لشبونة"), bk(4414, "حيزيا: زفرة الغزالة الذبيحة"),
    bk(2448, "الأخلاق", "أرسطو"), bk(2902, "الأخلاق", "جورج إدوارد مور"),
]}
_ids = lambda xs: [b["book_id"] for b in xs]

# --- 1 (case 46.1, grave): a series by its one-word name
ok(_ids(R.series_books("ميلينيوم", BOOKS)) == [2829, 2823, 4231, 4209], "46.1: «ميلينيوم» is the series, its four parts in order")
ok(_ids(A.match_titles(["ميلينيوم"], BOOKS)) == [2829, 2823, 4231, 4209], "46.1: match_titles finds the series by its name alone (no author)")
ok(_ids(R.series_books("سلسلة ميلينيوم", BOOKS)) == [2829, 2823, 4231, 4209] and _ids(R.series_books("كل أجزاء ميلينيوم", BOOKS))[:1] == [2829],
   "fresh: «سلسلة ميلينيوم»، «كل أجزاء ميلينيوم» (the word «series» is not part of the name)")
ok(R.series_books("حروب", BOOKS) == [] and R.series_books("موساشي", BOOKS) == [] and R.series_books("فتاة", BOOKS) == [],
   "a one-word series only with 5 letters or more and two parts or more, never one word of a title")
ok(_ids(R.series_books("ثلاثية", BOOKS)) == [] and _ids(R.series_books("كينغسبريدج", BOOKS)) == [2746, 2769],
   "a common word names no series; «كينغسبريدج» (two parts) does")
ok(R.part_asked("عندكم كل أجزاء ميلينيوم؟") == "all", "«كل أجزاء» asks for all the parts")

# --- 2 (case 1.1, grave): «أضخم / أسمك / أكبر كتاب / أكثر صفحات» is the longest
ok(R.longest_asked("كم صفحة أضخم كتاب تاريخ عندكم؟") == "longest", "1.1: «أضخم كتاب تاريخ» is the longest")
ok(R.longest_asked("وأكبر رواية من ناحية الصفحات؟") == "longest" and R.longest_asked("وش أسمك كتاب فلسفة عندكم؟") == "longest"
   and R.longest_asked("أكثر رواية صفحات عندكم") == "longest" and R.longest_asked("الأضخم؟") == "longest",
   "fresh: «وأكبر رواية من ناحية الصفحات»، «أسمك كتاب»، «أكثر … صفحات»، «الأضخم»")
ok(R.longest_asked("thickest novel you have?") == "longest" and R.longest_asked("the biggest book in the history section") == "longest",
   "English: thickest, biggest book")
ok(R.longest_asked("وش اسمك؟") is None and R.longest_asked("أكبر كاتب عربي؟") is None and R.longest_asked("أكبر كتاب مبيعًا") is None
   and R.longest_asked("أكبر رواية عربية") is None and R.longest_asked("biggest fan of Mahfouz") is None,
   "«اسمك» (your name), «أكبر كاتب»، «أكبر كتاب مبيعًا»، «أكبر رواية عربية» (may be the greatest) are no length order")
ok(R.longest_asked("أبي كتاب أكثر من 300 صفحات") is None and R.longest_asked("أطول من ٥٠٠ صفحة") is None, "«أكثر من ٣٠٠ صفحات» is a page limit")
ok(R.shortest_asked("أنحف ديوان شعر عندكم كم صفحة؟") == "shortest" and R.shortest_asked("أقل رواية عدد صفحات") == "shortest"
   and R.shortest_asked("thinnest book?") == "shortest", "the shortest side: «أنحف»، «أقل عدد صفحات»، thinnest")
ok(R.shortest_asked("أصغر قارئ") is None and R.shortest_asked("أصغر كتاب للأصغر سنا") is None and R.shortest_asked("كتب أقل من 100 صفحات") is None,
   "«أصغر» for an age and «أقل من ١٠٠» are no shortest")
ok(R.absolute_asked("أضخم كتاب عندكم أصلاً؟"), "«أضخم … أصلاً» is over the whole stock")
ok('if not _longest27 and it.get("order_by") == "longest"' in src, "the model's order_by «longest» counts too (any wording)")

# --- 3 (case 3.1, medium): blood or killing refused for a teenager — horror out too
_n = lambda q: " ".join(normalize(q).split())
ok(A._GORE_NOT.search(_n("بنت أختي عمرها ١٣ تحب قصص السحر والعوالم الخيالية، بس أهلها ما يبون شي فيه دم أو قتل")) is not None,
   "3.1: «ما يبون شي فيه دم أو قتل» is content to avoid")
ok(A._GORE_NOT.search(_n("ولدي ١٢ سنة يحب الفانتازيا والتنانين بس ما نبي شي فيه دم")) is not None
   and A._BLOOD32.search(_n("ما نبي شي فيه دم")) and not A._BLOOD32.search(_n("ما يحب شي فيه عنف")),
   "fresh: «ما نبي شي فيه دم»; blood words only (not «عنف» alone) take horror out")
ok('pprefs["avoid"] = set(pprefs["avoid"]) | {"horror"}' in src and 'pprefs.get("audience") in ("children", "young_adult") and _BLOOD32.search(_lastq)' in src,
   "horror joins the content to avoid for a child or a teenager only")
_card = {"confidence": {("sensitive_content", "horror"): (0.75, False)}, "sensitive_content": ["horror"], "format": "young_adult"}
ok(A.age_allowed(_card, "young_adult", ("violence",)) and not A.age_allowed(_card, "young_adult", ("violence", "horror")),
   "«أمير الضباب» (horror) stays for «بلا عنف» alone, out with blood refused")

# --- 4 (case 5.2, medium): «عالمة»
ok(A.scientist_phrase32("عالمة امرأة") == "f" and A.scientist_phrase32("قصة حياة عالمة") == "f" and A.scientist_phrase32("عالمات عربيات") == "f",
   "5.2: «عالمة امرأة»، «قصة حياة عالمة»، «عالمات عربيات» are a woman scientist")
ok(A.scientist_phrase32("عالم كيميائي") == "m" and A.scientist_phrase32("عالمة فيزيائية") == "f", "fresh: «عالم كيميائي»، «عالمة فيزيائية»")
ok(A.scientist_phrase32("عالم الموضة") is None and A.scientist_phrase32("عالم الأدب") is None and A.scientist_phrase32("العالم العربي") is None,
   "«عالم الموضة / الأدب»، «العالم العربي» are a world, not a scientist")
ok('unsupported = list(dict.fromkeys(unsupported + ["عالمة", "سيرة عالمة"]))' in src, "a woman scientist's life is searched in the feminine")

# --- 5 (case 28.1, medium): remembered title words
ok(_ids(A.title_words_books32(["حصار", "غزالة"], BOOKS)) == [5534], "28.1: «حصار» + «غزالة» → «حصار الغزالة واو» («ال» folded)")
ok(_ids(A.title_words_books32(["حرير", "الغزالة"], BOOKS)) == [4125], "fresh: «حرير» + «الغزالة» → «حرير الغزالة»")
ok(A.title_words_books32(["غزالة"], BOOKS) == [] and A.title_words_books32(["رواية", "كتاب"], BOOKS) == [], "one word, or reading words, is no remembered title")
ok('if it["intent"] in ("clarify", "smalltalk"):\n            it["intent"] = "recommend"' in src and "الزائر يتذكر كلمات من عنوان" in src,
   "a clarifying question becomes the books, with the title offered first in the guidance")

# --- minor
ok("whose length we do not know comes right after them" in src and "        if _known32:\n" in src
   and "scored = sorted(((min(s, _floor32 - 0.01) if s > _floor32 and _unk32(b) else s, r, b) for s, r, b in scored)," in src,
   "20.1: a short book asked — a book with no page count and no short record comes after the good fits known to be short")
_lm = " ".join(normalize("فيه رواية ما أذكر عنوانها بالضبط، فيها كلمة حرير وكلمة غزالة".replace("،", " ")).split())
ok(__import__("re").findall(r"(?:^| )[وب]?(?:كلمه|كلمة|word) (\S+)", _lm) == ["حرير", "غزاله"]
   and _ids(A.title_words_books32(["حرير", "غزاله"], BOOKS)) == [4125] and 'asked_books = [all_books[i] for i in it["title_words32"]' in src,
   "replay fresh: «فيها كلمة حرير وكلمة غزالة» (the model kept only «الحرير») → «حرير الغزالة» as the book asked for")
ok("def _same_title_other32(i, chosen):" in src and "if i in all_books and _same_title_other32(i, ids):" in src,
   "45.1: «الأخلاق» لأرسطو in the reply does not bring Moore's «الأخلاق» card")
ok('prefs["soft_subgenres"] = list(dict.fromkeys(list(prefs.get("soft_subgenres") or []) + ["ملحمة", "ملحمية"]))' in src
   and A.matching_subgenres({"subgenres": '["رواية ملحمية"]'}, {"soft_subgenres": ["ملحمية"]}, "soft_subgenres"),
   "15.1: «ملحمي» ranks the books our records call epic first")

ok('if _nobq32 and _arec32 and _arec32.get("author_id"):' in src and 'authors.lookup(" ".join(_w32[k:k + _n32]), spelling=False)' in src
   and "ليس ضمن الفائزين بجائزة نوبل للآداب في قائمتنا" in src and 'not it.get("nobel_not32")' in src
   and '"نوبل" in str(r.get("award_name") or "")' in src,
   "6.1: «هل إبراهيم الكوني أخذ نوبل؟» — a linked author not in our Nobel list is said so, with his books named (not «no details»)")

# --- QADFIX32b (Ibrahim 2026-11-09 14:0x): «أ» children's-shelf books whose recorded age covers a reader of 12–15; «ب» a fifth card
_kid = lambda lo, hi=None, g="أطفال": {"genre": g, "age_min": lo, "age_max": hi}
ok(A.age_covers32(_kid(12), 13) and A.kid_shelf32(_kid(12), None), "3.1 (أ) positive: «مومو» (children's shelf, 12+) covers a reader of 13")
ok(not A.age_covers32(_kid(13), 12) and not A.age_covers32(_kid(None), 13) and not A.age_covers32(_kid(10, 11), 13)
   and not A.kid_shelf32(_kid(12, g="رواية"), None),
   "negative: «13+» for 12, no recorded age, «10–11» for 13, a novel off the children's shelf")
ok(A.age_covers32(_kid(12), 12) and A.age_covers32(_kid(10, 13), 13) and not A.age_covers32(_kid(10, 13), 14)
   and A.age_covers32(_kid(8), 12) and not A.age_covers32(_kid(7), 12),
   "boundary: «12+» for 12 and «10–13» for 13 yes; «10–13» for 14 no; an open «8+» covers 12, «7+» does not (four years past its start)")
ok("kid_added32 = []" in src and "if reader_age is not None and 12 <= reader_age <= 15:" in src
   and "(_top30 + 0.5) if b[\"book_id\"] in _kid32 else" in src and "[(_top30 + 1, r, b) for s, r, b in ya_added30]" in src,
   "(أ) only for a reader of 12–15, ranked right after the young readers' shelf books (shelf + 2·top, shelf additions top+1, these top+0.5)")
ok('or not age_allowed(_c32, "young_adult", _avoid30, b, reader_age) or not refusals_ok(b, _c32, prefs, exclusions)' in src
   and "not kid_shelf32(b, _c32) or not age_covers32(b, reader_age) or not all(h(b) for h in hard)" in src
   and '{b["book_id"] for _r, b in kid_added32}' in src,
   "(أ) the content to avoid, the refusals and the hard wishes hold; they survive the subject-only filter like the shelf additions")
_kc = {"confidence": {("sensitive_content", "horror"): (0.75, False)}, "sensitive_content": ["horror"], "format": "children"}
ok(not A.age_allowed(_kc, "young_adult", ("violence", "horror")), "(أ) a children's book recorded horror stays out when blood is refused")
_F = [{"id": i, "available": True} for i in (855, 3798, 3352, 5278, 1047, 5777)]
_B5 = {855: bk(855, "أقفاص فارغة", "فاطمة قنديل"), 3798: bk(3798, "حارث المياه", "هدى بركات"),
       3352: bk(3352, "رامة والتنين", "إدوار الخراط"), 5278: bk(5278, "رامة والتنين", "إدوار الخراط"),
       1047: bk(1047, "شوق الدرويش", "حمور زيادة"), 5777: bk(5777, "مسرى الغرانيق في مدن العقيق", "أميمة الخميس")}
_q5 = [R.title_key(t) for t in ("أقفاص فارغة", "حارث المياه", "رامة والتنين", "شوق الدرويش", "مسرى الغرانيق في مدن العقيق")]
ok(A.five_list32(_F, _B5, _q5, "عندنا ٥ كتب فازت بالميدالية") == [855, 1047, 3352, 3798, 5777]
   and A.five_list32(_F, _B5, _q5, "عندنا خمسة كتب فازت هي نفسها") != [],
   "31.1 (ب) positive: five works named in full and «٥ / خمسة» → five cards (two copies of «رامة والتنين» count once)")
ok(A.five_list32(_F, _B5, _q5[:4], "عندنا خمسة كتب فازت") == [] and A.five_list32(_F, _B5, _q5, "هذه بعض الكتب الفائزة") == []
   and A.five_list32(_F, _B5, _q5, "عندنا كتب فازت بين ١٩٩٩ و٢٠٠٥") == [],
   "negative / boundary: four named, or five named without «five», stay four cards; a year «٢٠٠٥» is not «٥»")
_F6 = _F + [{"id": 4125, "available": True}]
_B6 = {**_B5, 4125: bk(4125, "حرير الغزالة", "جوخة الحارثي")}
ok(A.five_list32(_F6, _B6, _q5 + [R.title_key("حرير الغزالة")], "ستة كتب، منها خمسة فازت") == []
   and A.five_list32([dict(f, available=(f["id"] != 1047)) for f in _F], _B5, _q5, "خمسة كتب") == [],
   "boundary: six works named, or one of the five not in stock, is no complete list of five")
ok(A.five_list32(_F, _B5, _q5, "أقصرها «حارث المياه» … وأطولها «مسرى الغرانيق»", prior=[855, 3798, 5777, 1047, 3352]) != []
   and A.five_list32(_F, _B5, _q5, "أقصرها … وأطولها", prior=[855, 3798, 5777, 1047]) == [],
   "replay 31.2: «أيها أقصر؟» ordering the very five works shown last keeps five cards; four shown before is no complete list")
ok("_cap32 = 5 if _five32 else 4" in src and "for i in ids[:_cap32]:" in src and "ids[:4]" not in src[src.index("_cap32 = 5"):src.index("debug[\"named_kind\"] = _named_kind_dbg")],
   "(ب) the card limit is five only then, in every step of the card check")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
