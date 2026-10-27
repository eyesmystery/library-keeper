"""The five start buttons of the visitor chat (five tap-ready answers each since 2026-10-27, Ibrahim: «اقل شيء ٥ اختيارات مساعدة») (Ibrahim 2026-10-11, from the library owners' proposal).

Tapping a start button never brings books straight away: Manar first asks one question for that path,
with tap-ready answers. The visitor's answer then goes through the normal conversation; a vague answer
gets one more question, and after two questions (or the «just show me» answer) books are suggested.
The replies are fixed text, so they need no model call and work in the keyword fallback too.
"""
from keyword_prefs import normalize

SKIP_AR = "ورّني كتب على طول"
SKIP_EN = "Just show me books"
SURPRISE_AR = "أي شي يفاجئني"
SURPRISE_EN = "Anything, surprise me"

# start button text -> (Manar's first question, tap-ready answers)
PATHS = {
    "ar": {
        "أدوّر كتاب معيّن": ("أبشر! وش اسم الكتاب، أو اسم مؤلفه؟", []),
        "رشّحي لي كتاب يناسب ذوقي أو مزاجي": (
            "حلو! قل لي وش آخر كتاب قرأته وعجبك، أو وش مزاجك اليوم؟",
            ["رواية تشد", "شي خفيف ومريح", "كتاب يفيدني", "شي قصير أخلّصه بسرعة", SKIP_AR]),
        "أبغى كتاب هدية": (
            "فكرة حلوة! لمن الهدية، ووش يحب يقرأ؟",
            ["لصديق يحب الروايات", "لطفل", "لشخص يحب تطوير الذات", "لشخص يحب التاريخ", "ما أدري وش يحب"]),
        "أبغى أبدأ قراءة": (
            "خطوة جميلة! وش يشدّك أكثر: القصص، ولا المعلومات، ولا الكتب اللي تطوّر الذات؟",
            ["القصص والروايات", "المعلومات والتاريخ", "تطوير الذات", "شي قصير وسهل", SKIP_AR]),
        "فاجئيني بكتب جديدة": (
            "يسعدني! تبغى المفاجأة رواية، ولا كتاب غير روائي؟",
            ["رواية", "كتاب غير روائي", "كتاب فاز بجائزة", "شي قصير", SURPRISE_AR]),
    },
    "en": {
        "Find a specific book": ("Sure! What is the book's title, or its author?", []),
        "Recommend something for my taste or mood": (
            "Lovely! Tell me the last book you read and enjoyed, or your mood today.",
            ["A gripping novel", "Something light", "Something useful", "A short, quick read", SKIP_EN]),
        "I want a gift book": (
            "Nice idea! Who is the gift for, and what do they like to read?",
            ["A friend who loves novels", "A child", "Someone into self-help", "Someone who loves history", "Not sure what they like"]),
        "I want to start reading": (
            "A great step! What draws you most: stories, facts, or books for personal growth?",
            ["Stories and novels", "Facts and history", "Personal growth", "Something short and easy", SKIP_EN]),
        "Surprise me with new books": (
            "Happy to! Would you like the surprise to be a novel, or non-fiction?",
            ["A novel", "Non-fiction", "A prize-winning book", "Something short", SURPRISE_EN]),
    },
}

_KEYS = {lang: {" ".join(normalize(k).split()): k for k in paths} for lang, paths in PATHS.items()}
# the button's earlier wording, from a page opened before the change (2026-10-11: «رشّح» -> «رشّحي», Manar is a woman)
_KEYS["ar"][" ".join(normalize("رشّح لي كتاب يناسب ذوقي أو مزاجي").split())] = "رشّحي لي كتاب يناسب ذوقي أو مزاجي"
# «I don't know» answers to a path's first question: Manar asks one more question before any book
UNDECIDED = ("ما أدري وش يحب", "Not sure what they like", "ما أدري", "مدري", "I don't know")
_UNDECIDED = {" ".join(normalize(x).split()) for x in UNDECIDED}
_SKIPS = {" ".join(normalize(x).split()) for x in (SKIP_AR, SKIP_EN, SURPRISE_AR, SURPRISE_EN)}


def opener(user_messages, language="ar"):
    """-> (reply, suggestions) when the conversation is only a tapped start button, else None."""
    if len(user_messages) != 1:
        return None
    lang = "en" if language == "en" else "ar"
    key = _KEYS[lang].get(" ".join(normalize(user_messages[0]).split()))
    return PATHS[lang][key] if key else None


def is_skip(text):
    """«ورّني كتب على طول» / «أي شي يفاجئني»: no more questions, show books now."""
    return " ".join(normalize(text or "").split()) in _SKIPS


def is_undecided(text):
    """«ما أدري وش يحب»: still nothing to choose by, so one more question."""
    return " ".join(normalize(text or "").split()) in _UNDECIDED
