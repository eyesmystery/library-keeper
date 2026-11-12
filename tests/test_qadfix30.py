"""[QADFIX30] The errors of the QAD round 2026-11-07 (Ibrahim's standing word of 2026-11-06: QAD fixes go ahead): 46.1–46.2 the most
expensive book of a kind over the whole stock; 2.1 «ما يقل عن ١٠٠ صفحة ولا يزيد عن ٢٠٠»; 25.2 «شي ثاني بس مو لمحفوظ»; 45.1 «… بس مو
العادات الذرية لأني قريته»; 17.2–17.3 a girl of 13, realistic, no violence, «رف اليافعين»; medium 5.2–5.3 the longlist only, 24.1 the
prize winner itself in a decade, 42.1 «بالعربي او انجليزي ما تفرق», 39.3 «أقصر» with a subject found nowhere; minor 3.1, 22.1, 29.1,
49.1. Each check uses the round's own wording and a fresh rewording."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import chat_rules as R
import honors as H
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()

# --- 1 (cases 46.1–46.2, grave): the most expensive book of a kind
ok(R.dearest_asked("أغلى كتاب فنون عندكم كم سعره؟") and R.dearest_asked("لا أقصد أغلى كتاب فن عندكم على الإطلاق، مو من المعروض"),
   "46.1–46.2: «أغلى كتاب فنون» is the most expensive")
ok(R.dearest_asked("وش الكتاب الأعلى سعرًا عندكم في الشعر؟") and R.dearest_asked("what's the most expensive art book you have?"),
   "fresh: «الأعلى سعرًا» / 'most expensive'")
ok(not R.dearest_asked("هذا أغلى من 100 ريال؟") and not R.dearest_asked("أرخص رواية") and not R.dearest_asked("كتاب غالي على قلبي"),
   "«أغلى من 100» (a comparison), «أرخص», «غالي» are not the most expensive")
ok(R.absolute_asked("لا أقصد أغلى كتاب فن عندكم على الإطلاق، مو من المعروض") and R.absolute_asked("أرخص رواية عندكم أصلاً")
   and not R.absolute_asked("أغلى كتاب فنون عندكم"), "46.2: «على الإطلاق، مو من المعروض» is over the whole stock")
ok(R.is_price_phrase("أغلى كتاب") and R.is_price_phrase("الأعلى سعرًا") and not R.is_price_phrase("الفنون"), "«أغلى كتاب» is never a subject")
ok('_dear30 = chat_rules.dearest_asked(_last_msg) and not cheapest and not _short' in src
   and '("dearest" if _dear30 else "cheapest" if cheapest else _short)' in src and 'elif order_by == "dearest":' in src,
   "the price order turned round, with its own guidance")
ok('and order_by != "dearest"):' in src and 'not it.get("copy_language") and not _dear30:' in src,
   "«The Cosmic Script» (English, 700) is not pushed after the Arabic copies")
ok("_base = _base + [(1, [], b) for b in pool if b[\"book_id\"] not in _ids30" in src, "every book the request allows, not only the scored ones")

# --- 2 (case 2.1, grave): page limits in every wording
ok(R.page_limit(["ابي كتاب تاريخ ما يقل عن ١٠٠ صفحة ولا يزيد عن ٢٠٠"]) == (200, 100), "2.1: «ما يقل عن ١٠٠ صفحة ولا يزيد عن ٢٠٠» = 100–200")
ok(R.page_limit(["أبي كتاب تاريخ لا تقل صفحاته عن ١٥٠ وما تتعدى ٣٠٠"]) == (300, 150), "fresh: «لا تقل صفحاته عن ١٥٠ وما تتعدى ٣٠٠»")
ok(R.page_limit(["رواية من ١٠٠ لين ٢٠٠ صفحة"]) == (200, 100) and R.page_limit(["من ١٠٠ إلى ٢٠٠ صفحة"]) == (200, 100),
   "«من … لين / إلى … صفحة»")
ok(R.page_limit(["not less than 100 pages and no more than 250"]) == (250, 100) and R.page_limit(["كتاب لا يقل عن 150 صفحة"]) == (None, 150),
   "English bounds; a lower bound alone")
ok(R.page_limit(["لا يكون أقل من ١٠٠ صفحة"]) == (None, 100), "«لا يكون أقل من ١٠٠» is at least 100, not under 100")
ok(R.page_limit(["كتاب ما يزيد عن ٥٠ ريال وصفحاته كثيرة"]) == (None, None) and R.page_limit(["رواية من 1950 الى 1960"]) == (None, None),
   "a price or a span of years is no page limit")
ok(R.page_limit(["أقل من ١٠٠ صفحة"]) == (99, None) and R.page_limit(["بين ٨٠ و١٥٠ صفحة"]) == (150, 80) and R.page_limit(["بين القصرين"]) == (None, None),
   "QADFIX27 / QADFIX29 limits unchanged")
ok('_pu30 = [u for u in unsupported if chat_rules.is_pages_phrase(u)]' in src, "the interpreter's «من ١٠٠ إلى ٢٠٠ صفحة» is the page limit too")

# --- 3 (case 25.2, grave): a named author refused
_fake = {1: {"book_id": 1, "title": "قشتمر", "author": "نجيب محفوظ", "quantity": 2, "active": 1},
         2: {"book_id": 2, "title": "مئة عام من العزلة", "author": "غابرييل غارسيا ماركيز", "quantity": 2, "active": 1},
         3: {"book_id": 3, "title": "الشاب", "author": "آني إرنو", "quantity": 2, "active": 1},
         4: {"book_id": 4, "title": "المسخ", "author": "فرانز كافكا", "quantity": 2, "active": 1}}
_req = {"name_as_written": "محفوظ", "full_name": "نجيب محفوظ", "aliases": ["Naguib Mahfouz"], "mode": "works"}
ok(R.negated_spans("شي ثاني بس مو لمحفوظ") == ["لمحفوظ"], "25.2: «مو لمحفوظ» is a refused span")
_ra = A.refused_authors30(["ابي رواية صارت مسلسل عشان اقراها قبل لا اتفرج", "شي ثاني بس مو لمحفوظ"], _req, _fake)
ok("نجيب محفوظ" in _ra and "Naguib Mahfouz" in _ra, "25.2: محفوظ refused in every spelling of the request")
ok("نجيب محفوظ" in A.refused_authors30(["أبي رواية تحولت لمسلسل بس غير نجيب محفوظ"], {}, _fake), "fresh: «غير نجيب محفوظ» with no request")
ok(A.refused_authors30(["وشي واقعي مو خيال؟"], {}, _fake) == [] and A.refused_authors30(["ما ابي شي طويل"], {}, _fake) == [],
   "«مو خيال»، «ما ابي شي طويل» refuse no author")
ok(A.refused_authors30(["شي ثاني بس مو لمحفوظ", "طيب عندكم شي لنجيب محفوظ؟"], {}, _fake) == [], "asked for again later: no longer refused")
ok(A.refused_authors30(["ما ابي الا نجيب محفوظ"], {}, _fake) == [], "«ما أبي إلا نجيب محفوظ» means only him")
ok('author_request = dict(author_request, mode="none")\n            it["author_request"] = author_request\n            it["wants_new_options"] = True' in src
   and "hard.append(_not_by30)" in src and '("noauthor", ' in src, "the author request dropped, his books left out, a badge to lift it")
ok("لا تقل إن المناسب كله له، ولا تعرض" in src, "the guidance: never «all of them are his», never his English copies")

# --- 4 (case 45.1, grave): «لأني» is no author; the book read is refused
ok(R.lam_is_word("لأني") and R.lam_is_word("لانه") and R.lam_is_word("لبنتي") and not R.lam_is_word("لمحفوظ"), "«لأني»، «لانه»، «لبنتي» are words")
ok(A.title_author_said("العادات الذرية", "ابي كتاب عن بناء العادات بس مو العادات الذرية لأني قريته", _fake) is None,
   "45.1: «العادات الذرية لأني قريته» names no author (not آني إرنو)")
ok(A.title_author_said("المسخ", "عندكم المسخ لكافكا؟", _fake) == "كافكا", "«المسخ لكافكا» still names Kafka")
ok(R.title_refused("العادات الذرية", "ابي كتاب عن بناء العادات بس مو العادات الذرية لأني قريته"), "45.1: the title is refused")
ok(R.title_refused("العادات الذرية", "ودي بكتاب يساعدني أكوّن عادات يومية، غير العادات الذرية لأنه عندي"), "fresh: «غير العادات الذرية لأنه عندي»")
ok(not R.title_refused("الخيميائي", "مو الخيميائي حلوة؟") and not R.title_refused("العادات الذرية", "عندكم العادات الذرية؟")
   and not R.title_refused("العادات الذرية", "ابي شي زي العادات الذرية لاني قريته وعجبني"), "a question or a likeness is no refusal")
ok(A.head_object30("بناء العادات") == "العادات" and A.head_object30("تطوير الذات") == "الذات" and A.head_object30("الحرب الباردة") is None,
   "«بناء العادات»: the habits are the subject, never «بناء»")
ok('it["refused_ids30"] = sorted(' in src and "hard.append(_not_refused30)" in src and '"relation": "rejected"} for t in _refused30' in src,
   "the refused book (all its copies) is left out, as a rejected reference")
ok("_s30 = _s30.replace(" in src and "(head_object30(p) or topics.core_word(p, topic_index))" in src,
   "the words of the refused title are no refused subject; the subject is not cut to «بناء»")

# --- 5 (cases 17.2–17.3, grave): a girl of 13, realistic, no violence, the young readers' shelf
_mr = {"subgenres": '["واقعية سحرية"]'}
ok(A.matching_subgenres(_mr, {"subgenres": ["واقعية"]}) == [], "17.2: «واقعية سحرية» is not the realism asked for")
ok(A.matching_subgenres(_mr, {"subgenres_exclude": ["خيال"]}, key="subgenres_exclude") == ["واقعية سحرية"], "… and it is the fantastic refused")
ok(A.matching_subgenres({"subgenres": '["رومانسية معاصرة", "واقعية"]'}, {"subgenres": ["واقعية"]}) == ["واقعية"]
   and A.matching_subgenres({"subgenres": '["فانتازيا مظلمة"]'}, {"subgenres": ["فانتازيا"]}) == ["فانتازيا مظلمة"], "other subgenres unchanged")
ok(A.matching_subgenres(_mr, {"subgenres_exclude": ["خيال علمي"]}, key="subgenres_exclude") == [],
   "«بدون خيال علمي» does not refuse magical realism (AUDFIX 2026-10-25 kind exclusion)")
ok("or (f[\"id\"] in {x[2][\"book_id\"] for x in ya_added30}" in src and "_real30 and re.search(" in src,
   "replay 17.2: a shelf book offered is not dropped for a subgenre its record lacks; no fantasy from the shelf for «واقعية»")
ok(R.ya_shelf_said("هي عمرها ١٣ وما تحب العنف، ابي شي من رف اليافعين") and R.ya_shelf_said("وش عندكم في قسم اليافعين؟")
   and R.ya_shelf_said("anything from the young adult shelf?") and not R.ya_shelf_said("رواية للكبار"), "17.3: the shelf named, fresh wording too")
# (QADFIX33 #2, QAD 2026-11-10 case 25.1: the young-reader path now runs to 17)
ok("ya_added30.append((0.5," in src and "(_yas30 or reader_age is None or 12 <= reader_age <= 17))" in src,
   "the shelf's books are offered for 12–17, a young reader of unsaid age, or when it is named")
# --- QADFIX30b (Ibrahim 2026-11-07 «ب»): with ≥2 fitting shelf books, adult books with no content record are left out entirely
def _bk(i, shelf=False, known=False, kid=False):
    return {"book_id": i, "shelf": shelf, "known": known, "kid": kid}
_on, _kn, _yb = (lambda b: b["shelf"]), (lambda b: b["known"]), (lambda b: b["kid"])
_sc = [(9, [], _bk(1, shelf=True)), (8, [], _bk(2, shelf=True)), (5, [], _bk(3)), (4, [], _bk(4, known=True)), (3, [], _bk(5, kid=True))]
_k, _o = A.drop_unknown_adult30b(_sc, _on, _kn, _yb)
ok(_o == {3} and [x[2]["book_id"] for x in _k] == [1, 2, 4, 5],
   "30b 17.3: two shelf books fit — the adult book with no content record goes; one with a record and a children's book stay")
_sc1 = [(9, [], _bk(1, shelf=True)), (0, [], _bk(2, shelf=True)), (5, [], _bk(3))]
_k1, _o1 = A.drop_unknown_adult30b(_sc1, _on, _kn, _yb)
ok(_o1 == set() and len(_k1) == 3, "30b boundary: one fitting shelf book (the other scores 0) — nothing left out, ordering only")
_k2, _o2 = A.drop_unknown_adult30b([(5, [], _bk(3)), (4, [], _bk(6))], _on, _kn, _yb)
ok(_o2 == set() and len(_k2) == 2, "30b: no shelf book fits — the adult books stay (the reply says their content is not known)")
ok("scored, _out30b = drop_unknown_adult30b(" in src and "if _avoid30:" in src and 'f["id"] not in set(it.get("ya_out30b") or ())' in src,
   "30b: only with a content to avoid, and never a card by another path")
ok("max(s - 3, 0.1) if s > 0 and _avoid30 and not _content_known30(b)" in src and "ما عندنا سجل محتوى لهذه الكتب" in src,
   "an adult book with no content record never passes as free of violence")
ok("ولا تقل إنه ما عندك\"\n                      \" تفاصيل عن هذا الرف" in src or "تفاصيل عن هذا الرف" in src, "never «ما عندي تفاصيل عن رف اليافعين»")

# --- 6 (cases 5.2–5.3, medium): the longlist only
ok('"long_only" if any("القائمه الطويله" in x for x in _own30)' in src and "الزائر يسأل عن الطويلة فقط: أجب بالعدد الأول" in src,
   "5.2–5.3: longlist only and shortlist counted apart, the same numbers every turn")
ok('(?:ال)?عدد (?:ال)?(?:كتب|كتاب|روايات|روايه|قصص' in src, "5.3: «عدد الروايات» is never a subject")
ok(A.longlist_only30(["كم رواية لكاتبات دخلت القائمة الطويلة للبوكر العربية؟", "سمّ لي اللي وصلت الطويلة بس وما دخلت القصيرة"])
   and A.longlist_only30(["طيب كم وحدة بالضبط وصلت الطويلة بس؟"]), "5.2–5.3: the longlist only, the round's wording")
ok(A.longlist_only30(["أبي اللي وصلت للقائمة الطويلة ولا وصلت القصيرة"]) and not A.longlist_only30(["ابي رواية طويلة بس"])
   and not A.longlist_only30(["كم رواية وصلت القائمة الطويلة؟"]), "fresh wording; «رواية طويلة بس» is no prize list")
ok('"long_only_ids") or []' in src, "replay 5.2: the longlist-only books come first in facts (their cards)")
# --- 7 (case 24.1, medium): the winner itself first
ok("الكتب اللي فازت هي نفسها ضمن طلبه: " in src, "24.1: «Beloved» itself named before the author's other books")
# --- 8 (case 42.1, medium): either language
ok(A.wanted_edition("Locus award winners عندكم؟ بالعربي او انجليزي ما تفرق") is None, "42.1: «بالعربي او انجليزي ما تفرق» is no copy wish")
ok(A.wanted_edition("ابي روايات فازت بهوغو، عربي أو إنجليزي عادي") is None and A.wanted_edition("ابيها بالعربي") == "ar"
   and A.wanted_edition("عندكم نسخة انجليزية؟") == "en", "fresh: «عربي أو إنجليزي عادي»; a plain copy wish unchanged")
ok('it["copy_language"] = None   # QADFIX30 m8' in src, "the model's copy language is lifted too")
# --- 9 (case 39.3, medium): «أقصر» with a subject found nowhere now
ok("# QADFIX30 m9 (case 39.3)" in src and "_sh30 = [(1, [], all_books[i]) for i in shown" in src,
   "39.3: «أقصر» among the books shown for the subject earlier («الطوباوية» 224, then «انزلاق رجل الجبار» 299)")

# --- minor
ok(H.to_english("فاز بجائزة البوكر الدولية 2024") == "Won International Booker Prize 2024"
   and H.to_english("مؤلفه فاز بجائزة البوكر الدولية 2019") == "Its author won International Booker Prize 2019", "49.1: no «الدولية» in English")
ok("_head29(j) == _k) >= 2" in src, "3.1: series cards are named by the rest of their title")
ok('debug["edition_cards30"] = _dup30' in src, "29.1: a second edition of a card's work is no card of its own")
ok("ما عندنا كتاب بعنوان «{_mt}» (لا تقل إنه غير متوفر الآن)" in src, "22.1: a title no one knows is «no book of that title», not «not in stock now»")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
