
"""[QADFIX29] The errors of the QAD round 2026-11-06 (Ibrahim «اصلحها»): 2.1 «بني الأحمر / قصر الحمراء» without the word غرناطة;
4.1–4.2 a page range and «أقصر رواية عندكم أصلاً»; 8.2 «الحيوانات البحرية» for a child; 10.1–10.2 the decade a story takes place in;
1.2–1.3 women on a prize shortlist; 31.2 a reply left with a question only; 37.1 / 42.1 a card the reply never names; 3.2 Latin
titles in an Arabic reply."""
import os, sys
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

# --- 1 (case 2.1, grave): the names of the Nasrid kingdom are Granada
ok(A.place_alias29("بنو الأحمر") == "غرناطة" and A.place_alias29("تسليم قصر الحمراء") == "غرناطة"
   and A.place_alias29("آخر سلاطين بني الأحمر") == "غرناطة", "2.1: «بنو الأحمر» / «قصر الحمراء» → «غرناطة»")
ok(A.place_alias29("النصريين في الأندلس") == "غرناطة" and A.place_alias29("the Alhambra palace") == "غرناطة"
   and A.place_alias29("أبو عبدالله الصغير") == "غرناطة", "the Nasrids, the Alhambra, Boabdil")
ok(A.place_alias29("الوردة الحمراء") == "الوردة الحمراء" and A.place_alias29("البحر الأحمر") == "البحر الأحمر",
   "a red rose or the Red Sea stays what it is")
ok("unsupported = list(dict.fromkeys(decade_only29(place_alias29(u)) for u in unsupported))" in src,
   "the wishes are rewritten before they are cut into subjects")

# --- 2 (cases 4.1–4.2, grave): a page range, and the shortest of the whole stock
ok(R.page_limit(["رواية تخلص في قعدة وحدة، يعني بين ٨٠ و١٥٠ صفحة"]) == (150, 80), "4.1: «بين ٨٠ و١٥٠ صفحة» = 80–150")
ok(R.page_limit(["من 200 الى 300 صفحة"]) == (300, 200) and R.page_limit(["between 100 and 200 pages"]) == (200, 100),
   "«من … إلى …» and 'between … and …'")
ok(R.page_limit(["أقل من ١٠٠ صفحة"]) == (99, None) and R.page_limit(["فوق السبعمية صفحة"]) == (None, 701),
   "QADFIX27 limits unchanged")
ok(R.page_limit(["بين القصرين"]) == (None, None) and R.page_limit(["رواية من 1950 الى 1960"]) == (None, None),
   "«بين القصرين» and a range of years are no page limit")
ok(R.absolute_asked("وش أقصر رواية عندكم أصلاً؟") and R.absolute_asked("what's the shortest novel you have at all?")
   and not R.absolute_asked("وش أقصر رواية؟") and not R.absolute_asked("أقصر"), "4.2: «أصلاً / at all» with a superlative")
ok('pages_max, pages_min = chat_rules.page_limit([_last_msg])' in src and 'it["absolute29"] = True' in src
   and '_shown_only = False   # QADFIX29 #2' in src, "4.2: only a limit said now, over the whole stock (not among the shown books)")
ok('unsupported = [u for u in unsupported if not (chat_rules.is_pages_phrase(u) and re.search(r"\\d", chat_rules.num_canon(u)))]' in src,
   "4.1: a number of pages is never a subject («صفحه»)")

# --- 3 (case 8.2, grave): the sea animals
ok(A.place_alias29("الحيوانات البحرية") == "البحر" and A.place_alias29("sea animals") == "البحر" and A.place_alias29("عالم البحار") == "البحر",
   "8.2: «الحيوانات البحرية» → «البحر»")
ok(A.place_alias29("الحيوانات") == "الحيوانات", "animals alone stay animals")
_bh = set().union(*T.content_words("البحريه"))
ok({"محيط", "بحر"} <= _bh, "«البحرية» finds the sea and the ocean")
_sea = set().union(*T.content_words("البحر"))
_fish = set().union(*T.content_words("سمكه"))
ok("سمكه" not in _sea and "بحر" in _fish, "a fish leads to the sea, the sea does not lead to every fish (one way)")

# --- 4 (cases 10.1–10.2, grave): the decade the story takes place in
ok(A.decade_only29("أحداث تقع في الستينات") == "الستينات" and A.decade_only29("الستينات") == "الستينات"
   and A.decade_only29("القاهرة في الستينات") == "القاهرة في الستينات", "10.2: «أحداث تقع في الستينات» → «الستينات»; a place stays")
def bk(summary, hook=""):
    return {"summary": summary, "hook": hook, "setting": "", "subjects": "", "keywords": ""}
ok(A.decade_fit29(bk("في القاهرة الستينيات يتعلق الراوي بضحى"), 1960), "10.1: «في القاهرة الستينيات» (قالت ضحى) is set in the 1960s")
ok(A.decade_fit29(bk("سنوات حرب الاستنزاف التي تلت هزيمة 1967"), 1960) and A.decade_fit29(bk("set in the sixties"), 1960),
   "a year of the decade, or the English decade")
ok(not A.decade_fit29(bk("رجل ستيني يعود إلى القاهرة بعد أربعين عامًا"), 1960) and not A.decade_fit29(bk("تدور بين 1468 و1499"), 1960),
   "«رجل ستيني» (a man of sixty) and other years are not the decade")
ok(A.decade_name29(1960) == "الستينات" and A.decade_name29(1980, "en") == "the 1980s", "decade names")
ok('it["decade29"] = _dec29' in src and 'نبذته تذكر أن أحداثه في' in src and "هي اللي تدور أحداثها في ذلك العقد" in src,
   "10: the books whose text names the decade come first, and the guidance says so")

# --- 5 (cases 1.2–1.3, medium): women on a prize shortlist
ok("_SHORTLIST_RX.search(\" \".join(normalize(u).split()))" in src, "1.2: «القائمة القصيرة» is the prize result asked, never a subject")
ok("{'، '.join(_ls27[:10])}" in src and "{', '.join(_ls27[:10])}" in src, "1.3: all the women on the list are named (not 3)")
ok('q7["women"] = ([x[2]["title"] for x in _wf[:12]' in src, "1.3: every woman's book in the pool is said to be by a woman")

# --- 6 (case 31.2, medium): a reply left with a question only
ok('_lead29 = ((f"«{_miss29[0][\'title\']}» itself is not in stock now."' in src and "نفسها غير متوفرة عندنا الآن." in src,
   "31.2: the trimmed reply says the book asked for is not in stock")

# --- 7 (cases 37.1, 42.1, minor): a card the reply never names
ok('debug["unnamed_cards29"] = _dropn29' in src and "(n >= 2 or not (all_books.get(i, {}).get(\"summary\") or \"\").strip())" in src,
   "37.1 / 42.1: an unnamed third card, or one with no summary, goes; the first two stay (3.1 omnibus)")

# --- 8 (case 3.2, minor): Arabic titles in an Arabic reply
ok("في الرد العربي اكتب عناوين الأعمال بالعربية" in src, "3.2: the parts of a volume in Arabic")

# --- 9 (case 3.1, found again on the fix's replay): a part the volume's text does not name
import json as _json
_op = _json.load(open(os.path.join(os.path.dirname(A.__file__), "omnibus_parts.json"), encoding="utf-8"))
ok(all(v.get("source", "").startswith("https://") and v.get("parts") for v in _op["volumes"]), "omnibus_parts.json: every volume has its parts and a source")
_ab = {1: {"book_id": 1, "title": "ثلاثية نيويورك", "author": "بول أوستر", "quantity": 2, "active": 1}}
A._OMNIBUS_PARTS29["v"] = [{"book_id": 1, "parts": ["الغرف الموصدة", "أشباح"]}]
_r = A.omnibus_part29(["الغرف الموصدة"], "عندكم «الغرف الموصدة» لبول أوستر؟", _ab)
ok(bool(_r) and _r[0]["book_id"] == 1 and _r[1] == ["الغرف الموصدة"], "3.1: «الغرف الموصدة» → «ثلاثية نيويورك»")
ok(A.omnibus_part29(["أشباح"], "عندكم رواية أشباح؟", _ab) is None and A.omnibus_part29(["أشباح"], "أشباح لبول أوستر", _ab),
   "a one-word part only with the author named")
_ab[2] = {"book_id": 2, "title": "الغرف الموصدة", "author": "x", "quantity": 1, "active": 1}
ok(A.omnibus_part29(["الغرف الموصدة"], "الغرف الموصدة لبول أوستر", _ab) is None, "a book of its own with that title in stock: no volume")
A._OMNIBUS_PARTS29.clear()
ok("_op29 = omnibus_part29(it[\"mentioned_titles\"], user_messages[-1], all_books)" in src, "used also when the author is named (author_only)")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
