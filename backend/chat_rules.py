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
            if (val < 10 and 20 <= nx[1] < 100 and nx[1] % 10 == 0) or (100 <= val and nx[1] < 100 and val % 100 == 0):
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
        return []
    out = []
    for b in all_books.values():
        s = b.get("series_name")
        if not s:
            continue
        sw = {strip_al(w) for w in title_key(s).split()}
        if mw == sw or mw <= sw:
            out.append(b)
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
_CHEAPEST = re.compile(r"(?:^| )(?:ال)?(?:ارخص|رخيص|رخيصه|cheapest|cheaper|cheap)(?: |$)|اقل سعر|lowest price")
_PRICE_WORD = re.compile(r"ريال|سعر|اسعار|رخيص|ارخص|ميزاني|price|cheap|budget|(?:^| )sar(?: |$)")


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


_SHORTEST = re.compile(r"(?:^| )(?:ال)?اقصر(?: |$)|(?:^| )(?:shortest|shorter)(?: |$)")
_SUPERLATIVE = re.compile(r"الاقصر|اقصر (?:كتاب|روايه|قصه|شي|واحد|وحده|عمل|ديوان)|shortest")


def shortest_asked(text):
    """-> "shortest" («وش أقصر كتاب له؟»), "shorter" («أقصر» after some books), or None."""
    t = " ".join(normalize(text).split())
    if not _SHORTEST.search(t):
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


def grade_ages(text):
    """«بأولى ابتدائي» -> [6], «ثاني متوسط» -> [13], «روضة» -> [5]: the usual age of that school year."""
    t = " ".join(normalize(text).split())
    out = [_STAGE_BASE[m.group(2)] + _GRADE_N.get(m.group(1), 1) for m in _GRADE.finditer(t)]
    if not out and re.search(r"(?:^| )(?:بال|في ال|ال)?(?:روضه|تمهيدي)(?: |$)", t):
        out.append(5)
    return out


# «بنتي ١٥»، «ولدي ٩ وبنتي ٤»: an age right after the child, with no «عمرها» / «سنة» (QAD 2026-10-12, case 38:
# «كم عمر بنتك؟» was asked again)
_KID_AGE = re.compile(r"(?:^| )[وف]?ل?(?:بنتي|ابنتي|ولدي|ابني|طفلي|طفلتي|حفيدي|حفيدتي|اخوي|اختي|بنت اختي|بنت اخوي|ولد اختي"
                      r"|ولد اخوي|my daughter|my son|my kid|my child)\s*[،,:]?\s*(\d{1,2})(?!\d)"
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
_KID_NUM = re.compile(r"((?:^| )[وف]?ل?(?:بنتي|ابنتي|ولدي|ابني|طفلي|طفلتي|حفيدي|حفيدتي|my daughter|my son)\s+)(" + _NUM_ALT
                      + r")(?= |$)")


def _num_words(t):
    """Digits for an age written in words; «سنتين» alone is two years."""
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
_KID_WORD = re.compile(r"(?:^| )(?:ولدي|ابني|بنتي|ابنتي|طفلي|طفلتي|اولادي|عيالي|اطفالي|بناتي|ولدنا|بنتنا|my son|my daughter|my kid)(?: |$)")
_FOR_ME = re.compile(r"(?:^| )(?:كتاب|كتب|شي|شيء|روايه) (?:لي|لنفسي)(?: انا)?(?: |$)|(?:^| )(?:a book|something) for (?:me|myself)")


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
    return words[-1] == "لي" and len(words) >= 3 and words[-2] not in _TELL


def minor_self(user_messages):
    """The visitor is himself the young reader («عمري ١٥»، «أنا بثاني متوسط»)."""
    for text in user_messages:
        t = " ".join(normalize(text).split())
        m = re.search(r"عمري (\d{1,2})", t)
        if (m and int(m.group(1)) < 18) or _MINOR_SELF.search(t):
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
_PAGES_OVER = re.compile(r"(?:اكثر من|فوق|اكبر من|more than|over|at least|عل[يى] الاقل)\s+(\d{2,4})\s*(?:صفحه|صفحات|صفحة|ص|pages?)(?:\s|$)")
_PAGES_BARE = re.compile(r"(?:^| )(\d{2,4})\s*(?:صفحه|صفحات|صفحة|pages)(?:\s|$)")
_PAGES_ANY = re.compile(r"(?:اي|مو مهم|ما يهم) (?:عدد )?(?:ال)?صفحات|الطول (?:مو مهم|ما يهم)|any length")


def page_limit(user_messages):
    """-> (most pages, fewest pages) the visitor asked for as a number, each None when not said. The limit
    stays through the conversation until another is named or it is lifted («مو مهم عدد الصفحات»)."""
    lo = hi = None
    for text in user_messages:
        t = " ".join(num_canon(text).split())
        if _PAGES_ANY.search(t):
            lo = hi = None
        m_hi, m_lo = _PAGES_UNDER.search(t), _PAGES_OVER.search(t)
        if m_hi:
            hi = int(m_hi.group(1))
            if re.match(r"(?:اقل|تحت|less|under|fewer|below)", m_hi.group(0)):
                hi -= 1   # «أقل من ١٠٠ صفحة»: 99 at most
        if m_lo:
            lo = int(m_lo.group(1))
            if re.match(r"(?:اكثر|فوق|اكبر|more|over)", m_lo.group(0)):
                lo += 1   # «أكثر من ١٠٠ صفحة»: 101 at least; «على الأقل ١٠٠» keeps 100 (AUDFIX, audit 2026-10-16 P3)
        if not m_hi and not m_lo and _PAGES_BARE.search(t) and re.search(r"(?:^| )(?:حول|تقريبا|بحدود|around|about)(?: |$)", t):
            n = int(_PAGES_BARE.search(t).group(1))
            lo, hi = int(n * 0.8), int(n * 1.2)
    return hi, lo


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
