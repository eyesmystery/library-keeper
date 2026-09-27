"""Keyword understanding of visitor requests (the safe fallback when no AI model answers).

Produces the same preference structure as the AI (llm.clean_prefs), so both paths share
the same constraints and ranking in app.py. Design rules (improvement plan items 3 and 14):
- whole-word matching: "سر" never matches "سريع", "حب" never matches "حبيب";
- negation: "ما أبغى حزين", "بدون رعب", "مو طويل", "ما يكون ثقيل" exclude instead of include;
- Saudi/Gulf dialect: يونسني، يروقني، يفتح النفس، يشدني، ما ينترك ...;
- conversations are read message by message: a later message overrides an earlier one
  ("لا، خلها رواية" replaces the genre; "عادي طويل" lifts the length limit);
- words that are not understood are only used to look for titles/authors, never as a mood.
"""
import re

_DIACRITICS = re.compile(r"[ؗ-ًؚ-ْـ]")


def normalize(text):
    text = _DIACRITICS.sub("", str(text or ""))
    text = re.sub("[إأآٱ]", "ا", text)
    text = text.replace("ى", "ي").replace("ة", "ه")
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    text = re.sub(r"[^\w\s]", " ", text)
    return text.lower()


def strip_al(word):
    return word[2:] if word.startswith("ال") and len(word) > 3 else word


def _variants(word):
    """The word plus the forms left after removing attached prefixes (و ف ب ل ك، ال، بال، وال، لل)."""
    out = {word, strip_al(word)}
    for pre in ("وال", "بال", "فال", "كال", "لل"):
        if word.startswith(pre) and len(word) > len(pre) + 1:
            out.add(word[len(pre):])
    if (len(word) > 3 and word[0] in "وفبلك") or (len(word) == 3 and word[0] in "وف"):
        out.add(word[1:])
        out.add(strip_al(word[1:]))
    return out


def word_match(word, token):
    """Whole-word match with light Arabic morphology: exact after prefix removal; longer
    tokens (4+ letters) may also match as a stem ("حزين" ~ "حزينه"); 3-letter tokens only
    with at most 2 extra letters ("شعر" ~ "شعري" but not "شعراء"...); 1-2 letters: exact only."""
    for v in _variants(word):
        if v == token:
            return True
        if len(token) >= 4 and v.startswith(token):
            return True
        if len(token) == 3 and v.startswith(token) and len(v) - len(token) <= 2:
            return True
    return False


def find_phrase(words, phrase):
    """Index of the first word where the (normalized) phrase starts, or -1."""
    toks = normalize(phrase).split()
    n = len(toks)
    for i in range(len(words) - n + 1):
        if all(word_match(words[i + j], toks[j]) for j in range(n)):
            return i
    return -1


NEGATORS = {normalize(w) for w in "ما مو مب مش لا بدون بلا غير ماابي ماابغى مابي مابغى ماني ابعد بعيد not no without avoid nothing never dont don doesnt isnt".split()}
# words between a negator and the keyword that do not break the negation
NEG_BRIDGE = {normalize(w) for w in "ابغى ابغي ابي ابيه ابغاه يكون تكون ودي احب اب فيه فيها شي شيء كتاب روايه يصير اي نوع i want would like a an something book story to be t too anything that is it s please really".split()}


def negated(words, i):
    """True when a negator appears within the 3 words before index i, with only bridge words between.
    English filler words (EN_FREE) do not count toward the 3, so «I don't want anything sad»
    (don t want anything sad) is covered while «ما ابغى رواية، ابغى شعر» keeps شعر wanted."""
    counted = 0
    for k in range(i - 1, max(-1, i - 9), -1):
        w = words[k]
        if w in NEGATORS or (w.startswith("ما") and len(w) > 3 and w[2:] in NEG_BRIDGE):
            return True
        if w not in NEG_BRIDGE:
            return False
        if w not in EN_FREE:
            counted += 1
            if counted >= 3:
                return False
    return False


EN_FREE = {"t", "s", "want", "anything", "too", "please", "really", "that", "is", "it", "a", "an", "to", "be"}


GENRES = {  # more specific entries first: their words are consumed before "أدب" etc.
    "أدب رحلات": ["ادب رحلات", "ادب الرحلات", "رحلات"],
    "رواية": ["روايه", "روايات", "قصه", "حكايه", "novel", "novels", "fiction", "story"],
    "قصص قصيرة": ["قصص قصيره", "مجموعه قصصيه", "short stories"],
    "أطفال": ["اطفال", "للاطفال", "قصص اطفال", "قصص للاطفال", "children", "children's", "kids"],
    "يافعين": ["يافعين", "لليافعين", "مراهقين", "young adult", "teen", "teens"],
    "تطوير ذاتي": ["تطوير ذات", "تطوير الذات", "تطوير ذاتي", "تحفيز", "عادات", "تنميه بشريه", "self help", "self development"],
    "علم نفس": ["علم نفس", "نفسيه", "علم النفس", "psychology"],
    "مسرحية": ["مسرحيه", "مسرحيات", "مسرح"],
    "أدب": ["ادب", "مقالات", "خواطر"],
    "سيرة ذاتية": ["سيره ذاتيه", "مذكرات", "سيرته", "حياته"],
    "سيرة نبوية": ["سيره نبويه", "السيره النبويه", "سيره الرسول", "نبوي"],
    "تاريخ": ["تاريخ", "تاريخي", "تاريخيه", "history"],
    "دين": ["ديني", "دينيه", "اسلامي", "اسلاميه", "ايمان", "قران"],
    "فكر": ["فكر", "فكري", "فلسفه", "فلسفي", "thought", "philosophy"],
    "علوم": ["علوم", "علمي", "علميه", "فيزياء", "فضاء", "الكون", "science"],
    "أعمال": ["اعمال", "رياده", "مشاريع", "استثمار", "اقتصاد", "اداره"],
    "شعر": ["شعر", "اشعار", "ديوان", "دواوين", "قصائد", "قصيده", "poetry", "poems"],
}
MOODS = {
    "funny": ["مضحك", "يضحك", "ضحك", "كوميدي", "مرح", "خفيف دم", "يونس", "يونسني", "تونسني", "وناسه", "فله", "funny", "fun", "humorous"],
    "comforting": ["مريح", "يريح", "يريحني", "يروق", "يروقني", "رايق", "يفتح النفس", "يهدي", "دافي", "comforting", "cozy", "comfort"],
    "warm": ["دافئ", "دفء", "حميمي", "عائلي دافئ", "warm"],
    "suspenseful": ["مشوق", "تشويق", "يشد", "يشدني", "ما ينترك", "ما ينحط", "اثاره", "ما انام", "suspenseful", "thriller", "exciting"],
    "mysterious": ["غامض", "غموض", "لغز", "الغاز", "اسرار", "سر", "بوليسي", "جريمه", "mystery", "mysterious"],
    "inspiring": ["ملهم", "الهام", "تحفيزي", "يحفز", "يغير حياتي", "inspiring", "inspirational"],
    "contemplative": ["تاملي", "تامل", "عميق", "يخليني افكر", "فلسفي"],
    "romantic": ["رومانسي", "رومانسيه", "حب", "غرام", "عشق", "romantic", "romance"],
    "tense": ["متوتر", "توتر", "رعب", "مرعب", "مخيف"],
    "dark": ["سوداوي", "قاتم", "مظلم", "كئيب"],
    "sad": ["حزين", "حزن", "يبكي", "مبكي", "كئيب", "مؤلم", "يوجع", "sad", "melancholy"],
    "uplifting": ["مبهج", "متفائل", "تفاؤل", "يفرح", "سعيد", "ايجابي", "uplifting", "hopeful"],
}
PACE = {"fast": ["سريع", "سريع الاحداث", "احداث سريعه", "ما ينترك", "يشدني", "ايقاع سريع", "fast", "quick", "page turner"],
        "slow": ["هادي", "بطيء", "ايقاع هادي", "slow", "gentle"]}
WEIGHT = {"light": ["خفيف", "خفيفه", "بسيط", "سهل", "خفيف على النفس", "light", "easygoing"],
          "heavy": ["ثقيل", "ثقيله", "دسم", "مؤلم", "يوجع", "ثقيل على النفس", "heavy", "intense"]}
USE_CASES = {
    "gift": ["هديه", "اهدي", "اهديه", "gift"], "coffee": ["قهوه", "مع القهوه", "coffee"],
    "travel": ["سفر", "رحله", "طياره", "الطيران", "مطار"],
    "habit_start": ["اول مره اقرا", "ابدا القراءه", "مبتدئ", "ابدا اقرا", "اتعود على القراءه", "start reading", "reading habit"],
    "book_club": ["نادي قراءه", "نادي القراءه", "نادي"], "discussion": ["نقاش", "يفتح نقاش", "للنقاش"],
    "practical_learning": ["عملي", "تطبيقي", "اتعلم"], "quick_read": ["اخلصه في يوم", "اخلصه بسرعه", "قراءه سريعه"],
}
AVOID = {"violence": ["عنف", "دموي", "قتل", "دم"], "horror": ["رعب", "مرعب", "مخيف"],
         "sexual_content": ["مشاهد جريئه", "جريئه", "جنس", "خادش", "ايحاءات"],
         "strong_language": ["الفاظ", "شتائم", "كلام بذي"], "substance_use": ["مخدرات", "خمر", "كحول"]}
FAMILY_SAFE = ["محترم", "محترمه", "للعائله", "عائلي", "نظيف", "يناسب العائله"]
SHORT = ["قصير", "قصيره", "صغير", "اخلصه في يوم", "خفيف وقصير", "صفحات قليله", "short", "quick read"]
LONG = ["طويل", "طويله", "ملحمه", "ضخم", "long", "epic"]
LENGTH_ANY = ["عادي طويل", "عادي طويله", "ما يهم الطول", "اي طول", "الطول ما يهم", "عادي لو طويل"]
BEGINNER = ["مبتدئ", "اول مره اقرا", "اول كتاب", "ابدا القراءه", "سهل", "لغه سهله", "beginner", "easy to read"]
ADVANCED = ["متقدم", "قارئ متمرس", "لغه قويه", "advanced"]
STOP = {normalize(w) for w in (
    "ابغى ابغي ابي أبي ابيه ودي نفسي ابحث عن كتاب كتب كتابا شيء شي أي اي مع في من على إلى الى لي له لها "
    "لشخص شخص يحب تحب و او أو ما مو مب لا بدون بلا غير يكون تكون عندي عندكم عادة القراءة قراءة اقرأ اقرا ابدأ ابدا "
    "هدية عربي عربية اليوم ممكن لو سمحت الله يعافيك يعطيك العافيه وش ايش كيف ابيه ابغاه خلها خله عادي "
    "كذا شوي يعني ولا بس برضو ترى طيب اوكي تمام ياليت عشان علشان لان هو هي انا احد واحد نوع اكثر اقل جدا مره "
    "حلو زين جميل ممتع ممتعه فيه فيها يكون يصير لازم ابغاها ابيها اللي الي هذا هذي ذا تنصحني تنصح نصيحه "
    "لولدي لبنتي ولدي بنتي لطفلي لطفلتي اخوي اختي صديقي صديقتي لصديقي لصديقتي امي ابوي زوجتي زوجي "
    "السلام عليكم مرحبا هلا اهلا صباح مساء الخير النور شكرا مشكور تسلم "
    "سنه سنوات سنين عمره عمرها غيرت رايي رايي ورني وريني غيرها غيره ثاني ثانيه طفل طفله طفلي "
    "i want would like a an the book books something with for me someone reader please help me find read "
    "today last loved enjoyed fun novel coffee gift start reading show me another something lighter "
    "my by of to and or but is it that this about any some good nice love hello hi hey thanks thank you there "
    "what have do can could suggest recommend looking am im kind sort maybe").split()}

# a message made only of these words is a greeting (answered with a question, no books)
GREETING_WORDS = {normalize(w) for w in (
    "السلام عليكم سلام مرحبا هلا اهلا يا هلا صباح مساء الخير النور كيفك كيف الحال "
    "hello hi hey salam good morning evening afternoon there how are you").split()}


def is_greeting(message):
    words = normalize(message).split()
    return bool(words) and all(w in GREETING_WORDS for w in words)


def _empty():
    return {"genres": [], "length": None, "moods_include": [], "moods_exclude": [], "pace": [],
            "weight_include": [], "weight_exclude": [], "use_cases": [], "reader_level": [],
            "authors": [], "title_keywords": [], "unsupported_requests": [], "audience": None,
            "avoid_content": []}


def _parse_one(text, audience_fn=None):
    """Preferences stated in ONE message. Returns (prefs, consumed_word_indexes, flags)."""
    words = normalize(text).split()
    p = _empty()
    used = set()
    flags = {"length_any": False, "genres_negated": []}

    def hits(table):
        for key, phrases in table.items():
            first = None
            for ph in phrases:  # consume every phrase of the key, report it once
                i = find_phrase(words, ph)
                span = range(i, i + len(normalize(ph).split()))
                if i >= 0 and not all(j in used for j in span):  # a longer phrase already took these words
                    used.update(span)
                    first = i if first is None else min(first, i)
            if first is not None:
                yield key, negated(words, first)

    for g, neg in hits(GENRES):
        (flags["genres_negated"] if neg else p["genres"]).append(g)
    for m, neg in hits(MOODS):
        (p["moods_exclude"] if neg else p["moods_include"]).append(m)
    for pace, neg in hits(PACE):
        if not neg:
            p["pace"].append(pace)
    for w, neg in hits(WEIGHT):
        if neg:
            p["weight_exclude"].append(w)
            if w == "heavy":
                p["weight_include"].append("light")
        else:
            p["weight_include"].append(w)
    for u, neg in hits(USE_CASES):
        if not neg:
            p["use_cases"].append(u)
    for a, neg in hits(AVOID):
        if neg:
            p["avoid_content"].append(a)
    if any(find_phrase(words, ph) >= 0 for ph in FAMILY_SAFE):
        p["avoid_content"] += ["sexual_content", "strong_language"]
        used.update(i for i, w in enumerate(words) if any(word_match(w, normalize(ph)) for ph in FAMILY_SAFE))
    if any(find_phrase(words, ph) >= 0 for ph in LENGTH_ANY):
        flags["length_any"] = True
        for ph in LENGTH_ANY:
            i = find_phrase(words, ph)
            if i >= 0:
                used.update(range(i, i + len(normalize(ph).split())))
    else:
        for ph in SHORT:
            i = find_phrase(words, ph)
            if i >= 0:
                used.add(i)
                p["length"] = "long" if negated(words, i) else "short"
                break
        if p["length"] is None:
            for ph in LONG:
                i = find_phrase(words, ph)
                if i >= 0:
                    used.add(i)
                    p["length"] = "short" if negated(words, i) else "long"
                    break
    if any(find_phrase(words, ph) >= 0 for ph in BEGINNER):
        p["reader_level"].append("beginner")
    if any(find_phrase(words, ph) >= 0 for ph in ADVANCED):
        p["reader_level"].append("advanced")
    # "بدون رعب": horror is both a content warning and a tense mood
    if "tense" in p["moods_exclude"] and "horror" not in p["avoid_content"]:
        p["avoid_content"].append("horror")
    if audience_fn:
        p["audience"] = audience_fn(normalize(text))
    # words not understood may name a title or an author; they never become a mood
    p["title_keywords"] = [w for i, w in enumerate(words)
                           if i not in used and len(w) > 1 and w not in STOP and w not in NEGATORS
                           and not w.isdigit()]
    for k in p:
        if isinstance(p[k], list):
            p[k] = list(dict.fromkeys(p[k]))
    return p, flags


def understand(messages, audience_fn=None):
    """Merge the preferences of the visitor's messages in order (later ones win)."""
    merged = _empty()
    for text in messages:
        p, flags = _parse_one(text, audience_fn)
        if p["genres"]:
            merged["genres"] = p["genres"]  # "لا، خلها رواية" replaces the earlier genre
        merged["genres"] = [g for g in merged["genres"] if g not in flags["genres_negated"]]
        if flags["length_any"]:
            merged["length"] = None
        elif p["length"]:
            merged["length"] = p["length"]
        for inc, exc in (("moods_include", "moods_exclude"), ("weight_include", "weight_exclude")):
            merged[exc] = [x for x in merged[exc] if x not in p[inc]] + [x for x in p[exc] if x not in merged[exc]]
            merged[inc] = [x for x in merged[inc] if x not in p[exc]] + [x for x in p[inc] if x not in merged[inc]]
        for k in ("pace", "use_cases", "reader_level", "avoid_content", "title_keywords"):
            merged[k] += [x for x in p[k] if x not in merged[k]]
        if p["audience"]:
            merged["audience"] = p["audience"]
    merged["moods_include"] = [m for m in merged["moods_include"] if m not in merged["moods_exclude"]]
    merged["weight_include"] = [w for w in merged["weight_include"] if w not in merged["weight_exclude"]]
    return merged


def understood_anything(prefs):
    keys = ("genres", "moods_include", "moods_exclude", "pace", "weight_include", "weight_exclude",
            "use_cases", "reader_level", "avoid_content")
    return bool(prefs.get("length") or prefs.get("audience") or any(prefs.get(k) for k in keys))
