"""At least five tap-ready helpers under a chat answer (Ibrahim 2026-10-27: «يضيف بعد اختيارين للمساعدة. يعني اقل شيء
٥ اختيارات مساعدة»), and the «رشّحي لي تفضيلات قرّاء مكتبة ثلاث» helper.

The model is asked for five suggestions (chat.RESPOND_SYSTEM rule 9). When an answer still carries fewer — the model
gave three, or a fixed answer (keyword fallback, a no-data answer) has two — pad() adds general ways forward that
are not already there in other words, so every answer that offers helpers offers at least MIN. An answer that offers
none on purpose (Manar asks for the title of the book the visitor is looking for) is left without.
"""
import re

from keyword_prefs import normalize

MIN = 5
FAVORITES = {"ar": "رشّحي لي تفضيلات قرّاء مكتبة ثلاث", "en": "Thalath readers' favourites"}
FAVORITES_MORE = {"ar": "غيرها من تفضيلات القرّاء", "en": "More readers' favourites"}
# (a pattern that means the visitor already has this way forward, the helper)
_AFTER_BOOKS = {
    "ar": [(r"غير", "ورّني غيرها"), (r"قصير|اقصر", "شي أقصر"), (r"خفيف|اخف", "شي أخف"), (r"روايه|روايات", "رشّحي لي رواية"),
           (r"فاجئ|فاجا", "فاجئيني بكتاب"), (r"هديه", "أبغى كتاب هدية")],
    "en": [(r"other|else|more", "Show me others"), (r"short", "Something shorter"), (r"light", "Something lighter"),
           (r"novel", "Suggest a novel"), (r"surprise", "Surprise me"), (r"gift", "I want a gift book")],
}
_BEFORE_BOOKS = {
    "ar": [(r"علي طول|على طول", "ورّني كتب على طول"), (r"فاجئ|فاجا", "فاجئيني بكتاب"), (r"روايه|روايات", "رواية تشد"),
           (r"خفيف|اخف", "شي خفيف ومريح"), (r"هديه", "أبغى كتاب هدية")],
    "en": [(r"just show", "Just show me books"), (r"surprise", "Surprise me"), (r"novel", "A gripping novel"),
           (r"light", "Something light"), (r"gift", "I want a gift book")],
}


def _key(text):
    return " ".join(normalize(str(text or "")).split())


def is_favorites(text):
    """The visitor tapped «تفضيلات قرّاء مكتبة ثلاث» (or «غيرها من تفضيلات القرّاء»), in either language."""
    k = _key(text)
    return bool(k) and k in {_key(x) for d in (FAVORITES, FAVORITES_MORE) for x in d.values()} | {
        _key("رشحي لي تفضيلات قُرّاء مكتبة ثلاث"), _key("تفضيلات قرّاء مكتبة ثلاث")}


def pad(chips, has_books, language="ar", favorites_ready=False, none_on_purpose=False):
    """-> the helpers, at least MIN; never a duplicate. none_on_purpose: an answer that offers none on purpose (Manar asks for
    the title of the book the visitor is looking for) stays without."""
    lang = "en" if language == "en" else "ar"
    out, seen = [], set()
    for c in chips or []:
        if isinstance(c, str) and c.strip() and _key(c) not in seen:
            out.append(c.strip())
            seen.add(_key(c))
    # QADFIX22 (QAD 2026-10-27, cases 11.1, 28.1): a reply with no helper of its own (the readers' favourites before enough
    # reviews, «ما عندنا كتب لعلي الطنطاوي») still gets five — «خمس اقتراحات تحت كل رد»
    if not out and none_on_purpose:
        return out
    pool = list(_AFTER_BOOKS[lang] if has_books else _BEFORE_BOOKS[lang])
    if favorites_ready and not any(is_favorites(c) for c in out):
        pool.insert(2, (r"قراء مكتبه ثلاث|thalath readers|readers' fav", FAVORITES[lang]))
    have = " | ".join(_key(c) for c in out) + " | " + " | ".join(c.lower() for c in out)
    for pattern, chip in pool:
        if len(out) >= MIN:
            break
        if _key(chip) in seen or re.search(pattern, have):
            continue
        out.append(chip)
        seen.add(_key(chip))
        have += " | " + _key(chip) + " | " + chip.lower()
    return out
