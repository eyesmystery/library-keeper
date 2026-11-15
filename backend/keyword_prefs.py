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


def negated(words, i, wide=False):
    """True when a negator appears within the 3 words before index i, with only bridge words between.
    English filler words (EN_FREE) do not count toward the 3, so «I don't want anything sad»
    (don t want anything sad) is covered while «ما ابغى رواية، ابغى شعر» keeps شعر wanted.
    wide (QADKWFIX, the reader of the model-free answer): also «ما تبي / ما يبي / بلاش / ماله خص» and «بلا مشاهد …»."""
    negs, bridge = (NEGATORS | NEGATORS_WIDE, NEG_BRIDGE | NEG_BRIDGE_WIDE) if wide else (NEGATORS, NEG_BRIDGE)
    counted = 0
    for k in range(i - 1, max(-1, i - 9), -1):
        w = words[k]
        if (w in negs or (w.startswith("ما") and len(w) > 3 and w[2:] in bridge)
                or (len(w) > 2 and w[0] in "وف" and w[1:] in negs)):   # «ولا أبغى شي رومانسي»
            return True
        if w not in bridge:
            return False
        if w not in EN_FREE:
            counted += 1
            if counted >= 3:
                return False
    return False


EN_FREE = {"t", "s", "want", "anything", "too", "please", "really", "that", "is", "it", "a", "an", "to", "be"}

# ---------------------------------------------------------------------------
# QADKWFIX (Ibrahim 2026-11-14 on Q-QADKW-1, item 3): the wider reading used by the model-free answer only
# (understand(..., wide=True) — app.fallback_understand). The words come from comparing the model's readings of 3,228 past QAD
# turns with this reader (research/fallback_study_2026-11-13/mine_out.txt). The model path keeps the reading above unchanged.
# ---------------------------------------------------------------------------
NEGATORS_WIDE = {normalize(w) for w in "بلاش ماتبي ماتبغى ماتبغي مايبي مايبغى ماتحب مايحب ماله مالها مابيها ماابيها ماعندي".split()}
NEG_BRIDGE_WIDE = {normalize(w) for w in (
    "تبي تبغى تبغي تبغا يبي يبغى يبغي يبغا نبي نبغى تحب يحب تحبين نحب ابد ابدا ابيها ابغاها ابيه مشاهد مشهد كثير وايد مره خص "
    "any scenes much wants like more").split()}
SUBGENRES = {  # label -> phrases; the label is the visitor's kind of story, matched like the model's subgenres
    "خيال علمي": ["خيال علمي", "الخيال العلمي", "science fiction", "sci fi", "scifi", "سفر عبر الزمن", "time travel", "space opera",
                  "اوبرا الفضاء"],
    "واقعية سحرية": ["واقعيه سحريه", "الواقعيه السحريه", "magical realism"],
    "ديستوبيا": ["ديستوبيا", "ديستوبي", "ديستوبيه", "dystopia", "dystopian", "ما بعد نهايه العالم", "post apocalyptic"],
    "فانتازيا": ["فانتازيا", "فنتازيا", "فانتزي", "عالم سحري", "ممالك", "تنانين", "fantasy", "dragons"],
    "رعب": ["رعب", "الرعب", "مرعب", "مرعبه", "مخيف", "مخيفه", "تخوف", "يخوف", "اشباح", "زومبي", "horror", "scary", "creepy", "spooky",
            "haunted", "ghost", "ghosts"],
    "بوليسية": ["بوليسي", "بوليسيه", "تحقيق", "التحقيق", "محقق", "محققين", "detective", "detectives", "whodunit"],
    "جريمة": ["جريمه", "جرائم", "crime", "murder mystery"],
    "غموض": ["غموض", "غامض", "غامضه", "لغز", "الغاز", "mystery", "mysteries"],
    "جاسوسية": ["جاسوسيه", "تجسس", "جواسيس", "spy", "espionage"],
    "مغامرات": ["مغامره", "مغامرات", "adventure", "adventures"],
    "رواية ساخرة": ["ساخر", "ساخره", "سخريه", "satire", "satirical"],
    "شعر نبطي": ["نبطي", "نبطيه", "شعر شعبي"],
}
# kinds the compare (scripts/kw_score.py) treats as one: «بوليسية» = «جريمة» = «غموض» = «تحقيق»
SUBGENRE_FAMILY = {"خيال علمي": "scifi", "واقعية سحرية": "magic_realism", "ديستوبيا": "dystopia", "فانتازيا": "fantasy",
                   "رعب": "horror", "بوليسية": "mystery", "جريمة": "mystery", "غموض": "mystery", "جاسوسية": "spy",
                   "مغامرات": "adventure", "رومانسية": "romance", "رواية ساخرة": "satire", "شعر نبطي": "nabati",
                   "تاريخية": "historical"}
# «تشويق / إثارة» stays a mood (suspenseful) in the reader; the model's label is still counted in its family
_FAMILY_EXTRA = {"fantasy": "سحر سحريه خيالي خياليه", "horror": "خوارق جن", "mystery": "الغاز", "adventure": "مطاردات مطارده اكشن",
                 "thriller": "تشويق اثاره thriller", "satire": "كوميديا كوميدي ساخر", "romance": "رومانسيه رومانسي romance"}
# kinds of story that are fiction: with no form named, the reading is a novel (for a child: a children's book)
FICTION_KINDS = {"خيال علمي", "واقعية سحرية", "ديستوبيا", "فانتازيا", "رعب", "بوليسية", "جريمة", "غموض", "جاسوسية", "مغامرات",
                 "رواية ساخرة", "تاريخية"}
MOODS_WIDE = {
    "funny": ["اضحك من قلب", "يضحكني من قلب", "اضحك من كل قلبي", "اضحك", "يضحكني", "تضحكني", "يضحكنا", "فكاهي", "فكاهيه", "فكاهه",
              "كوميديا", "يفك خلقي", "يفك الخلق", "خفيف دمه", "دمه خفيف", "يونس", "hilarious", "laugh", "comedy"],
    "comforting": ["يواسيني", "يواسي", "تواسيني", "يطبطب", "يريح البال", "يريح بالي", "يطلعني من الجو", "heartwarming"],
    "uplifting": ["يرفع المعنويات", "يرفع معنوياتي", "يرفع معنويات", "يفرحني", "feel good"],
    "suspenseful": ["تشدني", "يشده", "تشده", "حماس", "حماسي", "حماسيه", "gripping"],
    "sad": ["يبكيني", "تقطع القلب"],
}
# in the wider reading these words are a kind of story (above), not a mood
MOOD_WORDS_AS_KIND = {"mysterious": {"غامض", "غموض", "لغز", "الغاز", "بوليسي", "جريمه", "mystery"},
                      "tense": {"رعب", "مرعب", "مخيف"}}
WEIGHT_WIDE = {"light": ["اخف", "اخف شوي", "lighter", "light read"]}
USE_CASES_WIDE = {
    "light_reading": ["خفيف", "خفيفه", "قراءه خفيفه", "light read", "something light"],
    "quick_read": ["بجلسه وحده", "بجلسه", "تنقري بجلسه", "اخلصه", "اخلصها", "اخلصه الليله", "ساعه او ساعتين", "one sitting"],
    "habit_start": ["كبدايه", "مدخل", "لاول مره", "first time"],
    "practical_learning": ["يعلمني", "خطوه بخطوه", "اتعامل", "افهم منه", "how to"],
}
SHORT_WIDE = ["اقصر", "ما يطول", "ما تطول", "بجلسه وحده", "تنقري بجلسه", "ساعه او ساعتين", "shorter", "novella", "نوفيلا"]
LONG_WIDE = ["اطول", "longer"]
AVOID_WIDE = {"violence": ["دمويه", "مشاهد دمويه", "جرائم قتل", "gore", "violence", "violent", "blood", "bloody"],
              "sexual_content": ["للكبار", "مشاهد للكبار", "explicit", "sex", "sexual"],
              "horror": ["horror", "scary"], "political": ["سياسه", "سياسي", "سياسيه", "politics", "political"],
              "strong_language": ["swearing", "profanity"]}
GENRES_WIDE = {"أطفال": ["picture book", "picture books", "قصص للصغار"], "سيرة ذاتية": ["biography", "memoir", "memoirs", "autobiography"],
               "علوم": ["popular science"], "تطوير ذاتي": ["self-help", "productivity", "انتاجيه"]}
AWARD_WORDS_WIDE = {"any": {normalize(w) for w in "winners winner winning won prize prizes award awards awarded shortlisted longlisted".split()},
                    "pulitzer": {"pulitzer", "بوليتزر", "بولتزر"}, "booker_any": {"booker"}, "zayed": {"زايد"},
                    "goncourt": {"goncourt"}, "nobel": {"nobel"}}
AWARD_PHRASES_WIDE = {"intl_booker": ["international booker", "البوكر العالميه", "البوكر الدوليه", "بوكر العالميه", "بوكر الدوليه"],
                      "mahfouz": ["ميداليه نجيب محفوظ", "جائزه نجيب محفوظ", "ميداليه محفوظ", "جائزه محفوظ", "mahfouz medal"],
                      "ipaf": ["البوكر العربيه", "بوكر العربيه", "arabic booker"]}
SCREEN_PHRASES_WIDE = {"film": ["made into a movie", "made into a film", "movie", "movies", "film", "films", "big screen"],
                       "series": ["tv series", "tv show", "netflix", "into a series", "made into a series"]}
# words that are never a title or an author word on their own in the model-free answer (QADKW report §4: «وأنا» matched
# «أنا …» picture books, «بعد» and «أيام» any title holding them); a title is still found by its other words
TITLE_COMMON = {normalize(w) for w in (
    "انا انت انتي هو هي احنا نحن هم ايام يوم سنه بعد قبل كل كثير زمان اول ثاني اخر جديد قديم واحد وحده عن الي على مع "
    "اللي هذا هذي ذا ذي هذول ابد ابدا وايد صدق كم صفحه صفحات سعر سعرها سعره متي مين وش ايش ليش وين طلعت صدرت نزلت انكتبت "
    "كتبت صارت انعملت تقريبا النسخه نسخه طبعه جدتها جدته جدي جدتي امها امه ابوها ابوه حفيدتي حفيدي اخته اخوه بنت ولد "
    "عمرها عمره ودها وده تحب يحب تموت يموت تبي يبي تبغى يبغى ابيها ابغاها شي شيء الحين توه زين طيب اوكي معي معك بس "
    "نفسيتي زفت بغيت يخليني تخليني يومي اسمها اسمه اسم عنوانها عنوانه بعنوان موجوده موجود متوفره متوفر "
    "want looking need some like just really very much more less one two pls plz was were anything everything").split()}
_FICTION_WORDS = {normalize(w) for w in "روايه روايات قصه قصص حكايه حكايات novel novels fiction story stories".split()}
_STORY_WORDS = {normalize(w) for w in "قصه قصص حكايه حكايات story stories".split()}
_HISTORICAL = {normalize(w) for w in "تاريخيه تاريخي historical".split()}
_FILM_WORDS = {normalize(w) for w in "فيلم فلم افلام مسلسل مسلسلات film movie".split()}
_SMALL = {normalize(w) for w in "صغير صغيره الصغير الصغيره".split()}
_SMALL_BOOK = {normalize(w) for w in "كتاب كتيب روايه قصه شي شيء ديوان مجلد book novel".split()}
_PROTECT_AFTER = {normalize(w) for w in "خط الخط بخط".split()}   # «الخط الديواني» is calligraphy, not «ديوان» (poetry)
_COMPARATIVE = {normalize(w) for w in "اقصر الاقصر اطول الاطول اقصرهم اقصرها اقصرهن اطولهم اطولها shorter shortest longer longest".split()}
# «بالإنجليزي»، «نسخة عربية»، «ترجمة عربية»، "in English": the language of the copy, not of the work (the model reads them so)
_COPY_BEFORE = {normalize(w) for w in "نسخه نسخته نسختها النسخه بنسخه طبعه الطبعه ترجمه الترجمه بترجمه copy edition version translation".split()}
_AR_WORK_BEFORE = {normalize(w) for w in (
    "روايه روايات قصه قصص ادب الادب شعر الشعر كاتب كاتبه كتاب كاتبات مؤلف مؤلفه مولف اديب اديبه ديوان دواوين مسرحيه سيره "
    "novel novels literature fiction poetry author writer").split()}
_EN_WORK_WORDS = {"novel", "novels", "literature", "fiction", "poetry", "poems", "author", "authors", "writer", "writers",
                  "classic", "classics", "writing", "novelist", "novelists", "poet", "poets"}
_PAGE_MAX_WIDE = re.compile(r"(?:اقل من|تحت|ما يتجاوز|ما تتجاوز|لا يتجاوز|لا تتجاوز|ما يزيد|لا يزيد|ما تزيد|لا تزيد|ما يتعدي|"
                            r"under|less than|fewer than|below)\s+(\d{2,4})\s*(?:صفحه|صفحات|ص|pages?)")


def subgenre_families(label):
    """The families a subgenre label belongs to (the model's «رواية بوليسية» -> {"mystery"}); for scripts/kw_score.py."""
    words = normalize(label).split()
    out = set()
    for lab, phrases in SUBGENRES.items():
        if any(find_phrase(words, ph) >= 0 for ph in phrases + [lab]):
            out.add(SUBGENRE_FAMILY[lab])
    for fam, extra in _FAMILY_EXTRA.items():
        if any(w in extra.split() or strip_al(w) in extra.split() for w in words):
            out.add(fam)
    if not out and any(strip_al(w) in ("تاريخيه", "تاريخي") for w in words):
        out.add("historical")
    return out


GENRES = {  # more specific entries first: their words are consumed before "أدب" etc.
    "أدب رحلات": ["ادب رحلات", "ادب الرحلات", "رحلات"],
    "قصص مصورة": ["قصص مصوره", "روايه مصوره", "روايات مصوره", "كوميكس", "مانغا", "مانجا", "comics", "comic", "manga",
                  "graphic novel", "graphic novels"],
    "مقالات ونصوص": ["مقالات", "مقاله", "نصوص نثريه", "رسائل", "essays"],
    "نقد أدبي": ["نقد ادبي", "النقد الادبي", "دراسات ادبيه", "literary criticism"],
    "فنون وسينما": ["سينما", "السينما", "فنون", "الفنون", "موسيقي", "الموسيقي", "cinema"],
    "رواية": ["روايه", "روايات", "قصه", "حكايه", "novel", "novels", "fiction", "story"],
    "قصص قصيرة": ["قصص قصيره", "مجموعه قصصيه", "short stories"],
    "أطفال": ["اطفال", "للاطفال", "قصص اطفال", "قصص للاطفال", "children", "children's", "kids"],
    "يافعين": ["يافعين", "لليافعين", "مراهقين", "young adult", "teen", "teens"],
    "تطوير ذاتي": ["تطوير ذات", "تطوير الذات", "تطوير ذاتي", "تحفيز", "عادات", "تنميه بشريه", "self help", "self development"],
    "علم اجتماع": ["علم اجتماع", "علم الاجتماع", "sociology"],
    "علم نفس": ["علم نفس", "نفسيه", "علم النفس", "psychology"],
    "مسرحية": ["مسرحيه", "مسرحيات", "مسرح"],
    "أدب": ["ادب", "خواطر"],
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


# Adjective forms only: a country name alone ("عن اليابان") is a topic, not a language.
ORIGIN_LANGUAGES = {
    "it": ["ايطالي", "ايطاليه", "الايطاليه", "الايطالي", "italian"], "ru": ["روسي", "روسيه", "الروسيه", "الروسي", "russian"],
    "fr": ["فرنسي", "فرنسيه", "الفرنسيه", "الفرنسي", "french"], "ja": ["ياباني", "يابانيه", "اليابانيه", "الياباني", "japanese"],
    "es": ["اسباني", "اسبانيه", "الاسبانيه", "الاسباني", "spanish"], "de": ["الماني", "المانيه", "الالمانيه", "الالماني", "german"],
    "en": ["انجليزي", "انجليزيه", "الانجليزيه", "الانجليزي", "english"], "tr": ["تركي", "تركيه", "التركيه", "التركي", "turkish"],
    "pt": ["برتغالي", "برتغاليه", "البرتغاليه", "البرتغالي", "portuguese"], "zh": ["صيني", "صينيه", "الصينيه", "الصيني", "chinese"],
    "ko": ["كوري", "كوريه", "الكوريه", "الكوري", "korean"], "fa": ["فارسي", "فارسيه", "الفارسيه", "الفارسي", "persian"],
}
# «رواية عربية» = written in Arabic (audit 2026-10-05). Exact words only: «بالعربي» / «للعربية» speak of the
# language of the copy, and so do the words after «باللغة» or "in".
ARABIC_ORIGIN = {"عربي", "عربيه", "العربي", "العربيه", "arabic"}
EDITION_BEFORE = {"باللغه", "اللغه", "بلغه", "للغه", "لغه", "in", "into", "to"}
TRANSLATED = ["مترجم", "مترجمه", "المترجم", "المترجمه", "translated"]
# a language adjective that is also a given name: a name when the next word is «بن» / «آل» or a family name («الحمد»)
NAME_LIKE_GIVEN = {"تركي"}
NAME_NEXT = {normalize(w) for w in "بن ابن آل ال".split()}
LANGUAGE_NEXT = {normalize(w) for w in "المعاصر الحديث القديم الكلاسيكي المترجم الاصل الاصلي الجميل الشهير".split()}


AWARD_WORDS = {"any": {normalize(w) for w in "جائزه جايزه جوائز جائزة حائزه حايزه فائزه فايزه".split()},
               "nobel": {"نوبل"}, "booker_any": {"بوكر"}, "katara": {"كتارا"}, "pulitzer": {"بوليتزر"},
               "goncourt": {"غونكور"}, "hugo": {normalize(w) for w in "هوغو هيوغو هوجو هيوجو hugo".split()},
               "nebula": {normalize(w) for w in "نيبولا نيبيولا nebula".split()}, "locus": {normalize(w) for w in "لوكس لوكوس locus".split()}}
SCREEN_WORDS = {"film": {normalize(w) for w in "فيلم فلم افلام فلما".split()},
                "series": {normalize(w) for w in "مسلسل مسلسلات".split()},
                # «كتب صارت انمي او رسوم متحركة» (QAD 2026-10-14, case 27)
                "animated": {normalize(w) for w in "انمي أنمي انيمي انمى كرتون كارتون انيميشن أنيميشن anime cartoon cartoons animated animation".split()}}
ANIMATED_PHRASES = [normalize(p) for p in ("رسوم متحركة", "رسوم متحركه", "افلام كرتون", "فلم كرتون", "فيلم كرتون")]


# «فيلم سينما قديم، أبيض وأسود» (QAD 2026-10-15, case 34): an old adaptation, made by 1975
OLD_SCREEN_YEAR = 1975
_BW = [normalize(p) for p in ("ابيض واسود", "أبيض وأسود", "الابيض والاسود", "الأبيض والأسود", "ابيض و اسود", "black and white", "b&w")]
_OLD_WORDS = {normalize(w) for w in "قديم قديمه القديم القديمه زمان كلاسيكي كلاسيكيه الكلاسيكي الكلاسيكيه old classic".split()}


def old_screen_said(normalized_text):
    """«أبيض وأسود» anywhere, or «قديم / زمان / كلاسيكي» in the same message as a film / series word."""
    words = normalize(normalized_text).split()
    if any(find_phrase(words, ph) >= 0 for ph in _BW):
        return True
    screen = any(_variants(w) & (SCREEN_WORDS["film"] | SCREEN_WORDS["series"]) for w in words)
    return screen and any(w in _OLD_WORDS for w in words)


def old_film_said(normalized_text):
    """QADFIX7 #9 (QAD 2026-10-19, case 29): «أبيض وأسود», or «قديم / زمان» with a film word — not with «مسلسل» alone."""
    words = normalize(normalized_text).split()
    if any(find_phrase(words, ph) >= 0 for ph in _BW):
        return True
    film = any(_variants(w) & SCREEN_WORDS["film"] for w in words)
    return film and any(w in _OLD_WORDS for w in words)


def animated_said(normalized_text):
    """«صارت انمي»، «رسوم متحركة»، «كرتون»: an animated adaptation, not a live-action film."""
    words = normalize(normalized_text).split()
    return (any(_variants(w) & SCREEN_WORDS["animated"] for w in words)
            or any(find_phrase(words, ph) >= 0 for ph in ANIMATED_PHRASES))


def _empty():
    return {"genres": [], "length": None, "moods_include": [], "moods_exclude": [], "pace": [],
            "weight_include": [], "weight_exclude": [], "use_cases": [], "reader_level": [],
            "authors": [], "title_keywords": [], "unsupported_requests": [], "audience": None,
            "avoid_content": [], "original_languages": [], "translated": None,
            "awards": [], "award_year": None, "adaptation": None}


def _merge_tables(base, extra, drop=()):
    out = {k: [ph for ph in v if normalize(ph) not in drop] for k, v in base.items()}
    for k, v in extra.items():
        out.setdefault(k, [])
        out[k] += [ph for ph in v if ph not in out[k]]
    return out


_WIDE = {}


def _wide_tables():
    """The reader's tables with the QADKWFIX words added (built once)."""
    if not _WIDE:
        kind_words = set().union(*MOOD_WORDS_AS_KIND.values())
        _WIDE.update(genres=_merge_tables(GENRES, GENRES_WIDE), moods=_merge_tables(MOODS, MOODS_WIDE, drop=kind_words),
                     weight=_merge_tables(WEIGHT, WEIGHT_WIDE), use_cases=_merge_tables(USE_CASES, USE_CASES_WIDE),
                     avoid=_merge_tables(AVOID, AVOID_WIDE), short=SHORT + SHORT_WIDE, long=LONG + LONG_WIDE,
                     awards={k: set(AWARD_WORDS.get(k, set())) | set(AWARD_WORDS_WIDE.get(k, set()))
                             for k in list(AWARD_WORDS) + [k for k in AWARD_WORDS_WIDE if k not in AWARD_WORDS]})
    return _WIDE


def _parse_one(text, audience_fn=None, wide=False):
    """Preferences stated in ONE message. Returns (prefs, consumed_word_indexes, flags).
    wide (QADKWFIX): the reading of the model-free answer — the subgenre field, more negations and words (see above)."""
    words = normalize(text).split()
    p = _empty()
    used = set()
    flags = {"length_any": False, "genres_negated": []}
    W = _wide_tables() if wide else None
    if wide:
        p["subgenres"], p["subgenres_exclude"] = [], []

    def neg_at(i):
        return negated(words, i, wide)

    protect = {i for i in range(1, len(words)) if words[i - 1] in _PROTECT_AFTER} if wide else set()

    def hits(table):
        for key, phrases in table.items():
            first, at = None, []
            for ph in phrases:  # consume every phrase of the key, report it once
                i = find_phrase(words, ph)
                span = range(i, i + len(normalize(ph).split()))
                if i >= 0 and protect & set(span):
                    continue   # «الخط الديواني»
                if i >= 0 and not all(j in used for j in span):  # a longer phrase already took these words
                    used.update(span)
                    first = i if first is None else min(first, i)
                    at.append(i)
            if first is not None:
                # wide: «… جرائم قتل ولا دم» — refused when any of its words is negated
                yield key, (any(neg_at(i) for i in at) if wide else neg_at(first))

    if wide:
        # «رواية تاريخية»: a kind of novel, not the history shelf (the model reads genres «رواية», subgenre «تاريخية»)
        hist = [i for i, w in enumerate(words) if _variants(w) & _HISTORICAL]
        if hist and any(_variants(w) & _FICTION_WORDS for w in words):
            used.update(hist)
            (p["subgenres_exclude"] if neg_at(hist[0]) else p["subgenres"]).append("تاريخية")
        for s, neg in hits(SUBGENRES):
            (p["subgenres_exclude"] if neg else p["subgenres"]).append(s)
            if neg and s == "رعب":
                p["avoid_content"].append("horror")   # «بدون رعب»: a content warning too, as before
    for g, neg in hits(W["genres"] if wide else GENRES):
        (flags["genres_negated"] if neg else p["genres"]).append(g)
    genre_used = set(used)   # «قصص قصيرة»: words of a shelf, never «short» below
    for m, neg in hits(W["moods"] if wide else MOODS):
        (p["moods_exclude"] if neg else p["moods_include"]).append(m)
    for pace, neg in hits(PACE):
        if not neg:
            p["pace"].append(pace)
    for w, neg in hits(W["weight"] if wide else WEIGHT):
        if neg:
            p["weight_exclude"].append(w)
            if w == "heavy":
                p["weight_include"].append("light")
        else:
            p["weight_include"].append(w)
    for u, neg in hits(W["use_cases"] if wide else USE_CASES):
        if not neg:
            p["use_cases"].append(u)
    for a, neg in hits(W["avoid"] if wide else AVOID):
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
        among = False
        if wide:
            import chat_rules   # late: chat_rules imports this module
            # «أيهم أقصر؟»، "which one is the shortest": an order among the books shown, not a wish for a short book
            among = chat_rules.among_shown_said(text)

        def _skip(i):   # «قصص قصيرة» is the short-stories shelf (its words are taken); «أيهم أقصر» an order
            if not wide:
                return False
            if words[i] in _SMALL and not (i > 0 and _variants(words[i - 1]) & _SMALL_BOOK):
                return True   # «أخوي الصغير»، «مشروع صغير»، «مو صغير (عمره ١٢)»: small is a book's length only after «كتاب / شي …»
            return i in genre_used or bool(among and _variants(words[i]) & _COMPARATIVE)
        for ph in (W["short"] if wide else SHORT):
            i = find_phrase(words, ph)
            if i >= 0 and _skip(i):
                continue
            if i >= 0:
                used.add(i)
                p["length"] = "long" if neg_at(i) else "short"
                break
        if p["length"] is None:
            for ph in (W["long"] if wide else LONG):
                i = find_phrase(words, ph)
                if i >= 0 and _skip(i):
                    continue
                if i >= 0:
                    used.add(i)
                    p["length"] = "short" if neg_at(i) else "long"
                    break
        if wide and p["length"] is None:
            # «لا يتجاوز مئتي صفحة»، "under 200 pages": a short book (the page limit itself is applied by the app)
            import chat_rules   # late: chat_rules imports this module
            m = _PAGE_MAX_WIDE.search(" ".join(chat_rules.num_canon(normalize(text)).split()))
            if m and int(m.group(1)) <= 300:
                p["length"] = "short"
    # literature of a language ("رواية إيطالية", "أدب روسي") and translated / not translated
    for i, w in enumerate(words):  # exact forms only: "ايطاليا" (a country) must not match "ايطالي"
        _vs = _variants(w)
        if wide and w.startswith("ف") and len(w) > 4:
            _vs = _vs - {w[1:], strip_al(w[1:])}   # «فروسية» (horsemanship) is not «و + روسية» (QADKWFIX, 27.1)
        code = next((c for c, forms in ORIGIN_LANGUAGES.items() if _vs & set(forms)), None)
        if code is None and w in ARABIC_ORIGIN:
            code = "ar"
        if code and _variants(w) & NAME_LIKE_GIVEN and i + 1 < len(words) and (
                words[i + 1] in NAME_NEXT or (words[i + 1].startswith("ال") and words[i + 1] not in LANGUAGE_NEXT)):
            # «تركي» is also a given name: «هل تركي بن فهد آل سعود شاعر؟»، «كتب تركي الحمد» ask about a person,
            # not Turkish literature (inbox 2026-10-11 §3.8; audit 2026-10-12 #8). «أدب تركي»، «رواية تركية» still count.
            continue
        if code and i > 0 and words[i - 1] in EDITION_BEFORE:
            used.add(i)  # the language of the copy, not of the work
            continue
        if code and wide and ((w.startswith("بال") and code != "ar") or w in ("بالعربي", "بالعربيه")
                              or (i > 0 and _variants(words[i - 1]) & _COPY_BEFORE)
                              or re.search(r"(?:^| )(?:in|into) " + re.escape(w) + r"(?: |$)", " ".join(words))
                              # "english or arabic both fine", "english copy": in English words, the copy's language unless a
                              # kind of writing follows ("an English novel", "Russian literature")
                              or (w.isascii() and not (i + 1 < len(words) and words[i + 1] in _EN_WORK_WORDS)
                                  and not (i > 0 and words[i - 1] in ("original", "originally")))):
            used.add(i)  # «بالإنجليزي»، «نسخة عربية»، "In English please": the copy's language (QADKWFIX)
            continue
        if code == "ar" and wide and not any(_variants(x) & _AR_WORK_BEFORE for x in words[max(0, i - 3):i]):
            continue   # «الخيل العربية»، «اللغة العربية»: Arabic is a work's language only beside «رواية / أدب / كاتب …»
        if code and not neg_at(i):
            used.add(i)
            if code not in p["original_languages"]:
                p["original_languages"].append(code)
    for ph in TRANSLATED:
        i = find_phrase(words, ph)
        if i >= 0:
            used.add(i)
            p["translated"] = "no" if neg_at(i) else "yes"
            break
    # "روسية ... عربية" or "عربية مترجمة": Arabic there is the language of the copy
    if "ar" in p["original_languages"] and (len(p["original_languages"]) > 1 or p["translated"] == "yes"):
        p["original_languages"].remove("ar")
    award_used = set()
    if wide:
        # «ميدالية نجيب محفوظ» is the prize, not the author; «البوكر العالمية» the International Booker
        for key, phrases in AWARD_PHRASES_WIDE.items():
            for ph in phrases:
                i = find_phrase(words, ph)
                if i >= 0 and not neg_at(i):
                    award_used.update(range(i, i + len(normalize(ph).split())))
                    if key not in p["awards"]:
                        p["awards"].append(key)
        used.update(award_used)
        for key, phrases in SCREEN_PHRASES_WIDE.items():
            for ph in phrases:
                i = find_phrase(words, ph)
                if i >= 0 and not neg_at(i) and not (key == "film" and p["adaptation"]):
                    used.update(range(i, i + len(normalize(ph).split())))
                    p["adaptation"] = key if p["adaptation"] in (None, key) else "screen"
    # prizes and screen adaptations (2026-10-08)
    for i, w in enumerate(words):
        if i in award_used:
            continue
        forms = _variants(w)
        key = next((k for k, f in (W["awards"] if wide else AWARD_WORDS).items() if forms & f), None)
        if key and not neg_at(i):
            used.add(i)
            for k in (["ipaf", "booker"] if key == "booker_any" else [key]):
                if k not in p["awards"]:
                    p["awards"].append(k)
            if key != "any":
                p["awards"] = [k for k in p["awards"] if k != "any"]
        elif w.isdigit() and len(w) == 4 and 1900 <= int(w) <= 2100 and p["awards"]:
            used.add(i)
            p["award_year"] = int(w)
        kind = next((k for k, f in SCREEN_WORDS.items() if forms & f), None)
        if wide and kind == "animated" and any(x.startswith(("ورق", "اوراق", "صفحات")) for x in words):
            kind = None   # «كتاب ورقه كرتون سميك» is board pages, not a cartoon (QADKWFIX, 11-14 36.1)
        if kind and not negated(words, i):
            used.add(i)
            p["adaptation"] = ("animated" if "animated" in (kind, p["adaptation"])
                               else "screen" if p["adaptation"] and p["adaptation"] != kind else kind)
    if any(find_phrase(words, ph) >= 0 for ph in ANIMATED_PHRASES):
        p["adaptation"] = "animated"
    if any(find_phrase(words, ph) >= 0 for ph in BEGINNER):
        p["reader_level"].append("beginner")
    if any(find_phrase(words, ph) >= 0 for ph in ADVANCED):
        p["reader_level"].append("advanced")
    # "بدون رعب": horror is both a content warning and a tense mood
    if "tense" in p["moods_exclude"] and "horror" not in p["avoid_content"]:
        p["avoid_content"].append("horror")
    if audience_fn:
        p["audience"] = audience_fn(normalize(text))
    if wide:
        if "romantic" in p["moods_exclude"] and "رومانسية" not in p["subgenres_exclude"]:
            p["subgenres_exclude"].append("رومانسية")   # «ما تبي رومانسية»: no book filed as a romance either
        if p["awards"] and any(k != "any" for k in p["awards"]):
            p["awards"] = [k for k in p["awards"] if k != "any"]   # "any Pulitzer winners?" is the Pulitzer
        if set(p["subgenres"]) & FICTION_KINDS and not p["genres"] and not flags["genres_negated"]:
            # «فانتازيا»، "sci-fi pls": a novel of that kind (for a child: a children's book), as the model reads it
            p["genres"] = ["أطفال"] if p["audience"] == "children" else ["رواية"]
        elif p["audience"] == "children" and p["genres"] == ["رواية"] and any(_variants(w) & _STORY_WORDS for w in words):
            p["genres"] = ["أطفال"]   # «قصة لبنتي ست سنوات» is a children's book
    # words not understood may name a title or an author; they never become a mood
    negs = (NEGATORS | NEGATORS_WIDE) if wide else NEGATORS
    p["title_keywords"] = [w for i, w in enumerate(words)
                           if i not in used and len(w) > 1 and w not in STOP and w not in negs
                           and not w.isdigit() and not neg_at(i)   # «ولا فيها موت» is not a title word
                           and not (len(w) > 2 and w[0] in "وف" and w[1:] in negs)
                           and not (wide and _variants(w) & (TITLE_COMMON | STOP))]
    if wide:
        # the common words left out, kept apart: beside another word they still help find a title («غرناطة (آخر الأيام)»)
        p["title_common"] = [w for i, w in enumerate(words) if i not in used and len(w) > 2 and w not in STOP
                             and _variants(w) & TITLE_COMMON and not neg_at(i)]
    for k in p:
        if isinstance(p[k], list):
            p[k] = list(dict.fromkeys(p[k]))
    return p, flags


def understand(messages, audience_fn=None, wide=False):
    """Merge the preferences of the visitor's messages in order (later ones win).
    wide=True (QADKWFIX): the wider reading of the model-free answer, with "subgenres" / "subgenres_exclude"."""
    merged = _empty()
    if wide:
        merged["subgenres"], merged["subgenres_exclude"], merged["title_common"] = [], [], []
    for text in messages:
        p, flags = _parse_one(text, audience_fn, wide)
        if wide:
            merged["title_common"] += [x for x in p["title_common"] if x not in merged["title_common"]]
            if p["subgenres"]:
                merged["subgenres"] = p["subgenres"]   # «لا، خلها خيال علمي» replaces the earlier kind
            merged["subgenres_exclude"] += [s for s in p["subgenres_exclude"] if s not in merged["subgenres_exclude"]]
            merged["subgenres"] = [s for s in merged["subgenres"] if s not in p["subgenres_exclude"]]
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
        if p["original_languages"]:
            merged["original_languages"] = p["original_languages"]
        if p["translated"]:
            merged["translated"] = p["translated"]
        if p["awards"]:
            merged["awards"] = p["awards"]
            merged["award_year"] = p["award_year"]
        if p["adaptation"]:
            merged["adaptation"] = p["adaptation"]
        if p["audience"]:
            merged["audience"] = p["audience"]
    merged["moods_include"] = [m for m in merged["moods_include"] if m not in merged["moods_exclude"]]
    merged["weight_include"] = [w for w in merged["weight_include"] if w not in merged["weight_exclude"]]
    return merged


def understood_anything(prefs):
    keys = ("genres", "moods_include", "moods_exclude", "pace", "weight_include", "weight_exclude",
            "use_cases", "reader_level", "avoid_content", "original_languages", "awards",
            "subgenres", "subgenres_exclude")   # QADKWFIX: only the wider reading has these two
    return bool(prefs.get("length") or prefs.get("audience") or prefs.get("translated") or prefs.get("adaptation")
                or any(prefs.get(k) for k in keys))
