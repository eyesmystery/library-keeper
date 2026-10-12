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


def part_asked(text):
    """«والجزء الثاني؟» -> 2, «وش الجزء اللي بعده؟» -> "next", else None."""
    t = " ".join(normalize(text).split())
    for m in _PART.finditer(t):
        w = m.group(1)
        if w.isdigit() and 1 <= int(w) <= 30:
            return int(w)
        if w in _ORD:
            return _ORD[w]
    return "next" if _NEXT.search(t) else None


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
_PRICE = re.compile(r"(\d{1,4})\s*(?:ريال|ريالات|ر س|sar|sr|riyal|riyals)(?:\s|$)")
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


def kid_ages(text):
    t = " ".join(normalize(text).split())
    return [int(m.group(1)) for m in _KID_AGE.finditer(t)]


def ages_in(text):
    t = " ".join(normalize(text).split())
    found = {(m.start(1) if m.group(1) else m.start(2)): int(m.group(1) or m.group(2)) for m in _AGE.finditer(t)}
    for m in _KID_AGE.finditer(t):
        found.setdefault(m.start(1), int(m.group(1)))
    return [found[k] for k in sorted(found)] + grade_ages(text)


# «لي أنا كأم»، «كمعلمة»، «للأهل»: the reader is the grown-up asking, even when the subject is teenagers or
# children («كتب عن الصحة النفسية للمراهقين، لي أنا كأم» got teen novels: QAD 2026-10-12, case 38)
_PARENT = re.compile(r"(?:^| )(?:انا |لي )?(?:كام|كاب|كوالد|كوالده|كمعلم|كمعلمه|كمربي|كمربيه|كاخصائي|كاخصائيه|كولي امر|كولية امر)(?: |$)"
                     r"|(?:^| )(?:للوالدين|للاهل|لاولياء الامور|لولي الامر|للامهات|للاباء|للمربين|للمعلمين)(?: |$)"
                     r"|as a (?:parent|mom|mother|dad|father|teacher)|for parents|for teachers")


def parent_said(text):
    return bool(_PARENT.search(" ".join(normalize(text).split())))


# «وش التصنيفات اللي عندكم؟»، «كم كتاب عندكم؟»، «وش أكثر نوع عندكم؟»: a question about the library itself
_OVERVIEW = re.compile(r"(?:^| )(?:ال)?(?:تصنيفات|اقسام|فئات)(?: |$)|(?:^| )(?:انواع|اصناف) (?:ال)?كتب"
                       r"|كم (?:كتاب|عنوان|نسخه|عدد (?:ال)?كتب)(?P<rest>(?: \S+)*)|عدد (?:ال)?(?:كتب|عناوين)(?P<rest2>(?: \S+)*)"
                       r"|اكثر (?:نوع|تصنيف|قسم|صنف)|(?:نوع|تصنيف|قسم) (?:عندكم )?(?:اكثر|الاكثر)"
                       r"|what (?:categories|genres|sections)|which (?:categories|genres)|how many books|most common (?:genre|category)")


_OVERVIEW_TAIL = {normalize(w) for w in "عندكم عندك عندكم؟ بالمكتبه المكتبه في موجود موجوده متوفر متوفره كلها تقريبا "
                  "اجمالا بالمحل المحل الحين حاليا الكتب المتوفره you have do in the store library total".split()}


def overview_asked(text):
    """Only the library as a whole: «كم كتاب عندكم لنجيب محفوظ؟» is a question about an author."""
    t = " ".join(normalize(re.sub(r"[؟?!.،,]", " ", text)).split())
    m = _OVERVIEW.search(t)
    if not m:
        return False
    rest = (m.group("rest") or m.group("rest2") or "").split()
    return all(w in _OVERVIEW_TAIL for w in rest)


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
    "in arabic english translated original version edition please only just by an a author writer").split()}


def refine_only(text):
    """The message only adds a language / translation / author origin («أبيها بالعربي»، «عربية»)."""
    words = normalize(re.sub(r"[؟?!.،,]", " ", text)).split()
    return bool(words) and len(words) <= 6 and all(w in _REFINE for w in words)
