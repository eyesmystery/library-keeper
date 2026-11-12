"""[QADFIX34] The errors of the QAD round 2026-11-11 (Ibrahim 2026-11-11 09:4x: «اصلح جميع الاخطاء واعد تشغيل السيرفر»): grave 17
("I'm 15 … The Hunger Games", "less adult please, I'm only 15"), 29.1 («رواية تدور في زمن العثمانيين بدون حروب»); medium 3.2 («سمّهم كلهم»),
8 ("List five classic novels"), 12.2 («أقصر من كذا» after a funny wish), 34.1 («روايات طالعة هالسنة او السنة اللي قبلها»), 44 (a book for
parents of a teenager); minor 1.2 (a children's-shelf «6+» book for 13), 23 («فوما غوردييف لغوركي»), 33.1 (no English copy).
Each check uses the round's own wording and a fresh rewording."""
import os, sys, time
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import chat_rules as R
import topics as T
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()


def bk(i, title, author="مؤلف", genre="رواية", q=2, **kw):
    return dict({"book_id": i, "title": title, "author": author, "genre": genre, "quantity": q, "active": 1, "language": "ar",
                 "series_name": None, "series_position": None}, **kw)


# --- 1 (case 17, grave): the visitor's own age in English; «less adult» is no grown-up reader
ok(R.ages_in("I'm 15 and I loved The Hunger Games, what should I read next?") == [15], "17.1: \"I'm 15\" is an age")
ok(R.reader_person(["I'm 15 and I loved The Hunger Games"])[1] == 15 and R.minor_self(["Something less adult please, I'm only 15"]),
   "17.1–17.2: the reader is 15, and he is the young reader himself")
ok(R.self_age_en("I am fifteen") == 15 and R.self_age_en("im 12, what should i read") == 12, "fresh: \"I am fifteen\", \"im 12\"")
ok(R.self_age_en("I'm 15 minutes from the library") is None and R.self_age_en("I'm 15 pages in") is None and R.ages_in("I'm 15 minutes away") == [],
   "\"I'm 15 minutes away\" / \"15 pages in\" are no age")
ok(not R.minor_self(["I'm 30 and like thrillers"]), "\"I'm 30\" is no young reader")
ok(not A.said_for_adult(["I'm 15 and I loved The Hunger Games", "Something less adult please, I'm only 15"]),
   "17.2: \"less adult please, I'm only 15\" is not a switch to a grown-up reader")
ok(not A.adult_said("something less adult please") and not A.adult_said("nothing too adult"), "fresh: \"less adult\" / \"too adult\" name no grown-up")
ok(A.said_for_adult(["my son is 10 and likes space", "now something for adults, for me: a thriller"]) and A.adult_said("كتاب للكبار"),
   "a grown-up reader asked plainly still switches (\"for adults, for me\", «للكبار»)")
ok(A.audience_age(["I'm 15 and I loved The Hunger Games"]) == 15, "17.1: audience_age reads 15 for the young readers' path")

# --- 2 (case 29.1, grave): «العصر العثماني» / «زمن العثمانيين» need no word «العصر / زمن» in the book
_TB = {1: bk(1, "الدرويش والموت", setting="البوسنة في العهد العثماني"), 2: bk(2, "عذراء قريش", subjects='["مقتل عثمان", "موقعة الجمل"]'),
       3: bk(3, "الفتى المتيم والمعلم", summary="رواية عن المعماري سنان في إسطنبول العثمانية"), 4: bk(4, "عصر البراءة"),
       5: bk(5, "مبادئ تاريخ الفن", summary="فن عصر النهضة الأوروبية"), 6: bk(6, "حركة الإصلاح", summary="النهضة العربية الحديثة")}
_TB.update({i: bk(i, f"كتاب عام {i}") for i in range(10, 80)})
_idx = T.index(_TB)
ok(T.match(["العصر العثماني"], 1, _idx)[0] and T.match(["العصر العثماني"], 3, _idx)[0], "29.1: «العصر العثماني» finds «في العهد العثماني» and «إسطنبول العثمانية»")
ok(T.match(["زمن العثمانيين"], 1, _idx)[0], "fresh: «زمن العثمانيين» finds «العهد العثماني»")
ok(not T.match(["العصر العثماني"], 4, _idx)[0], "«عصر البراءة» is no Ottoman book")
ok(T.match(["عصر النهضة"], 5, _idx)[0] and not T.match(["عصر النهضة"], 6, _idx)[0], "«عصر النهضة» keeps both words (the Arab «النهضة» alone is not it)")
ok(len(T.content_words("العصر العباسي")) == 1 and len(T.content_words("عصر النهضة")) == 2, "the time frame goes only before an era's adjective")

# --- 3 (case 3.2, medium): «سمّهم كلهم» names the whole list; one count
_AB = {11: bk(11, "أغالب مجرى النهر"), 12: bk(12, "فرانكشتاين في بغداد : Frankenstion in Baghdad"), 13: bk(13, "ترمي بشرر…"), 14: bk(14, "موت صغير")}
_items = [(11, 2026), (12, 2014), (13, 2010), (14, 2017)]
_rep = "عندنا ٤ روايات: «أغالب مجرى النهر» (٢٠٢٦)، «موت صغير» (٢٠١٧). تفضّل أي واحدة؟"
_miss = A.list_all_missing34(_rep, _items, _AB)
ok([i for i, _y in _miss] == [12, 13], f"3.2: the two left out are found ({_miss})")
_done = A.list_all_completed34(_rep, _miss, _AB)
ok("«فرانكشتاين في بغداد» (٢٠١٤)" in _done and "«ترمي بشرر» (٢٠١٠)" in _done and _done.rstrip().endswith("تفضّل أي واحدة؟")
   and "Frankenstion" not in _done, f"3.2: they are added before the closing question, Arabic head of the title ({_done})")
ok('resp["reply"] = list_all_completed34(resp["reply"], _miss34, all_books, language)' in src, "3.2: the call passes the books (replay crash 103.2)")
ok(not A.list_all_missing34("«أغالب مجرى النهر»، «فرانكشتاين في بغداد»، «ترمي بشرر»، «موت صغير»", _items, _AB), "a full reply is left as it is")
ok("guidance = re.sub(r\"(المتوفر عندنا الآن لطلب الجائزة: )\\S+( كتابًا فاز هو نفسه)\"" in src and 'it["list_all34"] = [(b["book_id"], y) for b, y in _own33]' in src,
   "3.2: the count opening the guidance follows the whole list")

# --- 4 (case 8, medium): a classic is also a work first published before 1960
_pp = {"subgenres": ["كلاسيكيات"]}
ok(A.matching_subgenres(bk(21, "Pride and Prejudice", language="en", original_year=1813), _pp) == ["كلاسيكيات"],
   "8.1: «Pride and Prejudice» (1813) answers «classics»")
ok(not A.matching_subgenres(bk(22, "Project Hail Mary", language="en", original_year=2021), _pp)
   and not A.matching_subgenres(bk(23, "Seabiscuit", genre="تاريخ", original_year=2001), _pp)
   and not A.matching_subgenres(bk(24, "تاريخ قديم", genre="تاريخ", original_year=1900), _pp), "a 2021 novel / a history book is no classic novel")
ok(A.matching_subgenres(bk(25, "The Thief and the Dogs", subgenres='["كلاسيكيات"]', original_year=1961), _pp) == ["كلاسيكيات"], "the recorded label still counts")
ok(A.matching_subgenres(bk(26, "Dracula", language="en", original_year=1897), {"subgenres": ["classic"]}) == ["كلاسيكيات"], "fresh: \"classic\" in English")

# --- 5 (case 12.2, medium): «أقصر من كذا» keeps the cheerful mood
ok('_wm34 = [m for m in (pprefs.get("moods") or ()) if m in ("funny", "uplifting", "comforting", "warm")]' in src
   and "_lm34 = [x for x in _less if _mood34(x[2])]" in src and 'it["shorter_mood34"]' in src, "12.2: the shorter books carry the mood asked")

# --- 6 (case 34.1, medium): «طالعة هالسنة او السنة اللي قبلها»
_y = time.localtime().tm_year
ok(A.years_said("روايات طالعة هالسنة او السنة اللي قبلها") == (_y - 1, _y), "34.1: this year or the one before")
ok(A.years_said("في روايات نازلة هالسنة؟") == (_y, _y) and A.years_said("الكتب اللي صدرت هالسنة") == (_y, _y), "fresh: «نازلة هالسنة»; «صدرت هالسنة» as before")
ok(A.years_said("books released this year or last year") == (_y - 1, _y), "fresh: \"this year or last year\"")
ok(A.new_release_said("روايات طالعة هالسنة") and not A.new_release_said("رواية طالعة من القلب"), "«طالعة هالسنة» is a new release; «طالعة من القلب» is not")
ok('it["first_publication34"] = True' in src, "34.1: works first published in those years go before reprints of unknown first year")

# --- 7 (case 44, medium): a parent's book
ok("else (s - 6, r, b) if (b.get(\"genre\") or \"\").strip() in _nonpar else (s, r, b)" in src
   # (QADFIX35: a guide to one medium does not count as one of the two; QADFIX35b: nor a book with no score)
   and "if sum(1 for _s35, _, b in scored[:6] if _s35 > 0 and _parenting_named(b) and b[\"book_id\"] not in shown and not _screen35(b)) < 2:" in src,
   "44: memoirs / novels after the parenting books; parenting books brought in until there are two")
ok(R.parent_said("أنا وزوجي تعبنا مع بنتنا ١٥ سنة، فيه كتاب للأهل عن المراهقة؟"), "44.1: «كتاب للأهل» is a parent reader")

# --- 8 (case 1.2, minor): a children's-shelf book whose recorded age does not cover the teenager
ok(not A.age_covers32(bk(31, "جيم الزر وسائق القطار", genre="أطفال", age_min=6), 13) and A.age_covers32(bk(32, "مومو", genre="أطفال", age_min=12), 13),
   "1.2: «6+» does not cover 13, «12+» does")
ok('it["kid_out34"] = sorted(_ids34)' in src, "1.2: the uncovered children's-shelf books leave the candidates")

# --- 9 (case 23, minor): the same title and author in other letters
ok(R.title_skeleton("فوما غوردييف") == R.title_skeleton("فوما جوردييف"), "23: «فوما غوردييف» = «فوما جوردييف»")
ok(" " + R.title_skeleton("غوركي") + " " in " " + R.title_skeleton("مكسيم جوركي") + " " and
   " " + R.title_skeleton("غوركي") + " " not in " " + R.title_skeleton("مكسيم خوري") + " ", "23: «غوركي» is «مكسيم جوركي», not another")
ok("_sk_by34 = chat_rules.title_skeleton(_said_by)" in src and "_known30 = any(chat_rules.title_skeleton(b.get(\"title\") or \"\") == _sk34" in src,
   "23: the asked book is ours, out of stock")

# --- 10 (case 33.1, minor): no English copy of the asked work
ok("_alt_ed = (_same_year or [o for o in _alt_ed if not (first_published(o) and first_published(_a0))])[:4]" in src,
   "33.1: his English copies of other first years are not offered as maybe that work")

# --- 11 (case 4.3, minor, replay): the order over the stock keeps the copy language asked
ok("_ed34 = [x for x in _base if ((x[2].get(\"language\") or \"ar\").strip() or \"ar\") == edition]" in src, "4.3: «أرخص … بالإنجليزي» orders English copies")

# --- 12 (case 25.2, minor, replay): no Arabic copy called English; «سمّ لي الباقي» is the whole list
ok("والنسخة الإنجليزية منها كذلك مع ذكر أنها بالإنجليزية" not in src and "(كل النسخ المذكورة عربية: لا تقل عن أي منها إنها بالإنجليزية)" in src,
   "25.2: the English-copy note names only English copies")
ok(R.list_all_asked33("سمّ لي الباقي") and R.list_all_asked33("name the rest") and not R.list_all_asked33("سمّ لي واحد"), "25.2: «الباقي» / \"the rest\" is the whole list")

# --- 13 (case 1.2, replay): «شي ثاني غير كيكي» keeps the young readers' path
ok("_ya30 = (pprefs.get(\"audience\") == \"young_adult\" and not author_only and (not it[\"mentioned_titles\"] or _other34)" in src,
   "1.2: a title named only as the one shown, to ask for another, keeps the young path")

# --- 14 (case 8, replay): five is a number of books
ok(R.books_count_asked33("List five classic novels you carry in English editions") == 5 and R.books_count_asked33("That's only two, I asked for five.") == 5
   and R.books_count_asked33("سم لي خمس روايات") == 5 and R.books_count_asked33("five stars please") is None, "8: «five / خمس» books asked")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
