"""Deterministic chat rules (QAFIX, 2026-10-11), written after the daily test round of that day
(research/chat_tests_2026-10-11/report.md). Ibrahim: «ابغى اصلاحات جذرية لها».

The rounds kept finding the same families of errors, each patched case by case in app.py:
- «غير متوفر» said about a book we have, because only the interpreter's rewording of the title was
  looked up (a transliterated English title, digits written as words, a series name, a later part);
- wishes that lived for one turn in the model's reading of the conversation (a refused country or
  kind of book, a price) or were not understood at all;
- the reader mixed up when two children were named.
Everything here is plain text work on the visitor's own words: no model, no database. app.py calls it.
"""
import re
import threading

import keyword_prefs
from keyword_prefs import normalize, strip_al

# ---------------------------------------------------------------------------
# Numbers: «عشر دقائق وثمانٍ وثلاثون ثانية» = «10 دقائق و38 ثانية», «مئة عام من العزلة» = «100 عام …»
# ---------------------------------------------------------------------------
_NUM = {}


def _add(words, value):
    for w in words.split():
        _NUM[normalize(w)] = value


_add("صفر zero", 0)
_add("واحد واحدة إحدى one", 1)
_add("اثنين اثنان اثنتين اثنتان ثنتين two", 2)
_add("ثلاث ثلاثة three", 3)
_add("أربع أربعة four", 4)
_add("خمس خمسة five", 5)
_add("ست ستة six", 6)
_add("سبع سبعة seven", 7)
_add("ثمان ثماني ثمانية eight", 8)
_add("تسع تسعة nine", 9)
_add("عشر عشرة ten", 10)
_add("احدعشر احدعش eleven", 11)
_add("اثنعشر اثنعش twelve", 12)
_add("خمسطعش خمسة_عشر fifteen", 15)
_add("عشرين عشرون twenty", 20)
_add("ثلاثين ثلاثون thirty", 30)
_add("أربعين أربعون forty", 40)
_add("خمسين خمسون fifty", 50)
_add("ستين ستون sixty", 60)
_add("سبعين سبعون seventy", 70)
_add("ثمانين ثمانون eighty", 80)
_add("تسعين تسعون ninety", 90)
_add("مئة مائة مية hundred", 100)
_add("مئتين مائتين ميتين مئتان", 200)
_add("ألف thousand", 1000)
_add("ألفين ألفان", 2000)
# QADFIX27 (QAD 2026-11-05 case 15.1, grave): «فوق السبعمية صفحة» — the hundreds as one word
_add("ثلاثمية ثلاثمئة ثلاثمائة ثلثمية ثلاثميه", 300)
_add("أربعمية أربعمئة أربعمائة ربعمية", 400)
_add("خمسمية خمسمئة خمسمائة", 500)
_add("ستمية ستمئة ستمائة", 600)
_add("سبعمية سبعمئة سبعمائة", 700)
_add("ثمانمية ثمانمئة ثمانمائة ثمنمية", 800)
_add("تسعمية تسعمئة تسعمائة", 900)


def _num_token(tok):
    """-> (attached prefix, value) when the word is a number («وثلاثون» -> ("و", 30)), else None."""
    for pre in ("", "و", "ب", "ل"):
        if pre and not tok.startswith(pre):
            continue
        rest = tok[len(pre):]
        if not rest:
            continue
        if rest.isdigit() and len(rest) <= 4:
            return pre, int(rest)
        if rest in _NUM and (not pre or len(rest) >= 3):
            return pre, _NUM[rest]
    return None


def num_canon(text):
    """Normalized text with every number written in digits."""
    toks = normalize(text).split()
    out, i = [], 0
    while i < len(toks):
        nt = _num_token(toks[i])
        if nt is None:
            out.append(toks[i])
            i += 1
            continue
        pre, val = nt
        j = i + 1
        while j < len(toks):
            nx = _num_token(toks[j])
            if nx is None or nx[0] != "و":
                break
            if ((val < 10 and 20 <= nx[1] < 100 and nx[1] % 10 == 0) or (100 <= val and nx[1] < 100 and val % 100 == 0)
                    or (1000 <= val and nx[1] < 1000 and val % 1000 == 0)):   # QADFIX27: «ألف وخمسمية» = 1500
                val += nx[1]
                j += 1
            else:
                break
        out.append(pre + str(val))
        i = j
    return " ".join(out)


_key_cache = {}


def title_key(text):
    """A title as it is compared: normalized, numbers in digits, hamza seats folded («دقائق» = «دقايق»)."""
    text = str(text or "")
    k = _key_cache.get(text)
    if k is None:
        k = " ".join(num_canon(text).replace("ئ", "ي").replace("ؤ", "و").replace("ء", "").split())
        if len(_key_cache) > 20000:
            _key_cache.clear()
        _key_cache[text] = k
    return k


_T_STOP = {normalize(w) for w in "في من على عن إلى الى هذا هذه ذلك تلك و او أو مع ما هو هي "
           "the a an of and in to for رواية كتاب روايه".split()}


def title_tokens(text):
    out = []
    for w in title_key(text).split():
        if w[:1] in "وبل" and w[1:].isdigit():
            w = w[1:]
        w = strip_al(w)
        if w in _T_STOP or (len(w) < 3 and not w.isdigit()):
            continue
        out.append(w)
    return out


def title_subset(title, mention):
    """Every meaningful word of the mention is in the title, in any order: at least two of them and
    one that is not a number («عشر دقايق و٣٨ ثانية» inside «10 دقائق و38 ثانية في هذا العالم الغريب»).
    -> the number of title words the mention did not say, or None when it does not match."""
    mt, tt = set(title_tokens(mention)), set(title_tokens(title))
    if len(mt) < 2 or not any(not w.isdigit() for w in mt) or not mt <= tt:
        return None
    return len(tt - mt)


def _avail(b):
    return bool(b.get("active")) and (b.get("quantity") or 0) > 0


# ---------------------------------------------------------------------------
# A title in Latin letters, as the visitor typed it: "Do you have Dune?" (the interpreter rewrites
# titles in Arabic, «كثيب», and the English copy on the shelf was never tried)
# ---------------------------------------------------------------------------
_EN_COMMON = {"home", "love", "life", "book", "books", "novel", "novels", "english", "arabic", "story", "stories",
              "poems", "poetry", "history", "gift", "science", "fiction", "more", "about", "have", "want", "like"}


def latin_titles(message, all_books, limit=2):
    raw = str(message or "")
    if not re.search(r"[A-Za-z]{3}", raw):
        return []
    msg = " " + " ".join(normalize(raw).split()) + " "
    n_words = len(msg.split())
    hits = []
    for b in all_books.values():
        title = str(b.get("title") or "")
        if not re.search(r"[A-Za-z]", title):
            continue
        for form in {title, re.split(r"[:(\[]| - ", title)[0]}:
            nt = " ".join(normalize(form).split())
            if len(nt) < 4 or " " + nt + " " not in msg:
                continue
            words = nt.split()
            if len(words) == 1 and (nt in _EN_COMMON or (form.strip() not in raw and n_words > 5)):
                continue   # a one-word title: typed with its capital letter, or the message is short
            hits.append((-len(words), not _avail(b), b["book_id"], nt, b))
            break
    hits.sort(key=lambda x: x[:3])
    out, keys = [], []
    for _, _, _, nt, b in hits:
        if any(" " + nt + " " in " " + k + " " and nt != k for k in keys):
            continue   # «Dune» inside «Dune Messiah» that also matched
        keys.append(nt)
        if b["book_id"] not in [x["book_id"] for x in out]:
            out.append(b)
    return out[:limit]


# ---------------------------------------------------------------------------
# A series by its name alone («ثلاثية القاهرة»، «رباعية نابولي») and «الجزء الثاني» of a book
# ---------------------------------------------------------------------------
def series_books(mention, all_books):
    mw = {w for w in (strip_al(x) for x in title_key(mention).split()) if w not in _T_STOP}
    if len(mw) < 2:
        # QADFIX32 #1 (QAD 2026-11-09 case 46.1, grave): «عندكم كل أجزاء ميلينيوم؟» → «سلسلة ميلينيوم غير متوفرة» while its four parts
        # are in stock (series_name «ميلينيوم», 2829 / 2823 / 4231 / 4209): a one-word name is the series when it is that series' WHOLE
        # name (5 letters or more, two parts or more in our records) — never one word of a longer series name
        return _series_one_word32(mw, all_books)
    out = []
    for b in all_books.values():
        s = b.get("series_name")
        if not s:
            continue
        sw = {strip_al(w) for w in title_key(s).split()}
        if mw == sw or mw <= sw:
            out.append(b)
    if not out and mw & _SERIES_WORDS32:
        # QADFIX32 #1: «سلسلة ميلينيوم»، «كل أجزاء ميلينيوم»، "the Millennium series" — the word «series» is not part of its name
        rest = mw - _SERIES_WORDS32
        return series_books(" ".join(sorted(rest)), all_books) if rest else []
    out.sort(key=lambda b: (b.get("series_position") or 99, b["book_id"]))
    return out


_SERIES_WORDS32 = {"سلسله", "سلسلة", "سلاسل", "اجزاء", "اجزا", "جميع", "كل", "كامله", "كامل", "series", "saga", "books", "all"}


def _series_one_word32(mw, all_books):
    """QADFIX32 #1: {"ميلينيوم"} -> the parts of the series named exactly «ميلينيوم», in order; [] otherwise."""
    if len(mw) != 1:
        return []
    w = next(iter(mw))
    if len(w) < 5:
        return []
    out = [b for b in all_books.values() if b.get("series_name")
           and [strip_al(x) for x in title_key(b["series_name"]).split()] == [w]]
    if len(out) < 2:
        return []
    out.sort(key=lambda b: (b.get("series_position") or 99, b["book_id"]))
    return out


_ORD = {normalize(k): v for k, v in {
    "الأول": 1, "الأولى": 1, "الثاني": 2, "الثانية": 2, "الثالث": 3, "الثالثة": 3, "الرابع": 4, "الرابعة": 4,
    "الخامس": 5, "الخامسة": 5, "السادس": 6, "السابع": 7, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}.items()}
_PART = re.compile(r"(?:الجزء|جزء|جزئه|المجلد|part|volume|vol)\s+(\S+)")
_NEXT = re.compile(r"(?:الجزء|الكتاب) (?:التالي|القادم|اللي بعده|الي بعده|الذي بعده|اللي يليه)|(?:^| )(?:اللي|الي) بعده(?: |$)"
                   r"|(?:^| )(?:تكملته|تكملتها|التكمله|تكمله)(?: |$)|next (?:part|book|one|volume)|(?:^| )sequel(?: |$)")


# «هل له أجزاء ثانية؟»، «باقي السلسلة؟» (staff chat 2026-10-13 22:59, «اسم الريح»)
_ALL = re.compile(r"(?:^| )(?:اجزاء|اجزاءه|اجزاوه|اجزاؤه|اجزائه|اجزاءها|اجزاوها|اجزاؤها|اجزائها|سلسلته|سلسلتها)(?: |$)"
                  r"|(?:بقيه|باقي|كامل) (?:ال)?(?:اجزاء|سلسله)|other parts|more parts|rest of the series|other books in the series"
                  # «كم جزء سلسلة كينغسبريدج؟» (open question 17, MGR 2026-10-17)
                  r"|(?:^| )كم (?:عدد )?(?:جزء|اجزاء|مجلد|مجلدات)(?: |$)|how many (?:parts|volumes|books in (?:the |this )?series)"
                  # QADFIX9 (QAD 2026-10-20, case 5.2): «ثلاثية التوأمين عندكم كاملة؟»
                  r"|(?:^| )(?:ثلاثيه|رباعيه|خماسيه|سلسله|السلسله|الثلاثيه)(?: .*)? (?:كامله|كاملين|بالكامل|كلها)(?: |$)"
                  r"|(?:^| )كل (?:ال)?(?:اجزاء|اجزائها|اجزاءها)(?: |$)|the whole (?:series|trilogy)|complete (?:series|trilogy)")


def part_asked(text):
    """«والجزء الثاني؟» -> 2, «وش الجزء اللي بعده؟» -> "next", «هل له أجزاء ثانية؟» -> "all", else None."""
    t = " ".join(normalize(text).split())
    for m in _PART.finditer(t):
        w = m.group(1)
        if w.isdigit() and 1 <= int(w) <= 30:
            return int(w)
        if w in _ORD:
            return _ORD[w]
    if _NEXT.search(t):
        return "next"
    return "all" if _ALL.search(t) else None


def series_others(anchor, all_books):
    """The other books of the anchor's series in our records (by series_name, or catalogued under the first
    part's title), in reading order."""
    series = title_key(anchor.get("series_name")) or title_key(anchor.get("title"))
    out = [b for b in all_books.values() if b["book_id"] != anchor["book_id"]
           and series and title_key(b.get("series_name")) == series]
    out.sort(key=lambda b: (b.get("series_position") or 99, b["book_id"]))
    return out


def next_part(anchor, want, all_books):
    """The part `want` (a number or "next") of the series the anchor book belongs to -> book or None.
    The first part is often catalogued without a series name and the later ones under its title
    («حكاية الجارية» / «العهود: حكاية الجارية (ج 2)»)."""
    series, pos = title_key(anchor.get("series_name")), anchor.get("series_position")
    if series:
        n = (pos or 1) + 1 if want == "next" else want
        cands = [b for b in all_books.values()
                 if title_key(b.get("series_name")) == series and b.get("series_position") == n]
    else:
        n = 2 if want == "next" else want
        t = title_key(anchor.get("title"))
        cands = [b for b in all_books.values() if b["book_id"] != anchor["book_id"]
                 and title_key(b.get("series_name")) == t and b.get("series_position") == n]
    cands.sort(key=lambda b: (not _avail(b), b["book_id"]))
    return cands[0] if cands else None


# ---------------------------------------------------------------------------
# Price: «رواية ما تتعدى ٢٥ ريال»، «بعشرة ريال»، «وش أرخص رواية؟»
# ---------------------------------------------------------------------------
# QADFIX12 #10 (QAD 2026-10-22, case 44.2): the number stands on its own («٢٥ ريال»، «بـ٢٥ ريال»), never inside a word:
# Arabizi «a2sar» (أقصر) is not «2 sar»
_PRICE = re.compile(r"(?:^|(?<=\s))[وبل]?(\d{1,4})\s*(?:ريال|ريالات|ر س|sar|sr|riyal|riyals)(?:\s|$)")
_PRICE_ANY = re.compile(r"(?:اي|مو مهم|مب مهم|مو شرط|ما يهم|ما يهمني) (?:ال)?(?:سعر|اسعار)|السعر (?:مو مهم|ما يهم|مب مهم)"
                        r"|any price|price (?:doesn t|does not) matter")
# QADFIX31 #1 (QAD 2026-11-08 case 1.3, grave): «وأغلى كتاب أطفال؟» after two turns → «ما أقدر أحدد أغلى كتاب أطفال» — a superlative
# word may carry «و / ف / ب» in front of it («والأرخص»، «وأقصر»، «فالأطول»)
_CHEAPEST = re.compile(r"(?:^| )[وفب]?(?:ال)?(?:ارخص|رخيص|رخيصه|cheapest|cheaper|cheap)(?: |$)|اقل سعر|lowest price")
_PRICE_WORD = re.compile(r"ريال|سعر|اسعار|رخيص|ارخص|ميزاني|price|cheap|budget|(?:^| )sar(?: |$)"
                         # QADFIX30 #1 (QAD 2026-11-07 case 46.2): «أغلى كتاب» went to unsupported_requests and was searched as a subject
                         r"|(?:^| )(?:ال)?(?:اغلي|اغلا)(?: |$)|expensive|priciest")
# QADFIX30 #1 (QAD 2026-11-07 cases 46.1–46.2, grave): «أغلى كتاب فنون عندكم كم سعره؟» → «المنمنمات الإسلامية ٥٢٫٣٠» from the few
# candidates, while «The Cosmic Script» (700) is in stock: the most expensive is an order on books.price, like «الأرخص»
_DEAREST = re.compile(r"(?:^| )[وفب]?(?:ال)?(?:اغلي|اغلا)(?: |$)(?!من \d)|(?:^| )[وفب]?(?:ال)?اعلي (?:سعر|سعرا|سعرن)(?: |$)|اكثر (?:كتاب |واحد )?(?:غلا|سعرا)(?: |$)"
                      r"|most expensive|priciest|highest price|dearest")


def dearest_asked(text):
    """«أغلى كتاب فنون عندكم»، «الأعلى سعرًا»، "the most expensive art book" -> True (QADFIX30 #1)."""
    t = " ".join(normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split())
    return bool(_DEAREST.search(t))


def price_wish(user_messages):
    """-> (the most the visitor wants to pay, or None; whether the latest message asks for the cheapest).
    The limit stays until the visitor lifts it («مو مهم السعر») or names another."""
    limit = None
    for text in user_messages:
        t = " ".join(num_canon(text).split())
        if _PRICE_ANY.search(t):
            limit = None
        nums = [int(m.group(1)) for m in _PRICE.finditer(t)]
        if nums:
            limit = max(nums) if re.search(r"(?:^| )(?:بين|between)(?: |$)", t) else nums[-1]
    last = " ".join(normalize(user_messages[-1] if user_messages else "").split())
    return (limit if limit and limit > 0 else None), bool(_CHEAPEST.search(last))


def price_said(text):
    t = " ".join(num_canon(text).split())
    return bool(_PRICE.search(t) or _CHEAPEST.search(t))


def is_price_phrase(phrase):
    """«ريال»، «سعر أقل من ٢٥»: never a subject to search books by."""
    return bool(_PRICE_WORD.search(" ".join(normalize(phrase).split())))


_SHORTEST = re.compile(r"(?:^| )[وفب]?(?:ال)?اقصر(?: |$)|(?:^| )(?:shortest|shorter)(?: |$)")   # QADFIX31 #1: «وأقصر»
_SUPERLATIVE = re.compile(r"الاقصر|اقصر (?:كتاب|روايه|قصه|شي|واحد|وحده|عمل|ديوان)|shortest")
# QADFIX32 #2 (QAD 2026-11-09 case 1.1, the other side of «أضخم»): «أنحف كتاب»، «أصغر رواية حجمًا»، «أقل عدد صفحات»، "thinnest" — the
# shortest («أصغر» alone is not: «أصغر قارئ»، «للأصغر سنًا»; «أقل من ١٠٠ صفحة» is a page limit)
_SHORTEST32 = re.compile(r"(?:^| )[وفب]?(?:ال)?(?:انحف|انحفها|انحفهم)(?: |$)"
                         r"|(?:^| )[وفب]?(?:ال)?(?:انحف|اصغر) (?:كتاب|روايه|مجلد|ديوان|عمل|قصه|واحد|وحده|شي)(?: |$)(?!.*(?<!\S)(?:سن|سنا|عمر|عمرا|عمره|عمرها)(?!\S))"
                         r"|(?:^| )[وفب]?(?:ال)?(?:اصغر|اصغرها|اصغرهم) (?:\S+ )?(?:حجما|حجم|من حيث الحجم|من ناحيه الحجم)(?: |$)"
                         r"|(?:^| )[وفب]?(?:ال)?(?:اقل|اقلها|اقلهم) (?:\S+ )?(?:\S+ )?(?:عدد (?:ال)?صفحات|صفحات|بالصفحات"
                         r"|من (?:حيث|ناحيه) (?:عدد )?(?:ال)?صفحات)(?: |$)"
                         r"|(?:^| )(?:thinnest|smallest (?:book|novel|one|volume)|fewest pages|least pages)(?: |$)")


# QADFIX29 #2 (QAD 2026-11-06 case 4.2, grave): «وش أقصر رواية عندكم أصلاً؟» after «بين ٨٠ و١٥٠ صفحة» → «حارس ذكريات» 144 pages,
# while «الشاب» has 48: «أصلًا / على الإطلاق / في المكتبة كلها» asks over everything in stock, not within the wishes said before
_ABSOLUTE29 = re.compile(r"(?:^| )(?:اصلا|اصلن|علي الاطلاق|بالاطلاق|في المكتبه كلها|بالمكتبه كلها|في المكتبه|بالمكتبه|عندكم كلها"
                         r"|من كل الكتب|من كل الروايات|بين كل|كلها|at all|of all|in the (?:whole )?(?:shop|library|store)|overall|ever"
                         # QADFIX30 #1 (QAD 2026-11-07 case 46.2): «… على الإطلاق، مو من المعروض» — not among the books shown
                         r"|(?:مو|مب|مش|موب|ليس|لا) (?:من )?(?:المعروض|المعروضه|اللي عرضتي|اللي عرضتيها|اللي عرضتها|الكتب المعروضه)"
                         r"|not (?:just )?(?:from )?(?:the ones|these|those) (?:you )?(?:shown|showed))(?: |$)")


# QADFIX33 #3 (QAD 2026-11-10 cases 33.1–33.2, grave): «الغونكور، وش أحدث رواية فائزة فيها عندكم؟» → «حديثة (من ٢٠٢١)» and «عندنا كتابان»
# while seven winners are in stock: «أحدث / الأحدث / آخر فائزة / latest / newest» is an order (the newest first), not the wish «a recent
# prize» («حديثة / مؤخرًا / جديدة / السنوات الأخيرة / recent / recently»), and «كلها / كل الفائزة / سمّها كلها / all of them» asks for the
# whole list
_NEWEST33 = re.compile(r"(?:^| )[وفب]?(?:ال)?احدث(?:ها|هم|هن)?(?: |$)"
                       r"|(?:^| )[وفب]?(?:اخر|الاخير|الاخيره) (?:روايه|كتاب|واحده|واحد|فائز|فائزه|فايز|فايزه|الفائزين|الفائزات|فوز|من فاز|وحده)(?: |$)"
                       r"|latest|newest|most recent")
_RECENT_FILTER33 = re.compile(r"(?:^| )[وفب]?(?:ال)?(?:حديث|حديثه|حديثا|حديثاً|مؤخرا|مؤخراً|جديد|جديده|الجديد|الجديده)(?: |$)"
                              r"|السنوات الاخيره|السنين الاخيره|اخر (?:\S+ )?(?:سنوات|سنين|سنتين)|(?:^| )recent(?:ly)?(?: |$)|in recent years|last few years")
_LIST_ALL33 = re.compile(r"(?:^| )[وب]?(?:كلها|كلهم|كلهن|جميعها|جميعهم|بالكامل|كامله)(?: |$)"
                         r"|(?:^| )كل (?:ال)?(?:فائز|فايز|فائزه|فايزه|الفائزه|الفايزه|الفائزين|الفايزين|اللي فاز|اللي فازت|الكتب|الروايات)"
                         r"|all of them|them all|the whole list|full list|complete list|every one of them|all the (?:winners|books|novels)"
                         # QADFIX34 #3 (case 25.2 «سمّ لي الباقي» → two of four and «أذكر لك المزيد؟»): the rest is the whole list too
                         r"|(?:^| )[وب]?(?:الباقي|الباقيه|البقيه|باقيها|باقيهم|الباقين)(?: |$)|the rest(?: of them)?|the others")


def newest_asked33(text):
    """«وش أحدث رواية فائزة؟»، «آخر فائزة»، "the latest winner" -> True (an order, the newest first)."""
    return bool(_NEWEST33.search(" ".join(normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split()).lower()))


def recent_filter_said33(text):
    """«فازت حديثًا»، «جوائز جديدة»، «السنوات الأخيرة»، "recently" -> True (a span of recent years); «أحدث» alone -> False."""
    return bool(_RECENT_FILTER33.search(" ".join(normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split()).lower()))


def list_all_asked33(text):
    """«لا أبيها كلها، كل الفائزة بالغونكور عندكم»، «سمّها كلها»، "list them all" -> True."""
    return bool(_LIST_ALL33.search(" ".join(normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split()).lower()))


# QADFIX35 #1 (QAD 2026-11-12 cases 28.1–28.3, grave; the mirror of QADFIX33 #3): «عندكم كتب فازت بجائزة غونكور؟ ابي أقدمها» → no cards, then
# «من أقدمها العاشق (١٩٨٤)» and «العاشق طبعتنا ٢٠١٧» while «بؤساء بلفيل» won in 1975: «أقدم / الأقدم / أقدمها / أول فائزة / oldest / earliest»
# on a prize is an order by the year of the prize, oldest first, over every winner in stock
_OLDEST35 = re.compile(r"(?:^| )[وفب]?(?:ال)?اقدم(?:ها|هم|هن)?(?: |$)"
                       r"|(?:^| )[وفب]?(?:اول|الاول|الاولي) (?:روايه|كتاب|واحده|واحد|فائز|فائزه|فايز|فايزه|الفائزين|الفائزات|فوز|من فاز|وحده)(?: |$)"
                       r"|oldest|earliest|first (?:winner|one to win|book to win|novel to win)")
_PRIZE_WORD35 = re.compile(r"فاز|فايز|فوز|جائز|جايز|جوايز|جوائز|won|winner|winning|prize|award")
_OTHER_ORDER35 = re.compile(r"(?:^| )[وفب]?(?:ال)?(?:احدث|ارخص|اغلي|اقصر|اطول|انحف|اسمك)(?:ها|هم|هن)?(?: |$)|latest|newest|cheapest|shortest|longest|most expensive|dearest")


def oldest_asked35(text):
    """«ابي أقدمها»، «وش أقدم رواية فائزة؟»، «أول فائزة»، "the oldest winner" -> True (an order, the oldest first)."""
    return bool(_OLDEST35.search(" ".join(normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split()).lower()))


def oldest_prize_kept35(user_messages):
    """«ابي أقدمها» then «يعني ما عندكم ولا وحدة فازت بالغونكور؟»: the oldest asked in the message before still holds when the latest one
    speaks of the prize again and asks no other order (QADFIX35 #1, case 28.2)."""
    if len(user_messages) < 2 or not oldest_asked35(user_messages[-2]):
        return False
    t = " ".join(normalize(re.sub(r"[؟?!.،,]", " ", user_messages[-1] or "")).split()).lower()
    return bool(_PRIZE_WORD35.search(t)) and not _OTHER_ORDER35.search(t)


# QADFIX35 #2 (QAD 2026-11-12 case 12.2, grave; the reverse of 11-11 17.2): «حفيدي بالرابع ابتدائي…» then «طيب وأنا جده، ابي رواية لي عن الريف
# والفلاحين» kept the reader at 9 (a children's book and «ما لقيت»): «وأنا + his kin to the child (جده / أبوه / أمها / خالته …)» and a wish
# «… لي / لنفسي» is the visitor himself as the reader
_KIN35 = ("جد", "جده", "جدته", "جدتها", "جدها", "جدهم", "جدتهم", "ابوه", "ابوها", "ابوهم", "امه", "امها", "امهم", "خاله", "خالته", "خالها",
          "خالتها", "عمه", "عمته", "عمها", "عمتها", "اخوه", "اخته", "اخوها", "اختها", "ابوي", "والده", "والدته", "والدها", "والدتها")
_KIN_START35 = re.compile(r"^(?:طيب |زين |اوكي |ok |okay |بس |اما )?و?انا (?:" + "|".join(_KIN35) + r")(?: |$)")


def kin_self_said35(text):
    """«طيب وأنا جده، ابي رواية لي عن الريف»، «وأنا أمها، ودي بكتاب لنفسي» -> True; «وأنا جده، ابي له رواية»، «وأنا جده، رشّحي لي شي له» -> False."""
    words = normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split()
    t = " ".join(words)
    if not _KIN_START35.search(t):
        return False
    if re.search(r"(?:^| )(?:له|لها|لهم|لهن|لحفيدي|لحفيدتي|لولدي|لبنتي|لاحفادي|يناسبه|يناسبها|تناسبه|تناسبها|عمره|عمرها)(?: |$)", t):
        return False
    if re.search(r"(?:^| )(?:لنفسي|لي انا|انا اقراه|انا اقراها|for myself|for me)(?: |$)", t):
        return True
    return any(w == "لي" and k >= 1 and (words[k - 1] not in _TELL or k + 1 < len(words) and words[k + 1] not in ("شي", "شيء"))
               for k, w in enumerate(words))


# QADFIX33 (QAD 2026-11-10 case 31.1, minor): «ناشر الكرمة، وش أحدث ثلاث كتب منه عندكم؟» → two books: the number of books asked
_NUM_WORDS33 = {"كتابين": 2, "روايتين": 2, "قصتين": 2, "ديوانين": 2, "اثنين": 2, "ثنتين": 2, "two": 2, "ثلاث": 3, "ثلاثه": 3, "three": 3,
                "اربع": 4, "اربعه": 4, "four": 4,
                # QADFIX34 #4 (QAD 2026-11-11 case 8, medium): "List five classic novels" → two, then two more: five is a number of books too
                # (a complete list of five gets five cards, QADFIX32b)
                "خمس": 5, "خمسه": 5, "five": 5}
_COUNT_NOUN33 = r"(?:كتب|كتاب|روايات|روايه|قصص|قصه|دواوين|عناوين|اصدارات|books|novels|titles|stories)"


def books_count_asked33(text):
    """«أحدث ثلاث كتب»، «٣ روايات»، «رشّح لي كتابين»، "three books" -> 3 / 3 / 2 / 3; None when no number of books is asked
    (2 to 4: the cards hold four)."""
    t = " ".join(num_canon(normalize(re.sub(r"[؟?!.،,]", " ", text or ""))).split()).lower()
    for w in ("كتابين", "روايتين", "قصتين", "ديوانين"):
        if re.search(r"(?:^| )[ول]?" + w + r"(?: |$)", t):
            return 2
    m = re.search(r"(?:^| )(اثنين|ثنتين|two|ثلاث|ثلاثه|three|اربع|اربعه|four|خمس|خمسه|five|[2-5]) (?:\S+ )?" + _COUNT_NOUN33 + r"(?: |$)", t)
    if not m:
        # QADFIX34 #4: "That's only two, I asked for five", "Just name five", «قلت لك خمس», «سمّ لي خمس»
        m = re.search(r"(?:^| )(?:asked for|name|list|give me|i said|طلبت|قلت لك|قلت|سم لي|سمي لي|سم|سمي|عطني|اعطني)"
                      r" (?:only |just |me |لي )?(اثنين|ثنتين|two|ثلاث|ثلاثه|three|اربع|اربعه|four|خمس|خمسه|five|[2-5])(?: |$)", t)
    if not m:
        return None
    w = m.group(1)
    return int(w) if w.isdigit() else _NUM_WORDS33.get(w)


# QADFIX33 #4 (QAD 2026-11-10 case 49.2, grave; 11-08 1.3 class): «وش أرخص ديوان شعر عندكم؟» → «وأغلى واحد؟» → «الأغلى ضمن طلبك هو «فن
# الرباعي» ٦٫٥٧ ريال»: the model read «واحد» as one of the two shown and the order ran among the cheapest poetry. A superlative points at
# the books just shown only when the message says so: «أيهم / أيها / منهم / فيهم / بينهم / من هذول / which / of these», or the
# superlative itself carries «ـها / ـهم» («أرخصها»، «أطولهم»)
_AMONG_SHOWN33 = re.compile(
    r"(?:^| )(?:اي|ايه|ايهم|ايها|ايهما|ايهن|مين|منو|منهم|منها|منهما|منهن|فيهم|فيها|فيهما|فيهن|بينهم|بينها|بينهما|بينهن|هذول|هذولا|هذولي"
    r"|هذا|هذي|هذه|ذول|ذولا|الاثنين|الاثنتين|الثنتين|الكتابين|الروايتين|الديوانين|المعروض|المعروضه|المعروضين|اللي عرضتي|اللي عرضتيها"
    r"|اللي عرضتها|اللي عرضتهم|اللي فوق|which|these|those|them|either|both|among|between)(?: |$)"
    r"|(?:^| )[وفب]?(?:ال)?(?:ارخص|اغلا|اغلي|اقصر|اطول|اضخم|انحف|اسمك|اقدم|احدث)(?:ها|هم|هما|هن)(?: |$)"
    r"|of (?:the|these|those) (?:two|three|four|ones)|(?:the )?(?:first|second|third) one")


def among_shown_said(text):
    """«أيهم أغلى؟»، «مين فيهم أقصر؟»، «أرخصها»، "which of these is cheaper" -> True; «وأغلى واحد؟»، «وأغلى ديوان؟» -> False."""
    t = " ".join(normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split()).lower()
    return bool(_AMONG_SHOWN33.search(t))


def stock_order_asked(text):
    """«وش أرخص ديوان شعر عندكم؟»، «أغلى كتاب فنون»، «وش أطول رواية؟»، «أنحف كتاب» -> the order over the stock asked
    ("cheapest" / "dearest" / "shortest" / "longest"), or None («أقصر شوي» is "shorter", no order over the stock) (QADFIX33 #4)."""
    if among_shown_said(text):
        return None
    t = " ".join(normalize(text or "").split())
    if _CHEAPEST.search(t):
        return "cheapest"
    if dearest_asked(text):
        return "dearest"
    if shortest_asked(text) == "shortest":
        return "shortest"
    if longest_asked(text) == "longest":
        return "longest"
    return None


def absolute_asked(text):
    """«وش أقصر رواية عندكم أصلاً؟» -> True: a superlative over the whole stock, not within the wishes said before."""
    t = " ".join(normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split())
    return bool(_ABSOLUTE29.search(t)) and bool(_SHORTEST.search(t) or _DEAREST.search(t)   # QADFIX30 #1: «أغلى … على الإطلاق»
                                                or re.search(r"(?:^| )[وفب]?(?:ال)?(?:اطول|ارخص|اقدم|احدث)(?: |$)|longest|cheapest|oldest|newest", t)
                                                or _LONGEST.search(t) or _SHORTEST32.search(t))   # QADFIX32 #2: «أضخم … أصلاً»


# QADFIX30 #4 (QAD 2026-11-07 case 45.1, grave): «… بس مو العادات الذرية لأني قريته» → «الزائر طلب «العادات الذرية» لـاني» (آني إرنو):
# «لأني / لأنه / لأن …» (because) and the other everyday words that begin with «ل» are never «لـ + an author»
_LAM_WORDS30 = {normalize(w) for w in (
    "لأني لاني لأنه لانه لأنها لانها لأنك لانك لأنكم لانكم لأنهم لانهم لأنو لانو لأن لان لأجل لاجل لأجلي لاجلي لأجلها لكي لكن لكني "
    "لما لماذا لمين لمن للأسف للاسف لازم لسه لسا لين لو لولا ليش لذلك لهذا لهذي لها له لي لنا لكم لهم ليه لاحقا لحد لغاية لغايه "
    "لوحدي لوحده لوحدها لنفسي لنفسه لنفسها لبنتي لولدي لابني لأمي لامي لأبوي لابوي لأختي لاختي لأخوي لاخوي لزوجتي لزوجي لصديقي لصديقتي "
    "لطفل لطفلة لطفله لأطفال لاطفال للأطفال للاطفال لكبار للكبار لصغار للصغار لعمر لسن").split()}


def lam_is_word(token):
    """«لأني»، «لأنه»، «لبنتي» -> True: a word of its own, not «لـ» + a name (QADFIX30 #4)."""
    return normalize(token or "") in _LAM_WORDS30


# QADFIX30 #3 / #4 (QAD 2026-11-07 cases 25.2 and 45.1, grave): «شي ثاني بس مو لمحفوظ» kept محفوظ as the author asked for; «بس مو
# العادات الذرية لأني قريته» offered the book read as the book asked for. What comes right after «مو / غير / بدون / إلا …» is refused
# («إلا» is left out: «ما أبي إلا نجيب محفوظ» means only him)
_NEG_LEAD30 = (r"(?:^| )(?:(?:بس|لكن|و|بشرط) )?(?:مو|مب|مش|موب|ماهو|ماهي|غير|بدون|عدا|ماعدا|ما عدا|ما ابي|ما ابغي|ما ابغا|ما اريد"
               r"|ومو|ومب|ومش|وغير|وبدون|not|except|but not|other than|anything but|no more)\s+")
_NEG_NAME30 = re.compile(_NEG_LEAD30 + r"(?:(?:من )?(?:كتب|تاليف|روايات|اعمال) |حق |by )?(\S+(?: \S+){0,2})")


def negated_spans(text):
    """«شي ثاني بس مو لمحفوظ» -> ["لمحفوظ"]; «غير نجيب محفوظ لو سمحت» -> ["نجيب محفوظ لو"] (up to three words each, normalized;
    the caller decides which words name an author)."""
    t = " ".join(normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split())
    return [m.group(1) for m in _NEG_NAME30.finditer(t)]


def title_refused(title, text):
    """«ابي كتاب عن بناء العادات بس مو العادات الذرية لأني قريته» -> True for «العادات الذرية»: the visitor rules this title out
    (QADFIX30 #4). «مو / مب / مش» only after «بس / لكن / و» or with «لأني قريته» after it («مو الخيميائي حلوة؟» asks)."""
    t = " ".join(normalize(re.sub(r"[؟?!.،,«»\"]", " ", title or "")).split())
    m = " " + " ".join(normalize(re.sub(r"[؟?!.،,«»\"]", " ", text or "")).split()) + " "
    if not t or (" " + t + " ") not in m:
        return False
    before, after = m.split(" " + t + " ", 1)
    before = " " + before.strip() + " "
    _read = re.match(r"\s*(?:لاني|لان|لانه|عشان|كوني|because|since|as)?\s*(?:انا )?(?:قد )?(?:قريته|قريتها|قراته|قراتها|قريت|خلصته|خلصتها"
                     r"|عندي|موجود عندي|i (?:ve |have )?(?:already )?read|already read|read it)", after)
    if re.search(r"(?:^| )(?:(?:بس|لكن|و) (?:مو|مب|مش|موب)|ومو|ومب|ومش)\s+(?:(?:ك|ال)?كتاب |(?:ال)?روايه )?$", before):
        return True
    if re.search(r"(?:^| )(?:غير|وغير|بدون|وبدون|عدا|ماعدا|ما عدا|ما ابي|ما ابغي|ما ابغا|ما اريد|except|other than|not|besides)\s+"
                 r"(?:(?:ك|ال)?كتاب |(?:ال)?روايه )?$", before):
        return True
    return bool(_read) and bool(re.search(r"(?:^| )(?:مو|مب|مش|موب)\s+$", before))


# QADFIX30 #5 (QAD 2026-11-07 case 17.3, grave): «ابي شي من رف اليافعين» names the young readers' shelf (genre «يافعين»)
_YA_SHELF30 = re.compile(r"(?:رف|قسم|ركن|جناح|كتب|روايات|ادب|اداب)\s+(?:ال)?(?:يافعين|يافعات|ناشئه|ناشئين|مراهقين|شباب)(?: |$)|(?:^| )لليافعين(?: |$)"
                         r"|young adults?(?: shelf| section| books| novels| fiction)?|(?:^| )ya (?:shelf|section|books|novels|fiction)|teen (?:shelf|section|books|fiction)")


def ya_shelf_said(text):
    """«ابي شي من رف اليافعين»، «روايات لليافعين»، "from the young adult shelf" -> True (QADFIX30 #5)."""
    t = " ".join(normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split()).lower()
    return bool(_YA_SHELF30.search(t))


def shortest_asked(text):
    """-> "shortest" («وش أقصر كتاب له؟»), "shorter" («أقصر» after some books), or None."""
    t = " ".join(normalize(text).split())
    if not _SHORTEST.search(t):
        if _SHORTEST32.search(" ".join(num_canon(re.sub(r"[؟?!.،,]", " ", t)).split())) and not re.search(
                r"(?:^| )(?:اقل|اصغر|انحف) من \d|(?:fewer|less|smaller) than \d", num_canon(t)):
            return "shortest"   # QADFIX32 #2: «أنحف كتاب»، «أقل عدد صفحات»
        return None
    return "shortest" if _SUPERLATIVE.search(t) else "shorter"


# ---------------------------------------------------------------------------
# A kind of book the visitor refuses: «أي شي إلا تطوير الذات والروايات»، «ما أبي روايات ولا شعر»
# ---------------------------------------------------------------------------
_EXCEPT = re.compile(r"(?:^| )(?:الا|ماعدا|ما عدا|عدا|except|but not|other than)(?: |$)")


_EXIST_Q = re.compile(r"(?:^| )(?:ما|مافيه|ماعندكم|ماعندك)(?: (?:فيه|في|عندكم|عندك|عندكن|يوجد|لقيت|حصلت|شفت|القي|احصل))?(?= )"
                      r"(?=(?: \S+){1,6}$)(?! (?:ابي|ابغي|ابغا|اريد|احب|ودي|نبي|يكون|تكون|ابيه|ابيها))")


def excluded_genres(user_messages):
    """The catalog genres the visitor said he does not want, kept until he asks for one of them."""
    out = []
    for text in user_messages:
        # «ما فيه رواية بعشرة ريال؟»، «ما عندكم روايات؟» ask whether we have one; nothing is refused
        text = _EXIST_Q.sub(" ", " ".join(normalize(text).split()))
        # «لا، ابي قصص مصورة عربية» answers the question before it: that «لا» refuses nothing that follows
        text = re.sub(r"^(?:لا|لاء|no|nope)(?: |$)", "", text)
        # QADFIX11 (QAD 2026-10-21, case 34.1): «إلا الروايات المترجمة» refuses translated ones only, not the kind
        text = re.sub(r"(?:^| )(?:الا|غير|ماعدا|عدا|بدون|بلا|مو|مب|ما ابي|ماابي|ما ابغى)\s+(?:ال)?(?:روايات|روايه|قصص|كتب)\s+(?:ال)?(?:مترجمه|مترجم|مترجمات)(?= |$)",
                      " ", " " + text + " ").strip()
        p, flags = keyword_prefs._parse_one(text)
        neg, pos = list(flags["genres_negated"]), list(p["genres"])
        t = " ".join(normalize(text).split())
        if neg and not any(w in keyword_prefs.NEGATORS or (w.startswith("ما") and w[2:] in keyword_prefs.NEG_BRIDGE) for w in t.split()):
            # «رواية ولا شعر؟» is a choice between two kinds, not a refusal: «ولا» alone refuses nothing
            pos, neg = pos + [g for g in neg if g not in pos], []
        m = _EXCEPT.search(t)
        if m:
            tail, tail_flags = keyword_prefs._parse_one(t[m.end():])
            for g in tail["genres"] + tail_flags["genres_negated"]:
                if g not in neg:
                    neg.append(g)
        if neg:
            for seg in re.split(r"(?:^| )ولا ", t)[1:]:
                seg_p, _ = keyword_prefs._parse_one(seg)
                for g in seg_p["genres"]:
                    if g not in neg:
                        neg.append(g)
        pos = [g for g in pos if g not in neg]
        out = [g for g in out if g not in pos]
        out += [g for g in neg if g not in out]
    return out


# ---------------------------------------------------------------------------
# The reader: which child the latest request is for, and «… لي» after the children
# ---------------------------------------------------------------------------
_F = {normalize(w) for w in "بنتي ابنتي لبنتي لابنتي بنتنا طفلتي لطفلتي طفلة لطفلة حفيدتي لحفيدتي أختها لأختها أخته لأخته "
      "أختي لأختي لها عمرها البنت للبنت للصغيرة للكبيرة daughter girl sister her".split()}
_M = {normalize(w) for w in "ولدي ابني لولدي لابني طفلي لطفلي طفل لطفل حفيدي لحفيدي أخوها لأخوها أخوه لأخوه أخوي لأخوي "
      "له عمره الولد للولد للصغير للكبير son boy brother him his".split()}
# «للصغيرة» / «وللكبير» after «ولدي ٩ وبنتي ٤»: the girl / the boy named before (QAD 2026-10-12, case 21.3)
_AGE = re.compile(r"(?:عمره|عمرها|عمري|عمر|سنه|سنوات|سنين|age|aged)\s*(\d{1,2})|(\d{1,2})\s*(?:سنه|سنوات|سنين|years?|yrs?|yo)(?:\s|$)")
_GRADE_N = {normalize(k): v for k, v in {"أول": 1, "اولى": 1, "أولى": 1, "ثاني": 2, "ثانية": 2, "ثالث": 3, "ثالثة": 3,
                                          "رابع": 4, "رابعة": 4, "خامس": 5, "خامسة": 5, "سادس": 6, "سادسة": 6}.items()}
_GRADE = re.compile(r"(?:ب|في |لل|ال)?(اول|اولي|ثاني|ثانيه|ثالث|ثالثه|رابع|رابعه|خامس|خامسه|سادس|سادسه) "
                    r"(?:ال)?(ابتدائي|متوسط|ثانوي)")
_STAGE_BASE = {"ابتدائي": 5, "متوسط": 11, "ثانوي": 14}
# QADFIX25 (QAD 2026-10-31, cases 43.1, 19.1): «ولد بالصف الثالث»، «بنتي بالصف الثاني» — the school year said with «الصف» and no
# stage is the primary school's (the age was never read: an academic study of the Prophet's life was offered to a boy of 8)
_GRADE_SAFF = re.compile(r"(?:^| )(?:بال|في ال|لل|ال|ب)?صف (?:ال)?(اول|اولي|ثاني|ثانيه|ثالث|ثالثه|رابع|رابعه|خامس|خامسه|سادس|سادسه)"
                         r"(?: (?:ال)?(ابتدائي|متوسط|ثانوي))?(?= |$)")


def grade_ages(text):
    """«بأولى ابتدائي» -> [6], «ثاني متوسط» -> [13], «روضة» -> [5]: the usual age of that school year."""
    t = " ".join(normalize(text).split())
    out = [_STAGE_BASE[m.group(2)] + _GRADE_N.get(m.group(1), 1) for m in _GRADE.finditer(t)]
    if not out:
        out = [_STAGE_BASE[m.group(2) or "ابتدائي"] + _GRADE_N.get(m.group(1), 1) for m in _GRADE_SAFF.finditer(t)]
    if not out and re.search(r"(?:^| )(?:بال|في ال|ال)?(?:روضه|تمهيدي)(?: |$)", t):
        out.append(5)
    if not out:
        # QADFIX26 (QAD 2026-11-01, case 3.1): «بنت عمي بالمتوسط» — the stage alone, no year: the middle of its years
        # («أول متوسط … ثالث متوسط» = 12–14 → 13; secondary 15–17 → 16; primary 6–11 → 9)
        m = _STAGE_ONLY.search(t)
        if m:
            out.append(_STAGE_MID[m.group(1) or m.group(2)])
    return out


_STAGE_ONLY = re.compile(r"(?:^| )(?:بال|في ال|ب ال|فال)(متوسط|ثانوي|ثانويه|ابتدائي|ابتدائيه)(?: |$)(?!(?:ال)?(?:طول|حجم|سعر|مستوي))"
                         r"|(?:^| )(?:بال|في ال|ال)?مرحله (?:ال)?(متوسطه|ثانويه|ابتدائيه)(?: |$)")
_STAGE_MID = {"متوسط": 13, "متوسطه": 13, "ثانوي": 16, "ثانويه": 16, "ابتدائي": 9, "ابتدائيه": 9}


# «بنتي ١٥»، «ولدي ٩ وبنتي ٤»: an age right after the child, with no «عمرها» / «سنة» (QAD 2026-10-12, case 38:
# «كم عمر بنتك؟» was asked again)
_KID_AGE = re.compile(r"(?:^| )[وف]?ل?(?:بنتي|ابنتي|ولدي|ابني|طفلي|طفلتي|حفيدي|حفيدتي|اخوي|اختي|بنت اختي|بنت اخوي|ولد اختي"
                      # QADFIX31 (replay of QAD 2026-11-08 case 44, fresh "my daughter is 9 and loves animals" → "something with pictures
                      # maybe" gave toddlers' word books): "my daughter is 9" is her age too
                      r"|ولد اخوي|my daughter|my son|my kid|my child)(?: (?:is|who is|turned|turns|just turned))?\s*[،,:]?\s*(\d{1,2})(?!\d)"
                      r"(?! ?(?:كتاب|كتب|صفحه|ريال|روايه|روايات|قصه|قصص|جزء|book|books|pages?))")


# «عمره عشر سنين»، «بنتي ست سنوات»، «ten years old»: an age written as a word (QAD 2026-10-13, case 14: «عشر سنين»
# was not read and the age was asked twice). Only next to an age word, so «عشر كتب» stays a count.
_NUM_WORDS = {
    "سنتين": 2, "ثلاث": 3, "ثلاثه": 3, "ثلث": 3, "اربع": 4, "اربعه": 4, "خمس": 5, "خمسه": 5, "ست": 6, "سته": 6,
    "سبع": 7, "سبعه": 7, "ثمان": 8, "ثماني": 8, "ثمانيه": 8, "ثمن": 8, "تسع": 9, "تسعه": 9, "عشر": 10, "عشره": 10,
    "احدعش": 11, "احدعشر": 11, "احد عشر": 11, "حداش": 11, "حدعش": 11, "اثنعش": 12, "اثنا عشر": 12, "اثني عشر": 12,
    "اثنعشر": 12, "اطنعش": 12, "ثلاث عشره": 13, "ثلاث عشر": 13, "ثلطعش": 13, "ثلاثطعش": 13, "ثلتعش": 13,
    "اربع عشره": 14, "اربع عشر": 14, "اربعطعش": 14, "ارباطعش": 14, "اربعتعش": 14, "خمس عشره": 15, "خمس عشر": 15,
    "خمسطعش": 15, "خمستعش": 15, "ست عشره": 16, "ست عشر": 16, "ستطعش": 16, "سطعش": 16, "سبع عشره": 17,
    "سبع عشر": 17, "سبعطعش": 17, "سبعتعش": 17, "ثمان عشره": 18, "ثماني عشره": 18, "ثمنطعش": 18, "ثمانطعش": 18,
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
}
_NUM_ALT = "|".join(sorted((re.escape(k) for k in _NUM_WORDS), key=len, reverse=True))
_NUM_AFTER = re.compile(r"((?:^| )(?:عمره|عمرها|عمري|عمر|عمرهم|عمرهما|age|aged)\s+)(" + _NUM_ALT + r")(?= |$)")
_NUM_BEFORE = re.compile(r"((?:^| )[وب]?)(" + _NUM_ALT + r")(?= (?:سنه|سنين|سنوات|اعوام|عام|years?)(?: |$))")
_KID_NUM = re.compile(r"((?:^| )[وف]?ل?(?:بنتي|ابنتي|ولدي|ابني|طفلي|طفلتي|حفيدي|حفيدتي|my daughter|my son)(?: is| who is)?\s+)(" + _NUM_ALT   # QADFIX31: "is nine"
                      + r")(?= |$)")


# QADFIX34 #1 (QAD 2026-11-11 case 17, grave): "I'm 15 and I loved The Hunger Games" / "less adult please, I'm only 15" — the visitor's
# own age said in English («i m 15» after normalize) was not read, so the young readers' path never ran (Fire & Blood, then «For an
# adult reader»): "I'm 15", "I am only 15", "im 15" are an age, like «عمري ١٥»; not "I'm 15 minutes away" / "I'm 15 pages in"
_SELF_AGE_EN = re.compile(r"(?:^| )(?:i m|im|i am)(?: only| just| almost| nearly| turning)? (\d{1,2}|" + "|".join(
    ("two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
     "sixteen", "seventeen")) + r")(?= |$)(?! (?:minutes?|mins?|hours?|pages?|books?|km|miles?|chapters?|percent|dollars?|riyals?))")


def self_age_en(text):
    """QADFIX34 #1: the visitor's own age said in English ("I'm 15") -> 15, else None."""
    m = _SELF_AGE_EN.search(" ".join(normalize(text).split()))
    if not m:
        return None
    v = m.group(1)
    return int(v) if v.isdigit() else _NUM_WORDS.get(v)


def _num_words(t):
    """Digits for an age written in words; «سنتين» alone is two years."""
    # QADFIX34 #1: "i m 15" is "age 15"
    t = _SELF_AGE_EN.sub(lambda m: " age " + m.group(1), t)
    t = re.sub(r"(?:^| )(?:عمره|عمرها|عمري)?\s*سنتين(?= |$)", lambda m: m.group(0).replace("سنتين", "2 سنين"), t)
    for rx in (_NUM_AFTER, _NUM_BEFORE, _KID_NUM):
        t = rx.sub(lambda m: m.group(1) + str(_NUM_WORDS[m.group(2)]), t)
    return t


def kid_ages(text):
    t = _num_words(" ".join(normalize(text).split()))
    return [int(m.group(1)) for m in _KID_AGE.finditer(t)]


def ages_in(text):
    t = _num_words(" ".join(normalize(text).split()))
    found = {(m.start(1) if m.group(1) else m.start(2)): int(m.group(1) or m.group(2)) for m in _AGE.finditer(t)}
    for m in _KID_AGE.finditer(t):
        found.setdefault(m.start(1), int(m.group(1)))
    return [found[k] for k in sorted(found)] + grade_ages(text)


# «لي أنا كأم»، «كمعلمة»، «للأهل»: the reader is the grown-up asking, even when the subject is teenagers or
# children («كتب عن الصحة النفسية للمراهقين، لي أنا كأم» got teen novels: QAD 2026-10-12, case 38)
_PARENT = re.compile(r"(?:^| )(?:انا |لي )?(?:كام|كاب|كوالد|كوالده|كمعلم|كمعلمه|كمربي|كمربيه|كاخصائي|كاخصائيه|كولي امر|كولية امر)(?: |$)"
                     r"|(?:^| )(?:للوالدين|للاهل|لاولياء الامور|لولي الامر|للامهات|للاباء|للمربين|للمعلمين)(?: |$)"
                     # «أنا معلمة وأبي كتاب عن إدارة الصف» (QAD 2026-10-13, case 13)
                     r"|(?:^| )انا (?:معلم|معلمه|مدرس|مدرسه|ام|اب|ابو|والد|والده|مربي|مربيه|اخصائي|اخصائيه)(?: |$)"
                     r"|as a (?:parent|mom|mother|dad|father|teacher)|for parents|for teachers"
                     # QADFIX5 #9 (QAD 2026-10-17, case 36.2): «دليل عملي للتعامل مع المراهقين» — the parent reads it
                     r"|(?:^| )(?:ل|لل)?(?:تعامل|التعامل|تربيه|تربية|فهم) (?:مع )?(?:ال)?(?:مراهقين|مراهق|مراهقه|ابناء|الابناء|اطفال|الاطفال|اولاد|العيال|طفل|طفله)(?: |$)"
                     r"|(?:deal|dealing|cope|coping) with (?:teens|teenagers|kids|children)|raising (?:teens|teenagers|kids|children)|parenting"
                     # «ولدي دخل سن المراهقة وصار عصبي، ابي كتاب يساعدني انا افهمه»، «بس الكتاب لي أنا مو له»
                     # (QAD 2026-10-14, case 8: the father got a teen's age question and a criminology book)
                     r"|(?:يساعدني|يفيدني|يعلمني) (?:انا )?(?:ك?يف )?(?:افهم|اتعامل|اتفاهم|اربي|اساعد|اتكلم|احتوي)"
                     r"|(?:^| )(?:افهم|اتعامل مع|اتفاهم مع|اربي|احتوي|اساعد) (?:ولدي|ابني|بنتي|ابنتي|طفلي|طفلتي|اولادي|عيالي|اطفالي|بناتي|ولدنا|بنتنا)(?: |$)"
                     r"|(?:^| )(?:الكتاب|الكتب) (?:لي|لنا) (?:انا|احنا)?(?: |$)|(?:^| )(?:لي|لنا) (?:انا |احنا )?(?:مو|مب|مش|وليس) (?:له|لها|لهم)(?: |$)"
                     r"|help me (?:understand|deal with|handle|raise) (?:my|our) (?:son|daughter|kid|child|teen)"
                     # QADFIX7 #4 (QAD 2026-10-19, case 47.1): «ابي كتاب لي يساعدني اتقرب منه»
                     r"|(?:يساعدني|يفيدني|يعلمني|يخليني) (?:انا )?(?:ك?يف )?(?:اتقرب|اقرب|اقترب|اصاحب|اكسب|اتواصل|اصادق|افتح|اكون قريب)"
                     r"|(?:^| )(?:اتقرب|اقرب|اقترب|اصاحب|اكسب) (?:من |مع )?(?:ولدي|ابني|بنتي|ابنتي|عيالي|اولادي|بناتي|منه|منها|منهم)(?: |$)"
                     r"|help me (?:connect|bond|get closer) with"
                     # QADFIX9 (QAD 2026-10-20, case 4.1): «أبي كتاب أقرأه أنا يفهمني نفسيتها»
                     r"|(?:^| )(?:اقراه|اقراها|اقرأه) (?:انا|بنفسي)(?: |$)|(?:^| )(?:يفهمني|يخليني افهم|يعرفني) (?:نفسيتها|نفسيته|نفسيه|بنتي|ولدي|ابني|طفلي)"
                     r"|(?:^| )(?:افهم|افهمها|افهمه) (?:نفسيتها|نفسيته|نفسيه|طبعها|طبعه)(?: |$)")
# (QADFIX35, case 7.1: «عيالنا / اولادنا / ابننا / بناتنا / our son» too)
_KID_WORD = re.compile(r"(?:^| )(?:ولدي|ابني|بنتي|ابنتي|طفلي|طفلتي|اولادي|عيالي|اطفالي|بناتي|ولدنا|بنتنا|عيالنا|اولادنا|ابننا|بناتنا|my son|my daughter|my kid|our son|our daughter|our kid)(?: |$)")
_FOR_ME = re.compile(r"(?:^| )(?:كتاب|كتب|شي|شيء|روايه) (?:لي|لنفسي)(?: انا)?(?: |$)|(?:^| )(?:a book|something) for (?:me|myself)"
                     # QADFIX35 (QAD 2026-11-12 case 7.1, medium): «ولدنا اللي بالمتوسط صاير يرد علينا بقلة أدب، نبي كتاب نقراه احنا الاثنين»
                     # → «ما لقيت كتابًا واضحًا» while «200 خطوة نحو التربية برفق» is in stock: the two parents read it themselves
                     # (not «نقراه مع ولدنا / سوا»: a family reading together)
                     r"|(?:^| )(?:نقراه|نقراها|نقرا) (?:احنا|انا وزوجتي|انا وامه|انا وابوه|انا وامها|انا وابوها|انا وام العيال)(?: |$)"
                     r"|(?:^| )(?:لنا|لي) (?:احنا|انا) (?:الاثنين|الثنين|الاتنين)(?: |$)|(?:^| )احنا الاثنين(?: |$)"
                     r"|(?:^| )(?:a book|something) for both of us|(?:^| )(?:my wife and i|my husband and i|both of us) (?:can |could |want to |would )?read")


# QADFIX17 (QAD 2026-10-24, case 45.2): «تقدرين تسألين موظف المكتبة» to a visitor who never said she is a woman — the
# reply speaks to the visitor in the masculine general form unless the model read her as a woman (rule 4 of the reply)
_FEM_TO_MASC = {"تقدرين": "تقدر", "تبغين": "تبغى", "تبين": "تبي", "تحبين": "تحب", "تفضلين": "تفضل", "تفضّلين": "تفضّل",
                "تريدين": "تريد", "تعرفين": "تعرف", "تسألين": "تسأل", "تحتاجين": "تحتاج", "تشوفين": "تشوف", "تلقين": "تلقى",
                "تختارين": "تختار", "تميلين": "تميل", "تودين": "تود", "تقرين": "تقرا", "تقرئين": "تقرأ", "تقرأين": "تقرأ",
                "تستمتعين": "تستمتع", "تجربين": "تجرب", "ترتاحين": "ترتاح", "تدورين": "تدور", "تبحثين": "تبحث",
                "تشترين": "تشتري", "تهدين": "تهدي", "تهديها": "تهديها", "تقدري": "تقدر", "تبغي": "تبغى", "تحبي": "تحب",
                "تفضلي": "تفضل", "أبشري": "أبشر", "ابشري": "ابشر", "جربي": "جرب", "جرّبي": "جرّب", "اسألي": "اسأل",
                "خذي": "خذ", "شوفي": "شوف", "لكِ": "لك", "عندكِ": "عندك", "إليكِ": "إليك",
                # QADFIX20 (QAD 2026-10-25, case 22.3): «معك حق، كنتِ تقصدين وصفات أكل … اللي تبينها» to a father
                "تقصدين": "تقصد", "تقصدي": "تقصد", "كنتِ": "كنت", "قصدكِ": "قصدك", "تطلبين": "تطلب", "تبحثي": "تبحث"}
# QADFIX20: a pronoun joined to the verb («تبينها»، «تحبينه») keeps it: «تبيها»، «تحبه»
_FEM_RX = re.compile(r"(?<![\wً-ْ])(و|ف)?(" + "|".join(sorted((re.escape(k) for k in _FEM_TO_MASC), key=len, reverse=True))
                     + r")(ها|ه|هم|هن|ني)?(?![\wً-ْ])")


def masculine_address(text):
    """The visitor addressed in the masculine general form («تقدر»، «تفضّل»)."""
    return _FEM_RX.sub(lambda m: (m.group(1) or "") + _FEM_TO_MASC[m.group(2)] + (m.group(3) or ""), text or "")


def parent_said(text):
    t = " ".join(normalize(text).split())
    # QADFIX7 #4: «ولدي … ابي كتاب لي» — a book for the parent himself, the child is what it is about
    return bool(_PARENT.search(t)) or bool(_KID_WORD.search(t) and _FOR_ME.search(t))


_KIDDING = re.compile(r"(?:^| )(?:عمره|عمرها|عمر|سنه|سنين|سنوات|سنة|ولدي|بنتي|ابني)(?: |$)|\d")


_model_reader = threading.local()


def set_model_parent(flag, not_parent=False):
    """QADFIX12 #3 (QAD 2026-10-22, case 13.1): the interpreter read this turn as a grown-up asking for a book for
    himself about a child («كتاب لي أنا عن تربية طفل عمره سنتين»), in whatever words. Set per request thread at the
    start of each turn, so parent_reader() below honours the model and not only the phrasings in its lists."""
    _model_reader.parent = bool(flag)
    # QADFIX16 (QAD 2026-10-24, cases 22.3, 41.2): the model read the visitor as the reader of a book about something
    # other than raising a child («وأنا بعد أبي شي عن كرة القدم لي»): no word list makes him a parent reader then
    _model_reader.not_parent = bool(not_parent) and not bool(flag)


def parent_reader(user_messages):
    """The grown-up is the reader: said in the latest message, or said just before and the latest only
    answers about the child («عمره ١٥») without asking for something for him (QAD 2026-10-14, case 8)."""
    if not user_messages:
        return False
    if getattr(_model_reader, "parent", False):
        return True
    if getattr(_model_reader, "not_parent", False):
        return False
    if parent_said(user_messages[-1]):
        return True
    last = " ".join(normalize(user_messages[-1]).split())
    earlier = any(parent_said(t) for t in user_messages[-3:-1])
    if not earlier or self_said(user_messages[-1]):
        return False
    # «عمره ١٥»، «١٥ سنة»: an answer about the child; «ابي له رواية» is a new wish for the child
    return len(last.split()) <= 6 and bool(_KIDDING.search(last)) and not re.search(r"(?:^| )(?:له|لها|لهم)(?: |$)", last)


# «وش التصنيفات اللي عندكم؟»، «كم كتاب عندكم؟»، «وش أكثر نوع عندكم؟»: a question about the library itself
_OVERVIEW = re.compile(r"(?:^| )(?:ال)?(?:تصنيفات|اقسام|فئات)(?: |$)|(?:^| )(?:انواع|اصناف) (?:ال)?كتب"
                       r"|كم (?:كتاب|عنوان|نسخه|عدد (?:ال)?كتب|روايه|روايات|قصه|قصص|ديوان|دواوين)(?P<rest>(?: \S+)*)"
                       r"|عدد (?:ال)?(?:كتب|عناوين|روايات)(?P<rest2>(?: \S+)*)"
                       r"|(?:اكثر|اقل) (?:نوع|تصنيف|قسم|صنف)|(?:نوع|تصنيف|قسم) (?:عندكم )?(?:اكثر|الاكثر|اقل|الاقل)"
                       r"|what (?:categories|genres|sections)|which (?:categories|genres)|how many books|most common (?:genre|category)")


_OVERVIEW_TAIL = {normalize(w) for w in "عندكم عندك عندكم؟ بالمكتبه المكتبه في موجود موجوده متوفر متوفره كلها تقريبا "
                  "اجمالا بالمحل المحل الحين حاليا الكتب المتوفره you have do in the store library total "
                  # «كم كتاب عندكم تقريبًا؟ ووش أقل قسم» (QAD 2026-10-13, case 5)
                  "و وش ووش ايش وايش اقل اكثر قسم القسم تصنيف التصنيف نوع الاقسام والاقسام التصنيفات عددها كم وكم "
                  "فيه فيها كتب كتاب عناوين طيب يعني "
                  # «كم كتاب عربي عندكم؟ وكم منها روايات؟»، «قصدي عدد الكتب العربية اللي بالمكتبة» (QAD 2026-10-15, case 50)
                  "منها منهم منهن اللي الي قصدي اقصد بس عدد العدد اجمالي الاجمالي".split()}


_OVERVIEW_LANG = {normalize(w) for w in "بالانجليزي بالإنجليزي بالانجليزيه بالإنجليزية انجليزي انجليزيه إنجليزي إنجليزية الانجليزي "
                  "الانجليزيه بالعربي بالعربيه عربي عربيه العربي العربيه مترجم مترجمه مترجمة english arabic in".split()}


def overview_asked(text):
    """Only the library as a whole: «كم كتاب عندكم لنجيب محفوظ؟» is a question about an author."""
    t = " ".join(normalize(re.sub(r"[؟?!.،,]", " ", text)).split())
    m = _OVERVIEW.search(t)
    if not m:
        return False
    rest = [w for w in (m.group("rest") or m.group("rest2") or "").split() if w not in _OVERVIEW_TAIL]
    if not rest:
        return True
    # «وكم كتاب بالانجليزي؟»: the copies in a language are still the shelves (QAD 2026-10-14, case 38)
    if len(rest) <= 2 and all(w in _OVERVIEW_LANG for w in rest):
        return True
    # «كم كتاب تاريخ عندكم؟»: a kind of book is still the shelves; a name or a subject is not;
    # «كم كتاب عربي … روايات»: a language and a kind together (QAD 2026-10-15, case 50)
    kind = [w for w in rest if w not in _OVERVIEW_LANG]
    if not kind:
        return len(rest) <= 3
    p, _ = keyword_prefs._parse_one(" ".join(kind))
    return len(rest) <= 4 and len(kind) <= 2 and bool(p["genres"]) and not p.get("authors")


def reader_person(user_messages):
    """Whose book is being looked for now -> (gender "f" / "m" / None, that person's age or None).
    «بنتي عمرها ١١» … «ولأخوها عمره ١٦» … «نرجع لبنتي»: the daughter again, with HER age (the round of
    2026-10-11 showed «طفلة عمرها ١٦»: the brother's age on the sister)."""
    ages, current = {}, None
    for text in user_messages:
        words = normalize(text).split()
        marks = []
        for w in words:
            forms = {w, w[1:] if w[:1] in "وف" and len(w) > 3 else w}
            g = "f" if forms & _F else "m" if forms & _M else None
            if g and (not marks or marks[-1] != g):
                marks.append(g)
        said = ages_in(text)
        if marks:
            current = marks[-1]
            if len(said) >= len(marks):
                for g, a in zip(marks, said):
                    ages[g] = a
            elif said:
                ages[marks[-1]] = said[-1]
        elif said:
            ages[current] = said[-1]
    if current in ages:
        return current, ages[current]
    if len(ages) == 1:
        return current, next(iter(ages.values()))
    return current, None


_TELL = {normalize(w) for w in "رشحي رشح رشّحي رشّح اقترحي اقترح قولي قل قول اختاري اختر اختار عطني عطيني اعطني اعطيني هاتي "
         "هات جيبي جيب دوري دور دوّري دوّر ابحثي ابحث شوفي شوف وريني ورني ورّيني ابي أبي ابغى أبغى ابغا أريد اريد ودي "
         "بغيت تكفين تكفى احجزي احجز اكتبي اكتب اشرحي اشرح وضحي find show tell give recommend suggest".split()}
_SELF_START = re.compile(r"^(?:طيب |زين |اوكي |ok |okay )?و?انا (?:بعد |كمان |برضو |ايضا |كذلك )?"
                         r"(?:ابي|ابغي|ابغا|اريد|ودي|بغيت|ابا|ابيها|ابيه)(?: |$)")
_MINOR_SELF = re.compile(r"(?:^| )انا (?:في |ب|بال)?(?:اول|اولي|ثاني|ثالث|رابع|خامس|سادس)? ?(?:ال)?(?:ابتدائي|متوسط|ثانوي)")


def self_said(text):
    """«وأنا ابي رواية رومانسية لي»، «ابي شي لنفسي»: the book is for the visitor himself.
    «رشّحي لي»، «أبغى لي قصة لولدي» are not: there «لي» only means «tell me»."""
    words = normalize(text).split()
    if len(words) < 2:
        return False
    t = " ".join(words)
    if re.search(r"(?:^| )(?:لنفسي|لي انا|for myself)(?: |$)", t) or _SELF_START.search(t):
        return True
    if kin_self_said35(text):   # QADFIX35 #2 (case 12.2): «طيب وأنا جده، ابي رواية لي عن الريف والفلاحين»
        return True
    return words[-1] == "لي" and len(words) >= 3 and words[-2] not in _TELL


def minor_self(user_messages):
    """The visitor is himself the young reader («عمري ١٥»، «أنا بثاني متوسط»)."""
    for text in user_messages:
        t = " ".join(normalize(text).split())
        m = re.search(r"عمري (\d{1,2})", t)
        if (m and int(m.group(1)) < 18) or _MINOR_SELF.search(t):
            return True
        _en34 = self_age_en(text)   # QADFIX34 #1: "I'm 15"
        if _en34 is not None and _en34 < 18:
            return True
    return False


# ---------------------------------------------------------------------------
# «غيرهم»: other authors than the ones just shown
# ---------------------------------------------------------------------------
_OTHERS = re.compile(r"(?:^| )(?:غيرهم|غيرهن|غير هذول|غير هاذول|غير هؤلاء|كاتب ثاني|كاتبه ثانيه|مؤلف ثاني|كتاب ثانين|مؤلفين ثانين"
                     r"|other authors|other writers|someone else|another author)(?: |$)")


def other_authors_asked(text):
    return bool(_OTHERS.search(" ".join(normalize(text).split())))


# ---------------------------------------------------------------------------
# «أبيها بالعربي»، «عربية»، «بس لكاتب عربي»: a follow-up that only adds the language or the origin;
# the prize, the subject and the years of the request before it still hold (QAD 2026-10-12, cases 13.2,
# 19.2, 22.2: «أبيها بالعربي» after a prize gave other prizes; «عربية» after «قبل ١٩٥٠» lost the year)
# ---------------------------------------------------------------------------
_REFINE = {normalize(w) for w in (
    "ابي ابيها ابيه ابيهم ابغى ابغاها ابغاه ابغا ابغاهم اريدها اريده ودي بس طيب زين اوكي لو سمحت ممكن تكون يكون "
    "تكون يكونون و او ولا فقط بالذات تحديدا يعني نسخه النسخه نسخة طبعه الطبعه حق "
    "بالعربي بالعربيه عربي عربيه العربيه العربي عربيات بالفصحى مترجم مترجمه مترجمة المترجمه مترجمات اصلي اصليه "
    "بالانجليزي بالانقليزي انجليزي انجليزيه الانجليزيه انقليزي بالانجليزيه لكاتب لكاتبه لمؤلف لمؤلفه كاتب كاتبه "
    "مؤلف مؤلفه سعودي سعوديه خليجي خليجيه مصري مصريه عراقي عراقيه سوري سوريه لبناني لبنانيه مغربي مغربيه "
    "اجنبي اجنبيه غربي غربيه مو مب غير "
    "in arabic english translated original version edition please only just by an a author writer "
    # «شي عربي» after «دراما عائلية» only adds the language (QADFIX6, QAD 2026-10-18 case 2.2)
    "شي شيء something").split()}


def refine_only(text):
    """The message only adds a language / translation / author origin («أبيها بالعربي»، «عربية»)."""
    words = normalize(re.sub(r"[؟?!.،,]", " ", text)).split()
    return bool(words) and len(words) <= 6 and all(w in _REFINE for w in words)


# «فيه رواية اسمها الساعة الخامسة شي كذا»: the start of a title after «اسمها», with the visitor's «شي كذا» /
# «تقريبًا» cut off (QAD 2026-10-13, case 30: the model asked about mood and «الساعة الخامسة والعشرون» was in stock)
_NAMED = re.compile(r"(?:^| )(?:اسمها|اسمه|عنوانها|عنوانه|بعنوان|تسمي|تسمى|يسمي|يسمى|اسم الكتاب|اسم الروايه|called|titled|named)\s+(.+)$")
_NAMED_TAIL = re.compile(r"\s+(?:شي كذا|شيء كذا|او شي.*|او كذا|او شي زي كذا|او شي قريب.*|تقريبا|على ما اذكر|ما اذكر.*|ما اتذكر.*"
                         r"|مدري.*|اظن|احس|او something.*|or something.*|something like that|i think|maybe|كذا|زي كذا"
                         # «اسمها «حدائق» شي، نسيت باقي العنوان» (QADFIX6, QAD 2026-10-18 case 19.1)
                         r"|شي(?: \S+){0,2} نسيت.*|نسيت.*|مو متاكد.*|مب متاكد.*|forgot.*|شي)\s*$")


def named_title(text):
    t = " ".join(normalize(text).split())
    m = _NAMED.search(t)
    if not m or not re.search(r"(?:^| )[وبل]?(?:ال)?(?:روايه|كتاب|قصه|ديوان|مجموعه|سلسله|book|novel|story)(?: |$)", t[:m.start() + 1]):
        return None   # «ولدي اسمه أحمد» is not a title
    frag = m.group(1)
    for _ in range(3):
        frag = _NAMED_TAIL.sub("", frag).strip()
    return frag if len(frag) >= 3 and len(frag.split()) <= 8 else None


# «مرتفعات ويذرنغ» = «مرتفعات ويذرنج» = «مرتفعات وذرينغ»: a foreign name spelled in Arabic letters in several ways
# (QAD 2026-10-13, case 29: only the English edition was found). Long vowels dropped, the letters used for
# g / th / s / t folded together, «ال» taken off each word.
_SKEL = str.maketrans({"غ": "ج", "ق": "ج", "ك": "ج", "ذ": "ز", "ث": "ز", "ظ": "ز", "ص": "س", "ط": "ت", "ض": "د",
                       "ا": None, "و": None, "ي": None, "ى": None, "ه": None, "ة": None, "ع": None, "ء": None})


def title_skeleton(text):
    words = [strip_al(w) for w in title_key(text).split()]
    return " ".join(w.translate(_SKEL) for w in words if w)


# ---------------------------------------------------------------------------
# Pages as a number (QADFIX4, QAD 2026-10-16 case 7.1): «كتب أقل من ١٠٠ صفحة» is a limit on page_count,
# not the general «short» (one book was offered when 254 in stock have fewer than 100 pages)
# ---------------------------------------------------------------------------
# (?<!\S): a whole word, so «على الأقل ١٠٠» is not read as «أقل ١٠٠» (AUDFIX, audit 2026-10-16 P3)
_PAGES_UNDER = re.compile(r"(?<!\S)(?:اقل من|اقل|تحت|ما يتعدي|ما تتعدي|ما يتجاوز|ما تتجاوز|لا يتعدي|لا تتعدي|لا يتجاوز|لا تتجاوز"
                          r"|ما يزيد عن|ما تزيد عن|لا يزيد عن|لا تزيد عن|حدود|بحدود|less than|under|fewer than|below|max|maximum)"
                          r"\s+(\d{2,4})\s*(?:صفحه|صفحات|صفحة|ص|pages?)(?:\s|$)")
_PAGES_OVER = re.compile(r"(?:اكثر من|فوق|اكبر من|اطول من|more than|over|longer than|at least|عل[يى] الاقل)\s+(\d{2,4})\s*(?:صفحه|صفحات|صفحة|ص|pages?)(?:\s|$)")
_PAGES_BARE = re.compile(r"(?:^| )(\d{2,4})\s*(?:صفحه|صفحات|صفحة|pages)(?:\s|$)")
# QADFIX29 #2 (QAD 2026-11-06 case 4.1, grave): «يعني بين ٨٠ و١٥٠ صفحة» went to unsupported_requests and «صفحه» became a subject —
# a range of pages is a lower and an upper limit
_PAGES_BETWEEN = re.compile(r"(?:^| )(?:بين|من|between|from)\s+(\d{2,4})\s*(?:صفحه |صفحات |pages )?(?:و|الي|ل|-|–|to|and)\s*(\d{2,4})\s*"
                            r"(?:صفحه|صفحات|صفحة|ص|pages?)(?:\s|$)|(?:^| )(\d{2,4})\s*[-–]\s*(\d{2,4})\s*(?:صفحه|صفحات|صفحة|ص|pages?)(?:\s|$)")
_PAGES_ANY = re.compile(r"(?:اي|مو مهم|ما يهم) (?:عدد )?(?:ال)?صفحات|الطول (?:مو مهم|ما يهم)|any length")
# QADFIX30 #2 (QAD 2026-11-07 case 2.1, grave): «كتاب تاريخ ما يقل عن ١٠٠ صفحة ولا يزيد عن ٢٠٠» → «ما ظهر لي كتاب أقدر أتأكد إنه بين
# ١٠٠ و٢٠٠ صفحة» while 50 fit: «ما / لا يقل عن N» is the fewest pages, «ولا يزيد عن M» the most, and once the message speaks of pages a
# bound may leave out the word «صفحة» («… ولا يزيد عن ٢٠٠»); a price or a year is never one
_NOT_UNIT30 = r"(?!\s*(?:ريال|ريالات|ر س|sar|sr|riyals?|سنه|سنة|سنين|سنوات|عام|ساعه|ساعات|دقيقه|دقايق|years?|hours?|minutes?))"
_UNIT30 = r"\s*(?:صفحه|صفحات|صفحة|ص|pages?)?" + _NOT_UNIT30 + r"(?:\s|$)"
_LO30 = (r"(?<!\S)و?(?:ما|لا|مو|مب) (?:يقل|تقل|ينقص|تنقص|يكون اقل|تكون اقل)(?: (?:عدد )?(?:صفحاته|صفحاتها|صفحاتهم))? (?:عن|من)"
         r"|(?<!\S)(?:not|no) (?:less|fewer) than|(?<!\S)(?:اقله|اقلها|كحد ادني|حد ادني)")
_HI30 = (r"(?<!\S)و?(?:ما|لا|مو|مب) (?:يزيد|تزيد|يتعدي|تتعدي|يتجاوز|تتجاوز|يكون اكثر|تكون اكثر|يطلع اكثر|تطلع اكثر)(?: (?:عدد )?(?:صفحاته|صفحاتها|صفحاتهم))? (?:عن|من)?"
         r"|(?<!\S)(?:no|not) more than|(?<!\S)at most|(?<!\S)up to|(?<!\S)(?:اكثره|اكثرها|كحد اقصي|حد اقصي)")
_PAGES_LO30 = re.compile(r"(?:" + _LO30 + r")\s*(\d{2,4})" + _UNIT30)
_PAGES_HI30 = re.compile(r"(?:" + _HI30 + r")\s*(\d{2,4})" + _UNIT30)
_PAGES_WORD30 = re.compile(r"صفح|(?:^| )ص(?: |$)|pages?(?: |$)")
_PAGES_FROM30 = re.compile(r"(?:^| )(?:من|بين|between|from)\s+(\d{2,4})\s*(?:صفحه |صفحات |pages )?(?:الي|لين|لغايه|لحد|حتي|ل|و|to|and|till|until)\s*"
                           r"(\d{2,4})" + _UNIT30)


def page_limit(user_messages):
    """-> (most pages, fewest pages) the visitor asked for as a number, each None when not said. The limit
    stays through the conversation until another is named or it is lifted («مو مهم عدد الصفحات»)."""
    lo = hi = None
    for text in user_messages:
        t = " ".join(num_canon(_pages_al(text)).split())
        if _PAGES_ANY.search(t):
            lo = hi = None
        m_bt = _PAGES_BETWEEN.search(t)
        if m_bt:
            a, b = sorted(int(x) for x in m_bt.groups() if x)
            if a < b:
                lo, hi = a, b
                continue
        if _PAGES_WORD30.search(t):
            # QADFIX30 #2 (QAD 2026-11-07 case 2.1): «ما يقل عن ١٠٠ صفحة ولا يزيد عن ٢٠٠»، «من ١٠٠ لين ٢٠٠ صفحة»
            m_fr = _PAGES_FROM30.search(t)
            if m_fr:
                a, b = sorted(int(x) for x in m_fr.groups())
                if a < b and not (a >= 1000 and b <= 2100):   # «من 1950 إلى 1960» is years
                    lo, hi = a, b
                    continue
            m_lo30, m_hi30 = _PAGES_LO30.search(t), _PAGES_HI30.search(t)
            if m_lo30:
                lo = int(m_lo30.group(1))
            if m_hi30:
                hi = int(m_hi30.group(1))
            for m30 in sorted((m for m in (m_lo30, m_hi30) if m), key=lambda m: -m.start()):
                t = t[:m30.start()] + " " + t[m30.end():]   # «لا يكون أقل من ١٠٠» is not «أقل من ١٠٠» below
        m_hi, m_lo = _PAGES_UNDER.search(t), _PAGES_OVER.search(t)
        if m_hi:
            hi = int(m_hi.group(1))
            if re.match(r"(?:اقل|تحت|less|under|fewer|below)", m_hi.group(0)):
                hi -= 1   # «أقل من ١٠٠ صفحة»: 99 at most
        if m_lo:
            lo = int(m_lo.group(1))
            if re.match(r"(?:اكثر|فوق|اكبر|اطول|more|over|longer)", m_lo.group(0)):
                lo += 1   # «أكثر من ١٠٠ صفحة»: 101 at least; «على الأقل ١٠٠» keeps 100 (AUDFIX, audit 2026-10-16 P3)
        if not m_hi and not m_lo and _PAGES_BARE.search(t) and re.search(r"(?:^| )(?:حول|تقريبا|بحدود|around|about)(?: |$)", t):
            n = int(_PAGES_BARE.search(t).group(1))
            lo, hi = int(n * 0.8), int(n * 1.2)
    return hi, lo


def _pages_al(text):
    """QADFIX27 (QAD 2026-11-05 cases 15.1–15.2, grave): «فوق السبعمية صفحة»، «فوق الألف صفحة» — a number word with «ال» right
    before «صفحة» is that number (num_canon itself keeps «ال» words as they are, titles rely on it)."""
    toks = normalize(text or "").split()
    for k, w in enumerate(toks):
        if (w.startswith("ال") and w[2:] in _NUM and len(w) > 4 and k + 1 < len(toks)
                and re.match(r"(?:صفحه|صفحات|ص|pages?)$", toks[k + 1])):
            toks[k] = w[2:]
    return " ".join(toks)


# QADFIX27 (QAD 2026-11-05 case 15.3, grave): «وش أطول رواية عندكم؟» — the longest, ordered from page_count like «الأقصر»;
# «أطول منها / أطول شوي» is longer than the books shown
_LONGEST = re.compile(r"(?:^| )[وفب]?(?:ال)?اطول(?: |$)|(?:^| )longest(?: |$)"   # QADFIX31 #1: «وأطول»
                      # QADFIX32 #2 (QAD 2026-11-09 case 1.1, grave): «كم صفحة أضخم كتاب تاريخ عندكم؟» → «تاريخ البشرية» 640 pages
                      # «من الخيارات المتاحة لي» while «الأنوار والمراقب» has 2,120: «أضخم / أسمك / أكبر كتاب / أكثر صفحات» is the longest too
                      # («أكبر» and «أسمك» alone are not: «أكبر كاتب»، «وش اسمك؟» — only with a book word, the size or the pages)
                      r"|(?:^| )[وفب]?(?:ال)?(?:اضخم|اضخمها|اضخمهم)(?: |$)|(?:^| )[وفب]?ال(?:اسمك|اكبر حجما|اكبر حجم)(?: |$)"
                      r"|(?:^| )[وفب]?(?:ال)?اسمك (?:كتاب|روايه|مجلد|ديوان|عمل|موسوعه|واحد|وحده|شي)(?: |$)"
                      r"|(?:^| )[وفب]?(?:ال)?اكبر (?:كتاب|مجلد|موسوعه)(?: |$)(?!.*(?:مبيع|مبيعا|شهره|قيمه|سعر|اثر|تاثير))"   # «أكبر رواية عربية» may be the greatest
                      r"|(?:^| )[وفب]?(?:ال)?(?:اكبر|اكبرها|اكبرهم|اسمكها) (?:\S+ )?(?:حجما|حجم|من حيث الحجم|من ناحيه الحجم)(?: |$)"
                      r"|(?:^| )[وفب]?(?:ال)?(?:اكبر|اكثر|اكثرها|اكثرهم|اكبرها) (?:\S+ )?(?:\S+ )?(?:عدد (?:ال)?صفحات|صفحات|بالصفحات"
                      r"|من (?:حيث|ناحيه) (?:عدد )?(?:ال)?صفحات)(?: |$)"
                      r"|(?:^| )(?:thickest|biggest (?:book|novel|one|volume)|largest (?:book|novel|one|volume)|most pages)(?: |$)")
_LONGER = re.compile(r"(?:^| )اطول (?:شوي|شويه|منها|منه|منهم|من كذا|من كذي|من هذا|من هذي|من هذول|من اللي|بشوي|بعد)(?: |$)"
                     r"|(?:^| )longer(?: |$)")


def longest_asked(text):
    """-> "longest" («وش أطول رواية عندكم؟»), "longer" («أطول منها شوي») or None."""
    t = " ".join(num_canon(_pages_al(re.sub(r"[؟?!.،,]", " ", text or ""))).split())
    if re.search(r"(?:^| )(?:اطول|اكثر|اكبر|اضخم) من \d|(?:longer|more|bigger) than \d", t):
        return None   # «أطول من ٥٠٠ صفحة»، «أكثر من ٣٠٠ صفحات» (QADFIX32 #2): a page limit (page_limit), not an order
    if _LONGER.search(t):
        return "longer"
    return "longest" if _LONGEST.search(t) else None


_BRANCH = re.compile(r"(?:^| )(?:ب|في |ف)?(?:ال)?(?:فرع|فئه|فئة|مجال)\s+((?:ال)?[^\s]{3,}(?: (?:ال)?(?:شاب|شابه|عربيه|العربيه))?)"
                     r"|(?:^| )(?:in the |the )?([a-z]+(?: [a-z]+)?) (?:category|branch)(?: |$)")


def award_branch(text):
    """QADFIX27 #5 (QAD 2026-11-05 case 29.1, grave): «جائزة زايد في فرع الآداب» -> «الاداب» (the prize's branch, matched against
    book_awards.category), not the shelf «أدب»; None when no branch is named."""
    t = " ".join(normalize(re.sub(r"[؟?!.،,]", " ", text or "")).split())
    m = _BRANCH.search(t)
    if not m:
        return None
    b = (m.group(1) or m.group(2) or "").strip()
    return b if b and b not in ("هذا", "ذا", "the", "this") else None


def is_pages_phrase(phrase):
    return bool(re.search(r"صفح|pages?(?: |$)", " ".join(normalize(phrase).split())))


# ---------------------------------------------------------------------------
# «اكتب لي قصيدة عن المكتبة» (QADFIX4, case 48.1): Manar does not write texts; she says so kindly and offers
# books of that kind (poetry for a poem) instead of searching for a book «about the library»
# ---------------------------------------------------------------------------
_WRITE_ASK = re.compile(r"(?:^| )(?:اكتب|اكتبي|الف|الفي|ألف|ألفي|نظم|انظم|انظمي|write|compose)(?: لي| لنا| me| us)?\s+"
                        r"(?:(?:ل[يى] |لنا )?(?:قصيده|قصيدة|شعر|بيت|ابيات|قصه|قصة|حكايه|حكاية|خاطره|خاطرة|مقال|مقاله|رساله|رسالة|نص|a poem|poem|a story|story|an essay|essay))")


def write_asked(text):
    """-> "poem" / "story" / "text" when the visitor asks Manar to write something herself, else None."""
    t = " ".join(normalize(text).split())
    m = _WRITE_ASK.search(t)
    if not m:
        return None
    w = m.group(0)
    if re.search(r"قصيد|شعر|بيت|ابيات|poem", w):
        return "poem"
    if re.search(r"قص|حكاي|story", w):
        return "story"
    return "text"


# ---------------------------------------------------------------------------
# A reply that says what was asked for is not in stock (QADFIX4: Ibrahim 2026-10-16 «اذا كان الجواب الأولي ان الكتاب
# او السلسلة مو متوفرين … يحلل السؤال اكثر … ويشوف ايش الاحتمالات للطلب»)
# ---------------------------------------------------------------------------
_NOT_FOUND = re.compile(r"(?:غير|مو|مب|مش|ليس|ليست|ما هي|ماهي|ما هو|ماهو) ?(?:متوفر|متوفره|متاح|متاحه|موجود|موجوده)"
                        r"|ما (?:عندنا|عندي|لقيت|لقينا|حصلت|حصلنا|وجدت|وجدنا|فيه|في)(?: |$)|ماعندنا|مالقيت|لم (?:اجد|نجد)"
                        r"|ما عندي تفاصيل|ليس لدينا|لا يوجد|لا توجد|ما يوجد"
                        # «العندليب … ليست عن الحروب الصليبية»: a book offered while saying it is not on the subject
                        r"|(?:ليس|ليست|مو|مب|مش|ما هي|ماهي|ما هو|ماهو) (?:عن|بخصوص|حول|قريب|قريبه|له علاقه|لها علاقه) "
                        r"|not (?:available|in stock)|(?:isn t|is not|aren t|are not) (?:available|here|in stock)"
                        r"|(?:don t|do not|couldn t|could not|didn t) (?:have|find)|no books? (?:by|about|on)|found no|found nothing|nothing (?:about|on)")


def says_not_found(reply):
    return bool(_NOT_FOUND.search(" ".join(normalize(reply or "").split())))


# ---------------------------------------------------------------------------
# A book in stock is never called «غير متوفر» (QAD 2026-10-18, cases 30.2, 46.2, 46.3): the second look said
# «"الجزيرة" و"الظلام" مو موجودتين» and «الكتابان المذكوران في الرد السابق غير متوفرين بالعربية» about books
# in stock, in Arabic, some shown in the same reply. The sentence part that says so is replaced by a plain
# «in stock» line, or dropped when it names no book.
# ---------------------------------------------------------------------------
_UNAVAIL = re.compile(r"(?:غير|مو|مب|مش|ليس|ليست|ما هي|ماهي|ما هو|ماهو|ماهم|ما هم|مهي|مهو) ?(?:متوفر|متوفره|متوفرين|متوفرتين|متوفران|متوفرتان"
                      r"|متاح|متاحه|متاحين|موجود|موجوده|موجودين|موجودتين|موجودان|موجودتان)(?: |$)"
                      r"|not (?:available|in stock)|(?:isn t|is not|aren t|are not) (?:available|in stock|here)")
_PREV_REF = re.compile(r"(?:ال)?مذكور(?:ين|ان|تين|تان|ه)? (?:في |ب)(?:ال)?رد (?:ال)?سابق|(?:اللي|الي|التي|الذي|اللذين|اللتين) "
                       r"(?:ذكرت|عرضت|اقترحت|رشحت)(?:ها|هم|ه|هما)? (?:قبل|سابقا|لك)|(?:ال)?(?:كتابين|كتابان|كتب) (?:ال)?سابق(?:ين|ان|ه)"
                       r"|(?:the )?(?:books|ones|two) (?:i )?(?:mentioned|showed|suggested|offered) (?:before|earlier)")
_LANG_AR = re.compile(r"(?:^| )(?:بال)?عربي(?:ه)?(?: |$)|(?:^| )ال?عربيه(?: |$)|in arabic")
_LANG_EN = re.compile(r"(?:^| )(?:بال)?(?:انجليزي|انجليزيه|انقليزي|انقليزيه)(?: |$)|in english")
_CLAUSE_END = re.compile(r"[.؟?!؛;\n]")


def _clause_lang(c):
    if _LANG_EN.search(c):
        return "en"
    if _LANG_AR.search(c):
        return "ar"
    return None


def _in_stock_for(b, lang):
    return _avail(b) and (lang is None or (b.get("language") or "ar") == lang)


def fix_false_unavailable(reply, all_books, prev_ids=(), language="ar"):
    """-> (reply, changed). all_books: {id: book}. prev_ids: books shown in the conversation before."""
    if not reply or not _UNAVAIL.search(" ".join(normalize(reply).split())):
        return reply, False
    by_key = {}
    for b in all_books.values():
        by_key.setdefault(title_key(b.get("title") or ""), []).append(b)
    out, pos, changed = [], 0, False
    for m in re.finditer(r"[^.؟?!؛;\n]+[.؟?!؛;\n]?", reply):
        sent = m.group(0)
        # the part of the sentence from the clause that says «غير متوفر» (a clause starts after «،» or at «و»)
        parts = re.split(r"(،|,)", sent)
        clauses, buf = [], ""
        for p in parts:
            buf += p
            if p in ("،", ","):
                clauses.append(buf)
                buf = ""
        if buf:
            clauses.append(buf)
        kept = []
        for idx, c in enumerate(clauses):
            nc = " ".join(normalize(c).split())
            if not _UNAVAIL.search(nc):
                kept.append(c)
                continue
            lang = _clause_lang(nc)
            titles = re.findall(r"«([^»]{2,120})»|\"([^\"]{2,120})\"", c)
            titles = [a or b for a, b in titles]
            books = [by_key.get(title_key(t), []) for t in titles]
            stocked = [t for t, bs in zip(titles, books) if any(_in_stock_for(b, lang) for b in bs)]
            false_named = bool(stocked)
            missing = [t for t in titles if t not in stocked]
            false_prev = (not titles and _PREV_REF.search(nc) and prev_ids
                          and all(i in all_books and _in_stock_for(all_books[i], lang) for i in prev_ids))
            if not (false_named or false_prev):
                kept.append(c)
                continue
            changed = True
            end = _CLAUSE_END.search(sent[-1:]) and sent[-1:] or ""
            lead = "و" if c.strip().startswith("و") and kept else ""
            if false_named:
                names = " و".join(f"«{t}»" for t in stocked) if language != "en" else " and ".join(f"«{t}»" for t in stocked)
                line = (f" {lead}عندنا نسخ متوفرة من {names}" if language != "en"
                        else f" {'and ' if lead else ''}{names} {'is' if len(stocked) == 1 else 'are'} in stock")
                if missing:   # «"الجزيرة" و"الظلام" مو موجودتين»: only «الجزيرة» is really not here
                    gone = " و".join(f"«{t}»" for t in missing) if language != "en" else " and ".join(f"«{t}»" for t in missing)
                    line = (f" {lead}ما عندنا نسخ من {gone} الآن، و{line.strip()[1:] if lead else line.strip()}" if language != "en"
                            else f" {'and ' if lead else ''}{gone} {'is' if len(missing) == 1 else 'are'} not in stock now, but {line.strip()}")
                kept.append(line)
            # the rest of the sentence after it spoke of the same books («وما أقدر أؤكد توفرهما بلغة ثانية»)
            kept.append(end)
            break
        s2 = "".join(kept)
        s2 = re.sub(r"\s*[،,]\s*([.؟?!؛;\n]|$)", r"\1", s2)
        if s2.strip() in ("", ".", "؛", ";"):
            s2 = ""
        out.append(reply[pos:m.start()] + s2)
        pos = m.end()
    out.append(reply[pos:])
    fixed = re.sub(r"[ \t]{2,}", " ", "".join(out)).strip()
    return (fixed, True) if changed else (reply, False)


# INBOXFIX2 (library inbox 2026-10-29, conversation 8.7): a bare «recommend me a book», nothing else asked
_BARE_VERBS = {normalize(w) for w in "رشحي رشح رشحيلي رشحلي اقترح اقترحي اقترحيلي اقترحلي عطني عطيني اعطني ابي ابغى ابغا ابغي ودي "
               "recommend suggest give".split()}
_BARE_FILL = {normalize(w) for w in "لي ليا كتاب كتابا كتب رواية روايه شي شيء طيب اوكي تمام ممكن حلو ثاني ثانيه غيره زيه مثله "
              "me a book novel something another one please ok".split()}


def bare_recommend(message):
    """«رشّحي لي كتابًا»، «اقترح لي شي»، «recommend me a book»: a recommend verb and only filler words (no wish of its own)."""
    words = [w for w in re.findall(r"\w+", normalize(message or ""))]
    if not words or len(words) > 6:
        return False
    if not all(w in _BARE_VERBS or w in _BARE_FILL for w in words):
        return False
    if any(w in _BARE_REC for w in words):
        return True
    # «ابي» alone after a book may mean that book: a wanting verb counts only with «كتاب / شي / رواية»
    return any(w in _BARE_VERBS for w in words) and any(w in _BARE_OBJ for w in words)


_BARE_REC = {normalize(w) for w in "رشحي رشح رشحيلي رشحلي اقترح اقترحي اقترحيلي اقترحلي recommend suggest".split()}
_BARE_OBJ = {normalize(w) for w in "كتاب كتابا كتب رواية روايه شي شيء book novel something".split()}


# CHATFIX30 (library inbox 2026-10-29 13:24, §3 and §8.1–8.2) --------------------------------------------------------------
# (4) «ايش تعرف عن كتاب زمان الملائكة؟» was answered about book 1412 and still logged «كتاب زمان الملائكه» as «موضوع ما لقينا
# عنه كتابًا»; (1) «ابحث عن كتاب تشريح الفيلم» logged «كتاب تشريح الفيلم» as «رغبة لا نقدر نبحث بها» and its word «تشريح» was
# searched as a subject. A phrase that is the title of a book named, asked about or shown is that book, never a subject.
_TITLE_LEAD = re.compile(r"^(?:(?:عن|ابي|ابغي|ابغا|ابغى|عندكم|عندك|ابحث عن|دور|ادور)\s+)*"
                         r"(?:ال)?(?:كتاب|كتب|روايه|روايات|قصه|ديوان|book|novel|the book|the novel)\s+")


def phrase_title_key(text):
    """«كتاب زمان الملائكه» -> «زمان الملايكه»: a title as compared (title_key: ة/ه, hamza seats, numbers), without a
    leading «كتاب / رواية / عن كتاب …» and without its subtitle after «:»."""
    k = title_key(str(text or "").split(":")[0])
    prev = None
    while prev != k:
        prev, k = k, _TITLE_LEAD.sub("", k)
    return k.strip()


def phrase_is_title(phrase, titles):
    """Is this wish / topic phrase one of these titles (or a title with its subtitle)?"""
    pk = phrase_title_key(phrase)
    if not pk:
        return False
    for t in titles or ():
        if t and pk in (phrase_title_key(t), title_key(t)):
            return True
    return False


# (2) «سجون نختار أن نحيا فيها» (Doris Lessing's essays) was missing and two Hassan Aourid NOVELS were offered as close to it:
# fiction and non-fiction are kept apart when books are chosen for their likeness to a book we do not have.
_FICTION_FORMATS = {"novel", "short_stories", "poetry", "play", "comics"}
_NONFICTION_FORMATS = {"biography", "history", "thought", "psychology", "self_dev", "science", "travel", "religious",
                       "business", "essays", "criticism", "arts", "sociology"}
_FICTION_SHELVES = {"رواية", "قصص قصيرة", "قصص مصورة", "مسرحية", "شعر"}
_OPEN_SHELVES = {"", "أطفال", "يافعين", "أدب"}   # children's and teenagers' shelves hold both
_NONFICTION_TAGS = re.compile(r"non-?fiction|essays?|philosophy|psycholog|sociolog|histor(?:y|ical studies)|biograph|memoir|"
                              r"politic|criticism|textbook|self[- ]help|science|religio|film|cinema|art\b|economics|reference",
                              re.I)
_FICTION_TAGS = re.compile(r"(?<!non)(?<!non-)\bfiction|novels?\b|short stories|poetry|plays?\b|drama|fantasy|romance|"
                           r"thriller|mystery|horror|روايه|رواية|قصص|شعر", re.I)


def reference_is_fiction(ref, outside=None):
    """A book named by the visitor that we do not have -> True (fiction), False (non-fiction) or None (not known).
    The tags found outside (Goodreads, Open Library …) decide first; else the form the model knows it by."""
    tags = " | ".join([str(g) for g in ((outside or {}).get("genres") or [])] + [str(s) for s in ((outside or {}).get("subjects") or [])][:8])
    if tags:
        nf, fi = bool(_NONFICTION_TAGS.search(tags)), bool(_FICTION_TAGS.search(tags))
        if re.search(r"non-?fiction", tags, re.I):
            return False
        if fi and not nf:
            return True
        if nf and not fi:
            return False
    fmt = (ref or {}).get("format")
    if fmt in _FICTION_FORMATS:
        return True
    if fmt in _NONFICTION_FORMATS:
        return False
    subs = " ".join(str(s) for s in ((ref or {}).get("subgenres") or []))
    if re.search(r"رواي|novel", normalize(subs)):
        return True
    return None


def book_is_fiction(book):
    """Our book by its shelf -> True (a novel, stories, a play, poetry), False (thought, history, psychology …), None (a
    children's or teenagers' shelf, or none recorded)."""
    g = str((book or {}).get("genre") or "").strip()
    if g in _OPEN_SHELVES:
        return None
    if g == "قصص مصورة":
        # QADFIX24 (QAD 2026-10-30, case 45.1): a comic that tells history or a life («نضال النساء: 150 عامًا من النضال…»،
        # subgenres «تاريخ») is not a story like «ون بيس»
        subs = str((book or {}).get("subgenres") or "")
        if re.search(r"[\"']تاريخ[\"']|سيره|سيرة|مذكرات|[\"']فكر[\"']|[\"']علوم[\"']", subs) and not re.search(r"رواية تاريخية|روايه تاريخيه|فانتازيا|مغامر", subs):
            return False
    return g in _FICTION_SHELVES


def same_form_as(ref_fiction, book):
    """A likeness candidate kept for a reference of this form (None keeps everything)."""
    if ref_fiction is None:
        return True
    if ref_fiction == "selfhelp":
        # CHATFIX30b (4): a self-help / spiritual book -> self-help, psychology, religion, thought; never a memoir or a novel
        return str((book or {}).get("genre") or "").strip() in SELFHELP_SHELVES
    have = book_is_fiction(book)
    return have is None or have == ref_fiction


# (3) «عندكم كتب روي ياكوبسون؟» -> «تقصد «روي ياكوبسن»؟» -> «صحيح» -> «ما عندنا الآن كتب لـروي ياكوبسن»: the same name in
# two spellings, and the corrected one leads to no book of ours — the question was a wasted turn (author_flow.resolve).


# (5) Ibrahim 2026-10-30: «اقتراح ان منار تجاوب اولا قبل تقترح … ما يقترح على طول من اول سؤال اذا كان السؤال ما يوضح الاقتراح
# بشكل قريب. يعني سؤال عام بشكل كبير ويحتاج سؤال ثاني»; a visitor (inbox item 10): «أقترح أن تجيبني على سؤالي أولاً بمعزل عن الكتب
# التي تود اقتراحها … الحوار تسويقي». The words of a first message that say what to read, once greetings, asking words and
# «كتاب / شي» are taken out: none left («وش عندكم؟»، «رشحي لي رواية»، «مرحبا، ابغى اقرا شي»), or one bare subject with no
# asking word («فلسفة»، «السعادة») -> a general message: one short question first, no book yet.
_GEN_ASK = {normalize(w) for w in "رشحي رشح رشحيلي رشحلي اقترح اقترحي اقترحيلي اقترحلي عطني عطيني اعطني ابي ابغى ابغا ابغي "
            "بغيت ابغي ودي اريد أريد احتاج ابحث دور ادور دوري تنصحني تنصحيني تقترحين انصحني بدي "
            "recommend suggest give want need looking find".split()}
_GEN_QUESTION = {normalize(w) for w in "وش ايش شو ماذا ما هو هي شنو عندكم عندك عندكن فيه عندنا متوفر "
                 "what which do you have any is are".split()}
_GEN_FILL = {normalize(w) for w in "لي ليا انا اني حاب احب يا منار عن في من على الى و او ثم شي شيء اي أي حلو حلوه زين ممكن لو سمحت "
             "طيب اوكي تمام كتاب كتابا كتب الكتب كتابين اقرا اقرأ اقراء اقراه قراءه القراءه للقراءه اقراها اقراه بقرا "
             "اليوم الحين الحين جديد مكتبه المكتبه "
             "i me to read reading a an the some something books book please about for good start begin starting".split()}
_GEN_FORM = {normalize(w) for w in "رواية روايه روايات الروايات قصص القصص قصه قصة شعر ديوان novel novels stories poetry".split()}
_GEN_SURPRISE = re.compile(r"فاج|يفاج|عشوائي|surprise|random")


def general_words(message):
    """-> (the words that say what to read, an asking word or a question was used)."""
    import keyword_prefs as _kp
    words = [w for w in re.findall(r"\w+", normalize(message or "")) if not w.isdigit() or len(w) > 2]
    asked = any(w in _GEN_ASK or w in _GEN_QUESTION for w in words) or bool(re.search(r"[؟?]", str(message or "")))
    left = [w for w in words if w not in _GEN_ASK and w not in _GEN_QUESTION and w not in _GEN_FILL
            and w not in _kp.GREETING_WORDS and w not in _BARE_VERBS and (w not in _BARE_FILL or w in _GEN_FORM)]
    # a form word counts only beside a subject («رواية رعب» is two words; «رشحي لي رواية» none)
    return (left if any(w not in _GEN_FORM for w in left) else []), asked


# QADFIX24 (case 13.1): fields named in two words, said alone as a first message
_TWO_WORD_FIELDS = {" ".join(normalize(w).split()) for w in (
    "علم نفس", "علم النفس", "علم اجتماع", "علم الاجتماع", "تطوير ذات", "تطوير الذات", "تطوير ذاتي",
    "ادب رحلات", "أدب رحلات", "أدب الرحلات", "ادب الرحلات", "تنمية بشرية", "التنمية البشرية",
    "نقد أدبي", "النقد الأدبي", "فلسفة إسلامية", "تاريخ إسلامي", "التاريخ الإسلامي", "self help")}


def general_first_message(message):
    """CHATFIX30 (5): is this (first) message too general to choose a book by? Forced only where the model's own «clarify»
    (OWN1, 2026-10-11) was seen to fail: one bare subject with no asking word and no question of his own («فلسفة»، «السعادة»،
    «كتب فلسفة»), or nothing to choose by at all («وش عندكم؟»، «مرحبا، ابغى اقرا شي»). A form asked for («رواية»، «أبغى رواية»)
    or a bare «ابي كتاب» stays the model's call (it asks first when it reads the request as too vague)."""
    import keyword_prefs as _kp
    if not str(message or "").strip() or _GEN_SURPRISE.search(normalize(message)):
        return False
    if books_about_asked(message) or activity_wish(message):
        return False   # CHATFIX30b (1), (3): «كتب عن أفغانستان»، «التلوين» are clear requests
    words = re.findall(r"\w+", normalize(message))
    left, _asked = general_words(message)
    subjects = [w for w in left if w not in _GEN_FORM]
    has_form = any(w in _GEN_FORM for w in words)
    has_verb = any(w in _GEN_ASK or w in _BARE_VERBS for w in words)
    greeting = any(w in _kp.GREETING_WORDS for w in words)
    wh = bool(_SUBJ_Q.search(" ".join(normalize(message).split())))
    if not subjects:
        return not has_form and (not has_verb or greeting)
    if len(subjects) == 2 and " ".join(subjects) in _TWO_WORD_FIELDS:
        # QADFIX24 (QAD 2026-10-30, case 13.1): «علم نفس» alone is one field like «فلسفة», not two subjects
        subjects = [" ".join(subjects)]
    return len(subjects) == 1 and not has_form and not has_verb and not (wh and not _AVAIL_Q.search(" ".join(normalize(message).split())))


_SUBJ_Q = re.compile(r"(?:^| )(?:وش|ايش|شو|ماذا|ما هو|ما هي|ما هي|ليش|ليه|لماذا|كيف|هل|متى|مين|من هو|من هي|what|why|how|who|is|are|does)(?: |$)")
_AVAIL_Q = re.compile(r"(?:^| )(?:عندكم|عندك|عندكن|فيه|في عندكم|متوفر|متوفره|موجود|موجوده|have|got|available|in stock)(?: |$)")


def subject_question(message):
    """CHATFIX30 (5): the visitor asks a question of his own about a subject («ليش نقرأ الفلسفة؟»، «وش الفرق بين الفلسفة وعلم
    النفس؟») — not whether we have something («عندكم روايات رعب؟») — so its answer comes before any book."""
    t = " ".join(normalize(message or "").split())
    return (bool(re.search(r"[؟?]", str(message or ""))) and bool(_SUBJ_Q.search(t)) and not _AVAIL_Q.search(t)
            and bool(general_words(message)[0]))


# CHATFIX30 (1): what a missing book is about, from the tags found outside (Goodreads, Open Library …) when the model does not
# know it: «تشريح الفيلم» came back tagged "Film, Nonfiction, Textbooks" with no theme from the model, and no film book of ours
# was found. Each tag gives the Arabic words our books' titles, keywords and subgenres use for that subject
_TAG_WORDS = {
    "film": "سينما سينمائي سينمائيه افلام فيلم سيناريو", "films": "سينما سينمائي افلام فيلم سيناريو",
    "cinema": "سينما سينمائي سينمائيه افلام فيلم سيناريو", "movies": "سينما افلام فيلم", "screenwriting": "سيناريو",
    "philosophy": "فلسفه فلسفي فلسفيه", "psychology": "نفسي نفسيه", "sociology": "اجتماع اجتماعي",
    "history": "تاريخ تاريخي", "politics": "سياسه سياسي", "political science": "سياسه سياسي", "economics": "اقتصاد",
    "religion": "دين ديني", "poetry": "شعر", "music": "موسيقي موسيقى", "art": "فنون رسم تشكيلي", "art history": "فنون",
    "architecture": "عماره معمار", "photography": "تصوير", "theatre": "مسرح", "theater": "مسرح", "drama": "مسرح",
    "linguistics": "لغه لسانيات", "education": "تعليم تربيه", "parenting": "تربيه", "travel": "رحلات رحله",
    "biography": "سيره", "memoir": "مذكرات سيره", "essays": "مقالات", "literary criticism": "نقد", "criticism": "نقد",
    "cooking": "طبخ", "food": "طبخ", "sports": "رياضه", "mathematics": "رياضيات",
    "physics": "فيزياء", "astronomy": "فلك فضاء", "biology": "احياء", "medicine": "طب", "feminism": "نسويه المراه",
}


def outside_subject_words(outside):
    """The subject words for a book found outside, from its tags -> set of normalized words (no «ال»)."""
    out = set()
    for tag in list((outside or {}).get("genres") or []) + list((outside or {}).get("subjects") or [])[:8]:
        t = " ".join(str(tag or "").lower().replace("-", " ").split())
        words = _TAG_WORDS.get(t)
        if words:
            out |= {strip_al(w) for w in normalize(words).split()}
    return out


# CHATFIX30 follow-up (MGR replay 2026-10-30 of the 21 library phrases): «هنري ترويا» and «روي ياكوبسن» as a first message were
# read by the model as «clarify» with no author request and got «تقصد تبغى كتابًا لهنري ترويا؟ وش النوع…» instead of «ما عندنا
# الآن كتب لـهنري ترويا…» (and no missing-author log). A message that is only a person's name is a request for that author.
def looks_like_name(message, first_names):
    """Two to four words, the first a first name some author of ours carries (`first_names`: normalized first words of the
    catalog's and the author records' names), no asking, question, form, greeting or reading word, no digit, and no word the
    keyword reader understands as a wish («سرد بسيط»، «تقييم مرتفع»، «جبر القلوب» are not names)."""
    import keyword_prefs as _kp
    text = str(message or "").strip(" ؟?!.،,")
    if not text or re.search(r"\d|[؟?]", text):
        return False
    words = normalize(text).split()
    if not 2 <= len(words) <= 4 or words[0] not in (first_names or ()):
        return False
    for i, w in enumerate(words):
        if i == 0:
            # a first name of ours («علي» is also «على» once normalized): only an asking word or a greeting is refused here
            if w in _GEN_ASK or w in _BARE_VERBS or w in _kp.GREETING_WORDS:
                return False
            continue
        if (w in _GEN_ASK or w in _GEN_QUESTION or w in _GEN_FILL or w in _GEN_FORM or w in _BARE_VERBS or w in _BARE_FILL
                or w in _kp.GREETING_WORDS or len(w) < 2):
            return False
        if _kp.understood_anything(_kp.understand([w])):
            return False
    return True


# CHATFIX30b (2), Ibrahim 2026-10-30 on a visitor's suggestion «أقترح الفصحى في الحوار حول الكتب»: «ممتاز خلها تساير السائل
# باسلوبه». A visitor who writes Modern Standard Arabic is answered in فصحى; one who writes in a dialect keeps the dialect.
_DIALECT_WORDS = {normalize(w) for w in (
    "وش ايش إيش شو ابغى أبغى ابغا ابغي ابي أبي ابيه أبيه ابغاه تبغى تبغي تبي ودي ودّي شلون كذا مو ليش زين حق يبي يبغى عشان "
    "علشان الحين اللي بس شي عطني اعطني وين ايه إيه هذي هذولا زي برضه شوي شوية يعطيك مب مش ابى "
    # CHATFIX30c (5): «ليه ما اقترحتها لي؟» was answered in فصحى after earlier فصحى messages — «ليه» and other common
    # Gulf / Hijazi / Najdi words were missing (the latest clear signal wins)
    "ليه وشلون كيفك حيل يعني ابا أبا ابيه عطيني ودّي تكفى تكفين يالله لاهنت ماعليه معليش عاد وشو ايوه اوكي").split()}
_DIALECT_PREFIX = re.compile(r"(?:^| )هال\w{2,}")
_FUSHA_WORDS = {normalize(w) for w in (
    "أريد اريد لديكم لديك لدي ماذا لماذا أود اود أرجو ارجو الذي التي الذين اللذين سوف ليس لست يوجد توجد أرغب ارغب "
    "أبحث ابحث رجاء أفضّل").split()}
_FUSHA_PHRASES = re.compile(r"(?:^| )(?:هل (?:لديكم|عندكم|يوجد|توجد|تنصح|تنصحين|يمكن|من)|ما هو|ما هي|من فضلك|أبحث عن|ابحث عن|"
                            r"أود أن|اود ان|هل)(?: |$)")


def message_register(text, titles=()):
    """One visitor message -> "fusha", "dialect" or None (no clear signal). The words of a book title the visitor quotes are
    not his register (QADFIX25, QAD 2026-10-31 case 23.1: «الرجل الذي كان يحب الكلاب متوفرة؟» was answered in فصحى)."""
    t = " ".join(normalize(text or "").split())
    for _ti in sorted({" ".join(normalize(re.split(r"\s*[:：(\[–—]\s*", x or "")[0]).split()) for x in titles if x}, key=len, reverse=True):
        if len(_ti.split()) >= 2 and _ti in t:
            t = " ".join(t.replace(_ti, " ").split())
    if not t or not re.search(r"[؀-ۿ]", str(text or "")):
        return None
    words = t.split()
    d = sum(1 for w in words if w in _DIALECT_WORDS) + len(_DIALECT_PREFIX.findall(" " + t))
    f = sum(1 for w in words if w in _FUSHA_WORDS) + len(_FUSHA_PHRASES.findall(" " + t + " "))
    if f > d:
        return "fusha"
    if d > f:
        return "dialect"
    return None


def visitor_register(user_messages, titles=()):
    """The latest visitor message with a clear signal decides; none -> "dialect" (the default)."""
    for m in reversed(list(user_messages or [])):
        r = message_register(m, titles)
        if r:
            return r
    return "dialect"


FUSHA_GUIDANCE = (" الزائر يكتب بالفصحى: اكتب ردك كله بالعربية الفصحى المبسّطة (تريد، ما، لدينا، الآن، شيء، هل تفضّل…)، ولا تستعمل"
                  " كلمات اللهجة (تبغى، وش، عندنا، الحين، شي، اللي، ولا بمعنى أم).")
DIALECT_GUIDANCE = " الزائر يكتب باللهجة: رد عليه باللهجة السعودية البسيطة كعادتك."

# safe swaps only (whole words, with an optional «و / ف» before them); phrases first
_FUSHA_PHRASE_SWAPS = [
    (r"تبغى أقترح لك|تبغاني أقترح لك|تبي أقترح لك", "هل تريد أن أقترح عليك"),
    (r"تبغى أعرض لك|تبغاني أعرض لك", "هل تريد أن أعرض عليك"),
    (r"ما عندنا", "ليس لدينا"), (r"ما عندي", "ليس لدي"), (r"ما لقيت", "لم أجد"), (r"ما لقينا", "لم نجد"),
    (r"ما فيه", "لا يوجد"), (r"وش اللي", "ما الذي"), (r"إيش اللي|ايش اللي", "ما الذي"),
    (r"هالفترة|هالفتره", "هذه الفترة"), (r"هالكتاب", "هذا الكتاب"), (r"هالرواية|هالروايه", "هذه الرواية"),
    (r"هالموضوع", "هذا الموضوع"), (r"هالنوع", "هذا النوع"),
    (r"تبغى تقرأ|تبغى تقرا|تبي تقرأ|تبي تقرا", "تريد أن تقرأ"), (r"تحب تقرأ|تحب تقرا", "تحب أن تقرأ"),
    (r"ودك تقرأ|ودك تقرا|ودّك تقرأ|ودّك تقرا", "تودّ أن تقرأ"),
]
_FUSHA_WORD_SWAPS = {
    "تبغى": "تريد", "تبغي": "تريد", "تبغاه": "تريده", "تبغاها": "تريدها", "تبغاهم": "تريدهم", "تبي": "تريد", "تبيه": "تريده",
    "تبيها": "تريدها", "يبغى": "يريد", "يبي": "يريد", "أبغى": "أريد", "ابغى": "أريد",
    "وش": "ما", "ايش": "ما", "إيش": "ما", "شو": "ما", "عندنا": "لدينا", "عندك": "لديك", "عندكم": "لديكم", "عندي": "لدي",
    "الحين": "الآن", "شي": "شيء", "ليش": "لماذا", "وين": "أين", "زين": "جيد", "كذا": "هكذا", "هذي": "هذه", "هذولا": "هؤلاء",
    "زي": "مثل", "برضه": "أيضًا", "شوي": "قليلًا", "شوية": "قليلًا", "مو": "غير", "مب": "غير", "إيه": "نعم", "ايه": "نعم",
    "أقدر": "أستطيع", "تقدر": "تستطيع", "نقدر": "نستطيع", "يقدر": "يستطيع", "تحس": "تشعر", "تحسّ": "تشعر",
    "يحسون": "يشعرون", "يحسّون": "يشعرون", "تقرا": "تقرأ", "عجبك": "أعجبك", "عجبه": "أعجبه", "ودك": "تودّ", "ودّك": "تودّ",
    "حلو": "جميل", "حلوة": "جميلة", "لقيت": "وجدت", "لقينا": "وجدنا", "تلقى": "تجد", "أبشر": "حسنًا", "ابشر": "حسنًا",
}
_FUSHA_WORD_RX = re.compile(r"(?<![\wً-ْ])(و|ف)?(" + "|".join(sorted((re.escape(k) for k in _FUSHA_WORD_SWAPS), key=len, reverse=True))
                            + r")(?![\wً-ْ])")


def fusha_rewrite(text, protect=()):
    """A reply for a visitor who writes فصحى: the commonest dialect words in it swapped for their فصحى words (whole words
    only), a question's «X ولا Y» made «X أم Y», and a sentence opening with «تريد …» made «هل تريد …».
    CHATFIX30c (6), AUDIT 2026-10-30 P2: «سلسلة التربية الاجتماعية: خيرا تعمل خيرا تلقى» came out «… خيرا تجد». Every «…»
    quoted span and every string in `protect` (titles and author names) is kept verbatim."""
    if not text or not re.search(r"[؀-ۿ]", text):
        return text
    kept = []
    def _hold(s):
        kept.append(s)
        return "" + str(len(kept) - 1) + ""
    out = re.sub(r"«[^»]{1,200}»|“[^”]{1,200}”|\"[^\"]{1,200}\"", lambda m: _hold(m.group(0)), text)
    for p in sorted({str(p).strip() for p in protect or () if p and len(str(p).strip()) >= 2}, key=len, reverse=True):
        if p in out:
            # «لحسن زين الدين»، «وخيرا تعمل…»: a joined «ل / و / ب / ف» before the name is held with it
            out = re.sub(r"(?<![\wً-ْ])(?:لل|[ولبف])?" + re.escape(p) + r"(?![\wً-ْ])", lambda m: _hold(m.group(0)), out)
    out = _fusha_words(out)
    for i in range(len(kept) - 1, -1, -1):   # inner holds were made after outer ones: restore from the last
        out = out.replace("" + str(i) + "", kept[i])
    return out


def _fusha_words(out):
    for pat, rep in _FUSHA_PHRASE_SWAPS:
        out = re.sub(r"(?<![\wً-ْ])(" + pat + r")(?![\wً-ْ])", rep, out)
    out = _FUSHA_WORD_RX.sub(lambda m: (m.group(1) or "") + _FUSHA_WORD_SWAPS[m.group(2)], out)
    # «خفيف ولا عميق؟» -> «خفيف أم عميق؟» inside a question only
    out = re.sub(r"[^.!؟?]*[؟?]", lambda m: re.sub(r"(?<=\S) ولا (?=\S)", " أم ", m.group(0)), out)
    # «… . تريد أن أقترح …؟» -> «هل تريد …؟»
    out = re.sub(r"(^|[.!؟?،:]\s*)(تريد|تفضّل|تفضل|تحب|تودّ|تود|تميل)(?= [^.!؟?]*[؟?])", lambda m: m.group(1) + "هل " + m.group(2), out)
    out = re.sub(r"هل هل ", "هل ", out)
    return out


# CHATFIX30b (1), Ibrahim 2026-10-30 on «كتب عن أفغانستان»: «واضح وتقترح». Books asked for ABOUT a subject («كتب عن X»، «كتاب عن X»،
# «عندكم كتب عن X؟»، «روايات عن X») are a clear request; a bare word («أفغانستان»، «فلسفة») stays a general first message.
_BOOKS_ABOUT = re.compile(r"(?:^| )(?:ال)?(?:كتب|كتاب|كتابا|كتابين|روايه|روايات|قصص|قصه|books?|novels?|stories) (?:عن|حول|بخصوص|about|on) \S")


def books_about_asked(message):
    return bool(_BOOKS_ABOUT.search(" ".join(normalize(message or "").split())))


# CHATFIX30b (3): «التلوين» / «كتب تلوين» / «ابي كتاب تلوين لبنتي» — colouring and other activity books are children's books
# («مجموعة التلوين الكبيرة»، «كتاب تلوين حروف وحيوانات»), a concrete kind of book, not a general wish
_ACTIVITY = re.compile(r"(?:^| )(?:[وبل]|بال|لل|وال)?(?:ال)?(?:تلوين|تلوينيه|ملصقات|استيكرات|ستيكرات|انشطه|نشاطات|متاهات|متاهه|"
                       r"قص ولصق|كلمات متقاطعه|colou?ring|activity books?|sticker books?|mazes?)(?: |$)")
_ACTIVITY_ADULT = re.compile(r"(?:^| )(?:للكبار|كبار|الكبار|لي انا|لنفسي|للبالغين|adults?|grown ?ups?|for me)(?: |$)")


def activity_wish(message):
    """A children's activity book asked for (colouring, stickers, mazes …), not for a grown-up («تلوين للكبار»)."""
    t = " ".join(normalize(message or "").split())
    return bool(_ACTIVITY.search(t)) and not _ACTIVITY_ADULT.search(t)


# CHATFIX30b (4): «قوة الآن» (Eckhart Tolle, self-help / spirituality) missing — «ظلام مرئي: مذكرات الجنون» (a memoir of
# depression) was offered as close to it. A self-help or spiritual book is answered from those shelves only
_SELFHELP_TAGS = re.compile(r"self[- ]help|spirituality|personal development|personal growth|mindfulness|meditation|inspirational|"
                            r"تطوير الذات|تطوير ذات|تنميه بشريه|روحاني", re.I)
SELFHELP_SHELVES = {"تطوير ذاتي", "علم نفس", "دين", "فكر"}


def reference_form(ref, outside=None):
    """reference_is_fiction, with one narrower non-fiction kind: "selfhelp" for a self-help / spirituality book."""
    fic = reference_is_fiction(ref, outside)
    if fic is True:
        return True
    first_tags = " | ".join(str(g) for g in ((outside or {}).get("genres") or [])[:3])   # its main tags only
    subs = " ".join(str(s) for s in ((ref or {}).get("subgenres") or []))
    if ((ref or {}).get("format") == "self_dev" or (first_tags and _SELFHELP_TAGS.search(first_tags))
            or _SELFHELP_TAGS.search(normalize(subs))):
        return "selfhelp"
    return fic


# CHATFIX30c (2), conversation c-mvvbiykv (2026-10-30 21:41): «فاجئيني بكتب جديدة» got «تبغى المفاجأة رواية، ولا كتاب غير روائي؟»
# — «فاجئيني» is a request for books now; and the «جديدة» of that request stayed through «شي في علم النفس» and «ابا اجمل روايه
# رومنسيه» (romance limited to 2023+). A wish belongs to the request it came with: it carries only into follow-ups of it
def surprise_asked(text):
    return bool(_GEN_SURPRISE.search(" ".join(normalize(text or "").split())))


_FOLLOW_WORDS = {normalize(w) for w in (
    "غيرها غيره غيرهم غير ثاني ثانيه ثانية ثانين ثانيين اخرى أخرى اخر آخر الثاني كمان زياده زيادة المزيد بعد ايضا أيضا "
    "اقصر أقصر اطول أطول ارخص أرخص اخف أخف اسهل أسهل اثقل أثقل احدث أحدث اقدم أقدم اصغر أصغر اكبر أكبر منها منه منهم "
    "شي شيء طيب بس اوكي تمام لا ايه إيه نعم ابي ابغى أبغى ابغا ابا أبا ودي هات عطني عطيني اعطني ورني ورّني وريني "
    "اقتراحات اقتراح ترشيحات خيارات خيار ثانيه وحده واحد واحده كتاب كتب رواية روايه "
    "more other others another else shorter cheaper lighter newer one ones different").split()}


def followup_only(text):
    """A message that only asks for more of the same or a nearer fit («غيرها»، «أقصر»، «شي ثاني»، "another one") and names no
    new subject, kind or reader."""
    words = re.findall(r"\w+", normalize(text or ""))
    return bool(words) and all(w in _FOLLOW_WORDS or w in _GEN_FILL for w in words)


# CHATFIX30c (1), real chat 2026-10-30 23:01: «ابغى روايات تاريخية تقييمها عالي» -> «أغالب مجرى النهر … بعض القرّاء أعطوه تقييمًا
# عاليًا: فاز بالبوكر العربية 2026» and «قطار إلى سمرقند … وتقييم بعض القرّاء له مرتفع» — a prize called a rating, and a rating
# claimed with no source. Ratings come only from our data: books.goodreads_rating (GRRATE), shown with 5 ratings or more
# (Ibrahim 2026-11-01 «غير الحد للاثنين الى ٥ تقاييم»: card and chat, was 50)
RATING_MIN_COUNT = 5
_RATING_WISH = re.compile(
    r"(?:^| )(?:ب|و)?(?:ال)?تقييم(?:ه|ها|هم|ات|اته|اتها)? (?:ال)?(?:عالي|عاليه|عال|مرتفع|مرتفعه|ممتاز|ممتازه|قوي|حلو|كويس|زين)(?: |$)"
    r"|(?:^| )(?:ال)?(?:اعلي|اعلى|الاعلي|الاعلى|افضل|الافضل|اقوي|احسن) (?:تقييم|تقييما|التقييمات|تقييمات)(?: |$)"
    r"|(?:^| )(?:عالي|عاليه|مرتفع|مرتفعه) (?:ال)?تقييم(?: |$)|(?:^| )(?:مقيم|مقيمه|مقيّم) (?:عالي|بشكل عالي|زين)(?: |$)"
    r"|highly rated|high(?:ly)?[- ]rated|best[- ]rated|top[- ]rated|high ratings?|well[- ]rated|good ratings?")


def high_rating_wished(text):
    """«تقييمها عالي»، «الأعلى تقييمًا»، «highly rated»: a wish for books readers rated highly."""
    return bool(_RATING_WISH.search(" ".join(normalize(text or "").split())))


def rating_phrase(u):
    """An unsupported wish that is only the rating wish («تقييم مرتفع») — never a subject to search."""
    return high_rating_wished(u)


_RATING_CLAIM = re.compile(
    r"(?:و)?(?:بعض |كثير من |اغلب |أغلب |معظم )?(?:ال)?(?:قرّاء|قراء)?\s*(?:أعطوه|اعطوه|أعطوها|اعطوها|يعطونه|يعطونها|منحوه|منحوها)\s+"
    r"تقييم(?:ًا|ا)?\s+(?:عاليًا|عاليا|عالٍ|عالي|مرتفعًا|مرتفعا|ممتازًا|ممتازا)"
    r"|(?:و)?تقييم(?:ه|ها|اته|اتها)?(?:\s+(?:بعض\s+)?(?:ال)?(?:قرّاء|قراء))?(?:\s+(?:له|لها))?\s+(?:عالٍ|عالي|عالية|مرتفع|مرتفعة|ممتاز|ممتازة)"
    r"|(?:و)?(?:عالي|مرتفع|مرتفعة|عالية) التقييم"
    r"|(?:and )?(?:is |are )?(?:highly|well) rated|(?:has |with )?(?:a )?(?:high|strong|excellent) ratings?|rated highly",
    re.IGNORECASE)


def strip_unsourced_rating(reply, rated_titles):
    """Every «rated highly» claim about a book whose facts carry no rating is taken out of the reply (the rest of its
    sentence stays: «… فاز بالبوكر العربية 2026» is a prize, said as a prize). A claim next to a rated book stays."""
    if not reply or not _RATING_CLAIM.search(reply):
        return reply
    rated = {title_key(t) for t in rated_titles or () if t}
    sentences = re.split(r"(?<=[.!؟?])\s+", reply)
    out = []
    for s in sentences:
        if not _RATING_CLAIM.search(s):
            out.append(s)
            continue
        titles = [title_key(x) for g in re.findall(r"«([^»]{1,150})»|“([^”]{1,150})”", s) for x in g if x]
        if titles and any(t in rated for t in titles):
            out.append(s)
            continue
        # clause by clause («؛» / «،» / «:»): the claim goes, the clause's other words stay (titles are held whole)
        held = []
        s = re.sub(r"«[^»]{1,150}»|“[^”]{1,150}”", lambda m: (held.append(m.group(0)), "" + str(len(held) - 1) + "")[1], s)
        parts = re.split(r"(\s*[؛;،,:]\s*)", s)
        kept = []
        for p in parts:
            if _RATING_CLAIM.search(p):
                rest = _RATING_CLAIM.sub("", p).strip(" ،,؛;:")
                if len(rest.split()) >= 2 and re.search(r"\w", rest.strip(" .!؟?")):
                    kept.append(rest)
                else:
                    end = re.search(r"[.!؟?]+\s*$", p)   # the sentence's own end stays
                    if end:
                        kept.append(end.group(0).strip())
                continue
            kept.append(p)
        s2 = "".join(kept)
        s2 = re.sub(r"\s*([؛;،,:])\s*(?:[؛;،,:]\s*)+", r"\1 ", s2)          # doubled separators
        s2 = re.sub(r"\s*[؛;،,:]\s*(?=[.!؟?]|$)", "", s2).strip()            # a separator left before the end
        s2 = re.sub(r"\s+([.!؟?])", r"\1", s2)
        for i, h in enumerate(held):
            s2 = s2.replace("" + str(i) + "", h)
        if re.search(r"\w", s2):
            out.append(s2)
    return " ".join(x for x in out if x)


def arabic_digits(text):
    return str(text).translate(str.maketrans("0123456789.,", "٠١٢٣٤٥٦٧٨٩٫٬"))


# CHATFIX30c (4), real chat eae1871d turn 1: «… أو «عصر الحب» لنجيب محفوظ…، وبعض القرّاء يحسّون «التميمة» قراءة خفيفة عاطفيًا،
# وهي ١٧٦ صفحة…» — a book on the cards named only as the object of a readers' impression, never introduced with its author.
# Every card book the reply names gets its author at its first mention when the reply never names him
_NO_AUTHOR_NAMES = {normalize(x) for x in ("غير معروف", "مجهول", "unknown", "مجموعة مؤلفين", "مجموعه مؤلفين", "various")}


_IMPRESSION_BEFORE = re.compile(r"(?:يحسّون|يحسون|يحس|يحسّ|يحسونها|يجدون|يرون|وصفوا|يصفون|يشعرون|يعتبرون|وجدوا|اعتبروا|"
                                r"feel|feels|find|finds|found|describe|describes|described|consider|considers)\s*$", re.IGNORECASE)


def _lam(name):
    """«لـ» before a name as Manar writes it: «لنبيل فاروق»، «للطيب صالح»."""
    return ("لل" + name[2:]) if name.startswith("ال") else ("ل" + name)


def introduce_authors(reply, books, language="ar"):
    """books: [(title, author as displayed)] of the cards. -> the reply with «X» لـAuthor at X's first mention, for each card
    book the reply names by its title while its author's name appears nowhere in it."""
    if not reply:
        return reply
    out = reply
    norm_reply = " " + " ".join(normalize(re.sub(r"«[^»]*»|“[^”]*”", " ", out)).split()) + " "
    for title, author in books or ():
        a = " ".join(str(author or "").split())
        if not title or not a or normalize(a).strip() in _NO_AUTHOR_NAMES:
            continue
        aw = [w for w in normalize(a).split() if len(strip_al(w)) >= 3]
        if not aw or " " + " ".join(normalize(a).split()) + " " in norm_reply or any(" " + w + " " in norm_reply
                                                                                   or " ل" + w + " " in norm_reply for w in aw[-1:]):
            continue   # he is named somewhere (in full, or by his family name)
        keys = {title_key(title), title_key(str(title).split(":")[0])}
        for m in re.finditer(r"«([^»]{1,150})»|“([^”]{1,150})”", out):
            q = m.group(1) or m.group(2)
            if title_key(q) not in keys:
                continue
            before = out[max(0, m.start() - 40):m.start()]
            if not _IMPRESSION_BEFORE.search(before):
                break   # named the ordinary way; only a title that first comes as the object of an impression is fixed
            after = out[m.end():m.end() + 30]
            if re.match(r"\s*(?:ل(?!كن|ان |أن |ها |ه |هم |لأطفال|لاطفال|لكبار|لقراء|لقرّاء|لمبتدئين)\S{2,}|by\s)", after):
                break   # an author already follows the title
            add = (" by " + a) if (language == "en" or m.group(2)) else (" " + _lam(a))
            out = out[:m.end()] + add + out[m.end():]
            break
    return out


_WHY_WORDS = {normalize(w) for w in "ليش ليه وش ايش شو لماذا ماذا كيف هل وين متى why what how where when".split()}


def narrowing_only(text):
    """CHATFIX30c (2): a short answer that narrows the request just made («رواية»، «أحب الروايات الغامضة»، «ابي شي غير روائي»)
    — no question of its own («ليش اخترت لي هذه الكتب») and no new field («شي في علم النفس» after «روايات جديدة»)."""
    raw = str(text or "")
    words = re.findall(r"\w+", normalize(raw))
    if not words or len(words) > 6 or re.search(r"[؟?]", raw):
        return False
    if any(w in _WHY_WORDS for w in words):
        return False
    # a new field («شي في علم النفس» after «روايات جديدة») is a new request; a form or a mood («الغامضة») narrows
    try:
        genres = set(keyword_prefs.understand([raw]).get("genres") or ())
    except Exception:
        genres = set()
    return genres <= {"رواية", "قصص قصيرة", "شعر", "مسرحية", "قصص مصورة"}


# CHATFIX31 (1d): "a light Arabic novel" is a novel first written in Arabic; "in Arabic" / "Arabic edition" is about the copy
_AR_ORIGIN = re.compile(r"\barabic (?:\w+ )?(?:novels?|books?|literature|fiction|stories|story|poetry|poems?|writers?|authors?|novelists?|classics?)\b"
                        r"|\b(?:novels?|books?|literature) (?:by|from) (?:an? )?arab(?:ic)? (?:writers?|authors?)\b|\barab (?:writers?|authors?|novelists?)\b",
                        re.IGNORECASE)
_AR_COPY = re.compile(r"\bin arabic\b|\barabic (?:edition|version|translation|copy|copies)\b|\btranslated (?:in|into) arabic\b", re.IGNORECASE)


def arabic_origin_asked(text):
    t = str(text or "")
    return bool(_AR_ORIGIN.search(t)) and not _AR_COPY.search(t)


# CHATFIX31 (1a): the card's «why this book» line. The labels come from the scoring (score_book, score_profile_card, the subject
# search): the most specific first, the bare shelf only alone
_SHELF_ONLY = re.compile(r"^من تصنيف «[^»]+» كما طلبت$")
_REASON_RANK = [
    (re.compile(r"^(?:يتناول|عنوانه عن|نبذته تذكر|قريب من موضوع) «"), 0),       # the subject asked for
    (re.compile(r"^(?:قريب من|يشبه|مثل|من نفس نوع|عنوانه مأخوذ من) «"), 1),         # «مثل X»
    (re.compile(r"^(?:فاز|فازت|مؤلفه فاز|مؤلفها فازت|وصل|وصلت|ضمن|في القائمة)"), 2),  # a prize
    (re.compile(r"^تحوّل إلى"), 2),
    (re.compile(r"^(?:من أعمال|كاتبه|كاتبته|لمؤلف|مانجا)"), 3),
    (re.compile(r"^تصنيفه الفرعي"), 4),
    (re.compile(r"^(?:أجواؤه|إيقاعه|قراءة|مناسب|لغته|أسلوبه|ما يحتاج|يرجّعك)"), 5),
    (re.compile(r"^(?:قصير نسبيًا|عمل طويل|عدد صفحاته|سعره)"), 6),
]


def _rank(r):
    for rx, n in _REASON_RANK:
        if rx.search(r):
            return n
    return 7 if _SHELF_ONLY.search(r) else 6


def card_reasons(reasons):
    """The reasons in the order a visitor reads them best; the bare shelf goes when anything more specific is there."""
    rs = [r for r in reasons if isinstance(r, str) and r.strip()]
    if any(not _SHELF_ONLY.search(r) for r in rs):
        rs = [r for r in rs if not _SHELF_ONLY.search(r)]
    return sorted(rs, key=_rank)


def short_reason(r):
    """«تصنيفه الفرعي «رواية تاريخية» يوافق طلبك» -> «رواية تاريخية كما طلبت»; «عدد صفحاته ١٧٦» -> «١٧٦ صفحة»; a price in
    Arabic digits."""
    m = re.match(r"^تصنيفه الفرعي «([^»]+)» يوافق طلبك$", r)
    if m:
        return f"«{m.group(1)}» كما طلبت"
    m = re.match(r"^سعره ([\d.]+) ريال$", r)
    if m:
        return f"سعره {arabic_digits(m.group(1))} ريال"
    return r


# CHATFIX31 (1c), Ibrahim 2026-11-01 «ابدا» (the chat review's «quick answers» step): the tap-ready answers under a reply are
# short, tied to what was just shown, never the visitor's own request again, never something our data cannot answer. The
# server pads them to five afterwards (helper_chips.pad, Ibrahim 2026-10-27: «اقل شيء ٥ اختيارات مساعدة»)
_CHIP_NOT_ANSWERABLE = re.compile(r"(?:^| )(?:ا?حجز\w*|اشتري\w*|شراء|اطلبه|اطلبها|اطلبي|توصيل|توصلونه|"
                                  r"متوفر بفرع|تاكدي من توفره|وين القاه|وين الاقيه|reserve\w*|buy|order it|delivery|deliver)(?: |$)")
# (chat.RESPOND_SYSTEM rule 9 already asks for none of these: no booking, buying or delivery in the chat, the place is on the card)
_CHIP_PRICE = re.compile(r"سعر|بكم|كم يكلف|price|cost")
_CHIP_PAGES = re.compile(r"كم صفح|صفحاته|pages")
# questions about the shown books only («روايات مترجمة»، «روايات تقييمها عالي» are new requests and stay)
_CHIP_RATING = re.compile(r"(?:^| )(?:وش|ايش|شو|كم|ما|what|how)(?: .*)? (?:تقييم\w*|rating\w*|rated)")
_CHIP_TRANSLATOR = re.compile(r"(?:^| )(?:من|مين|منو|who)(?: .*)? (?:ترجم\w*|مترجم\w*|translat\w*)|translator")


def clean_chips(chips, last_message, books, language="ar"):
    """-> at most five chips: no repeat of the visitor's message, none longer than five words, none the data cannot answer
    (a price or a page count no shown book has), and one tied to the cards first when the model gave none."""
    en = language == "en"
    last = " ".join(normalize(last_message or "").split())
    books = [b for b in books or [] if isinstance(b, dict)]
    out, seen = [], set()
    for c in chips or []:
        if not isinstance(c, str) or not c.strip():
            continue
        k = " ".join(normalize(c).split())
        if not k or k in seen or k == last or (last and ((len(k) >= 6 and k in last) or (len(last) >= 8 and last in k))):
            continue
        if len(k.split()) > 5 or _CHIP_NOT_ANSWERABLE.search(" " + " ".join(re.sub(r"[^\w\s]", " ", k).split()) + " "):
            continue
        if _CHIP_PRICE.search(k) and books and not any(b.get("price") for b in books):
            continue
        if _CHIP_PAGES.search(k) and books and not any(b.get("page_count") for b in books):
            continue
        # «وش تقييمها؟» with no rating in our data (the replay of 2026-11-01), «من مترجم الثاني؟» with no translator recorded
        if _CHIP_RATING.search(k) and books and not any(b.get("goodreads_rating") and (b.get("goodreads_ratings_count") or 0)
                                                        >= RATING_MIN_COUNT for b in books):
            continue
        if _CHIP_TRANSLATOR.search(k) and books and not any(b.get("translator") for b in books):
            continue
        seen.add(k)
        out.append(c.strip())
    tied = None
    if len(books) == 1 and not any(re.search(r"قريب|مثله|زيه|يشبه|similar|like it|like this", " ".join(normalize(c).split())) for c in out):
        # the card already shows its price, pages and place: the way on is a book like it (the same words the server uses
        # after a book named, so it is understood when tapped)
        tied = "Something similar" if en else "كتب قريبة منه"
    elif len(books) >= 2 and not any(re.search(r"اخف|اقصر|lighter|shorter|اسهل|easier", " ".join(normalize(c).split())) for c in out):
        tied = ("Which is shortest?" if en else "أي واحد أقصر؟") if sum(1 for b in books if b.get("page_count")) >= 2 else (
            "Which is lighter?" if en else "أي واحد أخف؟")
    if tied and " ".join(normalize(tied).split()) not in seen and " ".join(normalize(tied).split()) != last:
        out.insert(0, tied)
    return out[:5]


# CHATFIX31 (1b), Ibrahim 2026-11-01 «ابدا» (the chat review's «reply length» step): about 45–60 words is asked of the model
# (chat.RESPOND_SYSTEM rule 8). A reply that still runs far over is cut at sentence boundaries: the first sentence, every
# sentence that names a book (a «title», or a card's title written plainly) and the final question always stay
TRIM_OVER = 85      # words: a reply longer than this is trimmed
TRIM_TO = 70        # words: sentences are dropped until the reply is no longer than this (or none is left to drop)


def _sentences(text):
    """-> the sentences of a reply with their ending (and the space after it); never split inside «…» or “…”."""
    out, cur, depth = [], "", 0
    i, s = 0, str(text or "")
    while i < len(s):
        ch = s[i]
        cur += ch
        if ch in "«“":
            depth += 1
        elif ch in "»”" and depth:
            depth -= 1
        elif depth == 0 and (ch in ".!؟?\n" and not (ch == "." and i + 1 < len(s) and s[i + 1].isdigit())):
            while i + 1 < len(s) and s[i + 1] in ".!؟? \n\t":
                i += 1
                cur += s[i]
            out.append(cur)
            cur = ""
        i += 1
    if cur.strip():
        out.append(cur)
    return out


def _words(text):
    return len(re.findall(r"[^\s«»“”.,،؛:!؟?()\-–—]+", str(text or "")))


def trim_reply(reply, titles=(), over=TRIM_OVER, to=TRIM_TO):
    """-> (the reply, the sentences dropped). Unchanged when it is no longer than `over` words."""
    text = str(reply or "")
    if _words(text) <= over:
        return text, []
    parts = _sentences(text)
    if len(parts) <= 2:
        return text, []
    keys = [" ".join(normalize(t).split()) for t in titles or () if t and len(normalize(t).strip()) >= 4]
    def protected(n):
        p = parts[n]
        if n == 0 or "«" in p or "“" in p:
            return True
        if n == len(parts) - 1 and re.search(r"[؟?]\s*$", p):
            return True
        np = " " + " ".join(normalize(p).split()) + " "
        return any(k in np for k in keys)
    keep = list(range(len(parts)))
    dropped = []
    for n in reversed(range(len(parts))):
        if sum(_words(parts[k]) for k in keep) <= to:
            break
        if protected(n):
            continue
        keep.remove(n)
        dropped.append(parts[n].strip())
    if not dropped:
        return text, []
    out = "".join(parts[k] for k in keep).strip()
    return re.sub(r"[ \t]{2,}", " ", out), list(reversed(dropped))
