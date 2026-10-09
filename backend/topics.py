"""Subject search: «كتاب عن الأندلس»، «أترك التسويف»، «خيال علمي عن الفضاء» (2026-10-06).

The interpreter lists the subjects the visitor wants to read about; here they are looked up in
what we store about each book: its title, its subjects and its short summary. Only words that
are specific enough count (a word found in a large share of the catalog, like «تاريخ» or «كتاب»,
says nothing), and every specific word of a subject must be present in the book.
"""
import json
import time

from keyword_prefs import normalize, strip_al

TITLE, SUBJECTS, TEXT = 8, 6, 2          # score by where the subject was found
COMMON_SHARE = 0.06                      # a word in more than this share of books is not a subject
_STOP = {normalize(w) for w in (
    "عن في من الى على مع او و كتاب كتب روايه روايات قصه قصص ادب شي شيء موضوع يتكلم يتحدث تتكلم حول بدون غير "
    "مو ما لا اللي الذي التي هذا هذه كل بعض اكثر اقل جدا مره كثير نوع انواع ممتع ممتعه جميل حلو زين "
    "سرد اسلوب كلام طريقه عملي عمليه سهل بسيط book books about the a an of and or on in for with").split()}
# «حائزة على نوبل»، «أخذ جائزة»: the award words only say «a prize»; the prize's name is the subject
# (library trial, 2026-10-08)
AWARD_WORDS = {normalize(w) for w in (
    "جائزه جائزة جوائز الجائزه الجوائز بجائزه بالجائزه حائز حائزه الحائز الحائزه حايز فائز فائزه الفائز "
    "الفائزه فاز فازت حاصل حاصله الحاصل اخذ اخذت نال نالت award awards prize prizes winning won winner").split()}
_STOP |= AWARD_WORDS
_AWARD_STEMS = {"جائزه", "جوائز", "prize", "award"}
# «البوكر» in our summaries also means poker; the Arabic Booker is written out as its full name
_IPAF = "الجائزه العالميه للروايه العربيه"
_cache = {"key": None, "at": 0.0, "index": None}


def stems(word):
    """Forms a word may take in the catalog: without «ال», one attached prefix letter, or a short
    possessive/feminine/plural ending («وقتي» ~ «الوقت», «للتسويف» ~ «التسويف», «اندلسي» ~ «اندلس»)."""
    w = normalize(word).strip()
    out = {w, strip_al(w)}
    for pre in ("وال", "بال", "فال", "كال", "لل"):
        if w.startswith(pre) and len(w) > len(pre) + 2:
            out.add(w[len(pre):])
    if len(w) > 4 and w[0] in "وفبلك":
        out.add(strip_al(w[1:]))
    for v in list(out):
        for suf in ("يه", "يات", "ات", "ين", "ون", "ها", "هم", "ي"):  # not «ه»: «الغربة» is not «الغرب»
            # the adjective ending needs a longer stem: «الأندلسي» ~ «الأندلس», but «المالي» is not «المال»
            least = 4 if suf in ("ي", "يه") else 3
            if v.endswith(suf) and len(strip_al(v)) - len(suf) >= least:
                out.add(v[:-len(suf)])
    return {v for v in out if len(v) >= 3}


def _is_year(w):
    return w.isdigit() and len(w) == 4 and 1800 <= int(w) <= 2099


def _tokens(text):
    out = set()
    for w in normalize(text).split():
        if _is_year(w):
            out.add(w)              # «البوكر ٢٠٢٢»: the year is part of the subject
        elif len(w) >= 3 and not w.isdigit():
            out |= stems(w)
    return out


def _json_words(value):
    try:
        items = json.loads(value) if isinstance(value, str) and value.strip().startswith("[") else [value]
    except ValueError:
        items = [value]
    return " ".join(str(x) for x in items if x)


def index(all_books):
    """{book_id: (title words, subject words, summary words)} and the share of books per word.
    Rebuilt when the catalog size changes or after two minutes."""
    key = len(all_books)
    if _cache["index"] is not None and _cache["key"] == key and time.time() - _cache["at"] < 120:
        return _cache["index"]
    books, counts = {}, {}
    for bid, b in all_books.items():
        title = _tokens(b.get("title"))
        subj = _tokens(_json_words(b.get("subjects")) + " " + _json_words(b.get("subgenres")) + " " + str(b.get("setting") or ""))
        text = _tokens(str(b.get("summary") or "") + " " + str(b.get("hook") or "") + " " + _json_words(b.get("learning_outcomes")))
        books[bid] = (title, subj, text)
        for w in title | subj | text:
            counts[w] = counts.get(w, 0) + 1
    _cache.update(key=key, at=time.time(), index=(books, counts, max(1, len(all_books))))
    return _cache["index"]


def content_words(phrase):
    """Every word of a subject that carries meaning, each as its accepted forms."""
    out = []
    for w in normalize(phrase).split():
        if _is_year(w):
            out.append({w})
            continue
        if len(w) < 3 or w in _STOP or strip_al(w) in _STOP or w.isdigit():
            continue
        forms = stems(w)
        if forms:
            out.append(forms)
    return out


def specific_words(phrase, idx):
    """The words of a subject that can identify a book (not found in a large share of the catalog)."""
    _, counts, total = idx
    return [forms for forms in content_words(phrase)
            if max(counts.get(f, 0) for f in forms) <= COMMON_SHARE * total]


def _variants(phrase):
    """The ways a subject may be written in our data. «البوكر» needs a prize word beside it (poker
    is «البوكر» too) or the Arabic Booker's full name; «حائز على جائزة» alone is any prize."""
    words = normalize(phrase).split()
    years = [w for w in words if _is_year(w)]
    if any("بوكر" in w or w == "booker" for w in words):
        rest = " ".join(w for w in words if "بوكر" not in w and w != "booker")
        return [("booker", rest), ("text", _IPAF + " " + " ".join(years))]
    if not content_words(phrase) and any(w in AWARD_WORDS or strip_al(w) in AWARD_WORDS for w in words):
        return [("award", "")]
    return [("text", phrase)]


def _award_in(tokens):
    return bool(tokens & _AWARD_STEMS)


def match(phrases, book_id, idx):
    """-> (score, subjects the book is about, subjects its summary only mentions) for one book.
    A subject needs at least one specific word and counts where ALL its words are found
    («معنى الحياة» needs both). In the title or the listed subjects the book is about it; in the
    summary alone it is only mentioned, which ranks lower and is worded as such."""
    entry = idx[0].get(book_id)
    if not entry:
        return 0, [], []
    title, subj, text = entry
    score, about, mentioned = 0, [], []
    for phrase in phrases:
        for kind, v in _variants(phrase):
            if kind == "award":
                words = []
                if not _award_in(title | subj | text):
                    continue
            elif kind == "booker":
                words = [{"بوكر", "booker"}] + content_words(v)
                if not (all(forms & (title | subj | text) for forms in words) and _award_in(title | subj | text)):
                    continue
            else:
                if not specific_words(v, idx):
                    continue
                words = content_words(v)
                if not all(forms & (title | subj | text) for forms in words):
                    continue
            if words and all(forms & title for forms in words):
                score += TITLE
                about.append(phrase)
            elif words and all(forms & (title | subj) for forms in words):
                score += SUBJECTS
                about.append(phrase)
            else:
                score += TEXT
                mentioned.append(phrase)
            break
    return score, about, mentioned


def core_word(phrase, idx):
    """The rarest specific word of a subject of two words or more («سقوط غرناطة» -> «غرناطة»), used
    only when no book in stock has the whole subject (QA 2026-10-08), else None."""
    _, counts, total = idx
    best = None
    words = [w for w in normalize(phrase).split() if len(w) >= 3 and w not in _STOP and strip_al(w) not in _STOP and not w.isdigit()]
    if len(words) < 2:
        return None
    for w in words:
        n = max(counts.get(f, 0) for f in stems(w)) if stems(w) else 0
        if 0 < n <= COMMON_SHARE * total and (best is None or n < best[0]):
            best = (n, w)
    return best[1] if best else None


def searchable(phrases, idx):
    """The subjects that have at least one specific word (the others cannot be searched)."""
    return [p for p in phrases
            if any(kind != "text" or specific_words(v, idx) for kind, v in _variants(p))]
