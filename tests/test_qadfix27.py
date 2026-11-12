"""[QADFIX27] The QAD rounds of 2026-11-04 / 11-05 (Ibrahim «صلح الغلطات»): women winners of a prize (11-04 8.1–8.3, 11-05 1.1–1.2);
a place / a whole subject in the title or summary of a book without the subgenre tag (11-04 7.1 غرناطة, 16.2 الذكاء الاصطناعي);
a part held by an omnibus (11-05 3.1, 11-04 50 «مريمة» in «ثلاثية غرناطة»); page counts as words and the longest (11-05 15);
a prize's branch (11-05 29 «فرع الآداب»); a publisher's count (11-05 7, 11-04 27); no audience question after a known age
(11-05 18.3); exact title before a longer one and a copy's language (11-04 40.1); the asked title's card (11-04 24.1); a first
book in a field (11-05 4, 11-04 9)."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat_rules as CR, honors as H
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()
hsrc = open(H.__file__, encoding="utf-8").read()
N = A.normalize

def bk(i, title, author="x", genre="رواية", summary="", lang="ar", q=3, pages=None, publisher=None):
    return {"book_id": i, "title": title, "author": author, "genre": genre, "summary": summary, "language": lang, "quantity": q,
            "active": 1, "page_count": pages, "publisher": publisher}

# --- 1 (grave, 11-04 8.1–8.3; 11-05 1.1–1.2): women winners counted by the author records
ok(A.women_wish("عطني رواية مؤلفتها نفسها حازت جائزة البوكر العربية"), "8.1: «مؤلفتها نفسها» is a wish for a woman author")
ok(A.women_wish("أبي بس اللي فازت فيها امرأة، لا تعرض لي رجال") and A.women_wish("في كاتبات عربيات أخذن جائزة البوكر؟"),
   "8.2 / 1.1: «امرأة»، «كاتبات»")
ok(A.women_wish("يعني ما عندكم رواية ثانية فازت فيها كاتبة غير بريد الليل؟"), "8.3: «فازت فيها كاتبة غير بريد الليل» (not «غير الكاتبة»)")
ok(not A.women_wish("رواية مؤلفه سعودي") and not A.women_wish("شي ثاني مو لنفس الكاتبة"), "no women wish: «مؤلفه»، «نفس الكاتبة» (QADFIX11)")
ok("def women_award_counts(all_books, prefs, genres=None):" in src and 'q7["women_award27"] = women_award_counts(all_books, prefs, _gw)' in src,
   "8 / 1: the prize counts among the books our author records give to a woman")
ok("if _n_people > 1 and not _women27:" in src and "and _n_people > 1 and not _women27:" in src,
   "8.3: the general counts («٢٦ كتابًا فاز مؤلفه») are not given for a women wish")
ok("elif women_wish(user_messages[-1], _ttl35) and not author_only and not _wa27:" in src,   # (QADFIX35: the titles named are passed)
   "1.2: no «جنس المؤلف غير مسجل عندنا» when the women's prize counts are given")
ok("فأجب بصراحة بنعم أو لا منها" in src and '"unknown_won"' in src, "8.3 / 1.2: «is it the only one?» answered yes/no; unrecorded gender said as such")

# --- 2 (grave 11-04 7.1; medium 16.2): the subject's own words in a title / a whole phrase in a summary
ok('it["literal_topic27"]' in src and "_lit27(b)" in src, "7.1 / 16.2: a book with the subject in its title or summary stays a candidate")
ok("_ex27 = constrain_relaxing(_ex27, _p27, pprefs, exclusions, cards)[0][:4]" in src and "_p27 = dict(prefs, subgenres=[], soft_subgenres=[])" in src,
   "7.1: without the subgenre tag («رواية تاريخية»), every other wish kept")
ok("set(_uw27[u]) < set(_uw27[v])" in src, "16.2: «الذكاء» beside «الذكاء الاصطناعي» is no subject of its own")

# --- 3 (grave 11-05 3.1; 11-04 50): a part inside an omnibus
_bks = {1: bk(1, "ثلاثية غرناطة", "رضوى عاشور", summary="ثلاثية روائية من غرناطة ومريمة والرحيل، تبدأ عام 1491."),
        2: bk(2, "غرناطة (آخر الأيام)", "وليد سيف", summary="الأيام الأخيرة لغرناطة."),
        3: bk(3, "الأعمال الكاملة", "س", summary="فيها الحب والحرب."), 4: bk(4, "الحب", "ع")}
for i in _bks:
    _bks[i]["available"] = True
_avail = A.is_available
A.is_available = lambda b: bool(b.get("available"))
try:
    o, parts = A.omnibus_for(["غرناطة", "مريمة"], "قريت «غرناطة» أول جزء من ثلاثية رضوى عاشور، أبي «مريمة» اللي بعدها", False, _bks)
    ok(o and o["book_id"] == 1 and "مريمة" in parts, "3.1: «مريمة» after «غرناطة» → «ثلاثية غرناطة» (its summary names the part)")
    o, parts = A.omnibus_for(["مريمة"], "خلصت الجزء الأول من ثلاثية غرناطة، الثاني عندكم؟", True, _bks)
    ok(o and o["book_id"] == 1, "50.1: «الثاني من ثلاثية غرناطة» → the same volume")
    o, parts = A.omnibus_for(["ثلاثية غرناطة"], "خلصت الجزء الأول من ثلاثية غرناطة، الثاني عندكم؟", True, _bks)
    ok(o and o["book_id"] == 1, "50.1: the omnibus named itself and a part asked → that volume")
    ok(A.omnibus_for(["الحب"], "عندكم رواية الحب؟", False, _bks) == (None, []),
       "no parts or series said: «الحب» is not looked for inside an omnibus")
    ok(A.omnibus_for(["الحب"], "الجزء الثاني من الحب", True, _bks)[0] is None, "a part that is a book of its own here is that book")
finally:
    A.is_available = _avail
ok('it["omnibus27"] = {"id": omnibus27["book_id"], "parts": omnibus27_parts}' in src
   and "part_wanted, part_book, series_anchor, series_rest = None, None, None, []" in src,
   "3.1 / 50.1: the omnibus answers; no «ما عندي جزء آخر مسجّل»")
ok("الجزء اللي يسأل عنه داخل هذا المجلد نفسه" in src and "QADFIX27 #3: «مريمة» is inside «ثلاثية غرناطة» here" in src,
   "3.1: said first that the part is inside the volume; no «يشبه مريمة وهو غير متوفر»")
ok("(not _same27 or author_flow.display(b.get(\"author\")) == _oa27)" in src, "3.1: «رضوى عاشور» named — Walid Saif's «غرناطة» is not that part")

# --- 4 (grave 11-05 15): page counts in words, the longest
ok(CR.page_limit(["ودي برواية طويلة مرة فوق السبعمية صفحة أعيش معها شهر"]) == (None, 701), "15.1: «فوق السبعمية صفحة» = 701+")
ok(CR.page_limit(["أبيها فوق الألف صفحة بعد"]) == (None, 1001), "15.2: «فوق الألف صفحة» = 1001+")
ok(CR.page_limit(["ودي برواية فوق السبعمية صفحة", "أبيها فوق الألف صفحة بعد", "طيب وش أطول رواية عندكم؟"]) == (None, 1001),
   "15.3: the latest limit stays")
ok(CR.page_limit(["رواية تحت الثلاثمية صفحة"]) == (299, None) and CR.page_limit(["أطول من خمسمية صفحة"]) == (None, 501),
   "«تحت الثلاثمية»، «أطول من خمسمية»")
ok(CR.page_limit(["اكثر من ٧٠٠ صفحة"]) == (None, 701) and CR.page_limit(["كتب أقل من ١٠٠ صفحة"]) == (99, None), "QADFIX4 kept")
ok(CR.num_canon("ألف ليلة وليلة") == "1000 ليله وليله" and CR.num_canon("ألف وخمسمية") == "1500", "num_canon: «ألف وخمسمية» = 1500")
ok(CR.longest_asked("طيب وش أطول رواية عندكم؟") == "longest" and CR.longest_asked("what's the longest novel") == "longest",
   "15.3: «أطول رواية» → longest")
ok(CR.longest_asked("أبي أطول منها شوي") == "longer" and CR.longest_asked("أطول من ٥٠٠ صفحة") is None
   and CR.longest_asked("رواية طويلة") is None, "«أطول منها شوي» longer; «أطول من ٥٠٠ صفحة» a limit; «طويلة» no order")
ok('order_by, order_first, order_list = "longest", _ol27[0], _ol27[:4]' in src and 'elif order_by == "longest":' in src,
   "15.3: the longest from page_count over the request, said with its pages")
ok("pages_min = max(_lbp27) + 1" in src, "«أطول منها»: longer than the books shown")

# --- 5 (grave 11-05 29): the prize's branch
ok(CR.award_branch("عطني شي حاصل على جائزة زايد في فرع الآداب") == "الاداب", "29.1: «فرع الآداب» is a branch")
ok(CR.award_branch("طيب «هند أو أجمل امرأة في العالم» ما فازت بزايد؟") is None and CR.award_branch("رواية فازت بالبوكر") is None,
   "no branch named")
ok("def in_category(book_id, keys, branch):" in hsrc and 'verb += f" (فرع {_cat})"' in hsrc, "29.2: the branch is in the award label")
ok(H._nw("المؤلِّف الشاب") == ["مؤلف", "شاب"] and H._nw("الآداب") == ["اداب"], "branch names compared without «ال» / diacritics")
ok('it["award_branch27"] = _branch27' in src and "honors.in_category(b[\"book_id\"], list(_k), _br)" in src,
   "29.1: the winners of that branch (not the shelf «أدب»), kept as a hard wish")
ok("ولا تقل إنه ما عندك تفاصيل عن فرع الجائزة" in src, "29.2: never «ما عندي تفاصيل عن فرع الجائزة»")

# --- 6 (medium 11-05 7, 11-04 27): the publisher's count
ok('it["publisher_count27"] = len(_pw27)' in src and "لا تقل إنك ما تقدر تحدد العدد" in src, "7.2: «كم عددها بالضبط؟» → the count")
ok("(?:ال)?عدد (?:ال)?(?:كتب|كتاب|اصدارات|الاصدارات|عناوين)" in src, "7.1: «عدد الكتب» is not a subject")

# --- 7 (medium 11-05 18.3): no «للطفل ولا للكبار؟» after a known age
ok('it["audience_carried27"] = _age26' in src and "(?:و ?عن|وش عن|شي عن|وكتب عن|وفيه عن|وبعد عن|and about|what about|how about)" in src,
   "18.3: «طيب وعن الفضاء؟» after «عمره ٨» stays for the child")
ok(A.audience_age(["ولدي صاير يسأل عن البراكين والزلازل كل يوم", "عمره ٨", "طيب وعن الفضاء؟"]) == 8, "18.3: the age 8 is known")

# --- 8 (medium 11-04 40.1): exact title first; a copy's language
ok("_ex27t = [b for b in _h27 if chat_rules.title_key(b.get(\"title\") or \"\") == _mk27]" in src,
   "40.1: «الغريب – دراسة في الجهيمانية» (another shelf) is not «الغريب»")
ok("قل بوضوح: لا، ما عندنا نسخة بهذه اللغة" in src and "«رواية تركي الحمد» is an author's name" in src,
   "40.1: «نسخة فرنسية؟» answered «لا» from books.language")

# --- 9 (medium 11-04 24.1): the asked title's card
ok('debug["pinned_asked27"] = _pin27' in src and "ids = (_pin27 + ids)[:4]" in src, "24.1: «لأنك الله» quoted as in stock → its cards first")

# --- 10 (medium 11-05 4, 11-04 9): a first book in a field
ok('q7["intro27"]' in src and "ما عندنا مدخل مبسّط للعلم نفسه" in src, "4.1 / 9.1: general works first; no simplified introduction said")
ok(A._genre_in_title({"genre": "فكر", "title": "موسوعة علم الاجتماع ج2"}, {"علم اجتماع"}), "9.2: «موسوعة علم الاجتماع» is of the field")

# --- 11 (medium 11-04 11.1, 11-05 5.1): a decade the story takes place in is its setting, not a publication-year filter
ok('it["decade_setting27"] = [_lo, _hi]' in src and "(?:تدور|يدور|تصير|يصير|تجري|يجري|احداثها|احداثه|وقت|ايام|زمن|خلال|فتره|set in|takes place|during)" in src,
   "11.1 / 5.1: «تدور … وقت الاقتتال في السبعينات»، «أيام القصف في بيروت الثمانينات» — the setting, not books published then")
ok(A.years_said("قصة تدور بين ملاجئ بيروت وقت الاقتتال في السبعينات") == (1970, 1979)
   and A.years_said("ابي روايات انكتبت في الستينات") == (1960, 1969), "years_said itself unchanged (QADFIX5: «انكتبت في الستينات»)")
ok("not _RELEASE_VERB.search(_yst27)" in src, "«صدرت في السبعينات» stays a publication year")

# --- replay findings
ok("_lit27ids = set(it.get(\"literal_topic27\") or ())" in src and "or b[\"book_id\"] in _lit27ids or b[\"book_id\"] in topic_about_ids)" in src
   and "if b[\"book_id\"] not in _lit27ids else _prefs_about" in src,
   "16.1 replay: the book brought by its summary is not dropped as a card for the subgenre tag")
ok("_by_title27 = re.search(" in src, "24.1 replay: «كتاب اسمه شي زي «لأنك الله»» is the title asked, not a likeness model")
ok("«نسخة فرنسية» is the language of a copy asked for, never a subject" in src, "40.1 replay: «نسخة فرنسية» is not a subject")
ok("سؤاله عن رواية فازت هي نفسها («فازت فيها/بها»)" in src, "8.3 replay: «فازت فيها كاتبة» is the book itself winning")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
