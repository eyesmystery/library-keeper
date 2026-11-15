"""[SETS] Books sold together under one record (Ibrahim 2026-11-13: «بعض الكتب في المكتبة مسجلة كمجموعة تنباع مع بعض. مثلا
كتابين مسجلين تحت رقم واحد. هذي تحتاج معالجة خاصة عدد صفحات الكتاب والمعلومات اللي عادة تخص كتاب واحد مو اكثر»).

books.volumes      how many books / parts the one record holds; empty or 1 = one book
books.set_kind     'one_work'   = one work in several parts («ذهب مع الريح (جزءان)», «الأنوار والمراقب (7 أجزاء)»)
                   'collection' = different books together («سلسلة أنا والعالم» five picture books, «الأعمال الكاملة»)
books.volumes_source  where the count came from: title | summary | staff

For such a record the page count is not the length of one book (it may be all the parts, or one of them): the chat never
uses it as a length (page limits, shortest / longest, short / long, quick read) and never offers it to someone who asked for a
short book; the card and the facts say «في N أجزاء» / «مجموعة من N كتب». For a collection no single first-publication year,
Goodreads rating, prize, screen adaptation or ISBN is given as if it were one book's; a work in parts keeps its own.
"""
import re

KINDS = ("one_work", "collection")
SOURCES = ("title", "summary", "staff")
MAX_VOLUMES = 99
KIND_AR = {"one_work": "عمل واحد مقسوم على أجزاء", "collection": "مجموعة كتب مختلفة"}
_AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def volumes(book):
    """-> the number of books / parts in the record when more than one, else None."""
    try:
        n = int((book or {}).get("volumes") or 0)
    except (TypeError, ValueError):
        return None
    return n if n > 1 else None


def is_set(book):
    return volumes(book) is not None


def kind(book):
    """'one_work' | 'collection' for a set (a count with no kind is read as a collection: nothing of one book is assumed),
    None for one book."""
    if not is_set(book):
        return None
    k = (book or {}).get("set_kind")
    return k if k in KINDS else "collection"


def single_pages(book):
    """The page count as the length of one book, or None for a set (and for no page count)."""
    if is_set(book):
        return None
    return (book or {}).get("page_count") or None


def label(book, language="ar"):
    """«في ٧ أجزاء» / «مجموعة من ٥ كتب» (en: "in 7 volumes" / "a set of 5 books"), None for one book."""
    n = volumes(book)
    if not n:
        return None
    k = kind(book)
    if language == "en":
        return f"in {n} volumes" if k == "one_work" else f"a set of {n} books"
    d = str(n).translate(_AR_DIGITS)
    if k == "one_work":
        return "في جزأين" if n == 2 else (f"في {d} أجزاء" if n <= 10 else f"في {d} جزءًا")
    return "مجموعة من كتابين" if n == 2 else (f"مجموعة من {d} كتب" if n <= 10 else f"مجموعة من {d} كتابًا")


def fact(book, language="ar"):
    """The facts' entry for the model: what the record holds and what not to say."""
    n = volumes(book)
    if not n:
        return None
    k = kind(book)
    if language == "en":
        note = ((f"One work in {n} volumes, sold together as one record." if k == "one_work" else
                 f"{n} different books sold together as one record.")
                + " We hold no page count of one volume: give no page count as its length, never call it short, and offer it"
                  " as the whole set.")
        note += (" The author, first publication and prizes are the work's own." if k == "one_work" else
                 " No single first-publication year, rating or prize belongs to the set.")
        return {"volumes": n, "kind": "one work in parts" if k == "one_work" else "different books together", "note": note,
                "say": label(book, "en")}
    note = ((f"عمل واحد {label(book, 'ar')} تُباع معًا في سجل واحد." if k == "one_work" else
             f"{label(book, 'ar')} مختلفة تُباع معًا في سجل واحد.")
            + " ما عندنا عدد صفحات جزء واحد: لا تذكر له عدد صفحات كطول، ولا تصفه بأنه قصير، واعرضه كمجموعة كاملة.")
    note += (" المؤلف وسنة الصدور الأصلية والجوائز للعمل نفسه." if k == "one_work" else
             " لا تنسب للمجموعة سنة صدور أصلية واحدة ولا تقييمًا ولا جائزة.")
    return {"volumes": n, "kind": KIND_AR[k], "note": note, "say": label(book, "ar")}


def fmt(n):
    return str(n).translate(_AR_DIGITS)


_PER_BOOK_FIELDS = ("original_year", "original_year_source", "original_year_ref", "goodreads_rating", "goodreads_ratings_count",
                    "goodreads_rating_source", "isbn")
_LENGTH_USES = ("quick_read",)


def chat_view(books, cards=None, language="ar"):
    """The chat's view of the catalogue (books: a dict {id: book} or a list), changed in place and returned: a set's page count
    is moved aside (set_pages_recorded) so no length rule reads it; a collection loses the facts of one book; the card of a set
    loses its length and «quick read». Each set gets set_label for the visitor's card. Safe to call twice."""
    items = books.values() if isinstance(books, dict) else books
    for b in items:
        if not isinstance(b, dict) or not is_set(b) or b.get("_set_view"):
            continue
        b["_set_view"] = True
        b["set_pages_recorded"] = b.get("page_count")
        b["page_count"] = None
        b["set_label"] = label(b, language)
        b["set_kind"] = kind(b)
        if kind(b) == "collection":
            for f in _PER_BOOK_FIELDS:
                if f in b:
                    b[f] = None
        c = (cards or {}).get(b.get("book_id"))
        if isinstance(c, dict) and not c.get("_set_view"):
            c["_set_view"] = True
            c["perceived_length"] = None
            c["use_cases"] = [u for u in c.get("use_cases") or [] if u not in _LENGTH_USES]
            conf = c.get("confidence")
            if isinstance(conf, dict):
                for key in [k for k in conf if isinstance(k, tuple) and (k[0] == "perceived_length"
                                                                         or (k[0] == "use_cases" and k[1] in _LENGTH_USES))]:
                    conf.pop(key, None)
    return books


def collection_ids(conn):
    """Book ids of collections (several different books in one record), for the prize / adaptation records; empty on a
    database without the columns."""
    try:
        return {r[0] for r in conn.execute("SELECT book_id FROM books WHERE volumes > 1 AND COALESCE(set_kind, '') <> 'one_work'")}
    except Exception:  # noqa: BLE001 - a database not yet migrated
        return set()


def set_ids(conn):
    try:
        return {r[0] for r in conn.execute("SELECT book_id FROM books WHERE volumes > 1")}
    except Exception:  # noqa: BLE001
        return set()


# ------------------------------------------------------------------ reading a record (for the data pass and staff help)
_NUM_WORDS = {"اثنين": 2, "اثنان": 2, "ثلاثة": 3, "ثلاث": 3, "أربعة": 4, "اربعة": 4, "أربع": 4, "اربع": 4, "خمسة": 5, "خمس": 5,
              "ستة": 6, "ست": 6, "سبعة": 7, "سبع": 7, "ثمانية": 8, "ثماني": 8, "تسعة": 9, "تسع": 9, "عشرة": 10, "عشر": 10}
_NUMS = "|".join(_NUM_WORDS)
_DIG = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_COLLECTION_TITLE = re.compile(r"(?:مجموعة|مجموعه|الأعمال|الاعمال|أعمال|سلسلة|سلسله|مختارات|box(?:ed)?\s*set|boxset|treasury|collection)",
                               re.I)
_PARTS_IN_SUMMARY = re.compile(r"جزأين|جزءين|جزئين|مجلدين|(?:\d{1,2}|" + _NUMS + r")\s+(?:أجزاء|مجلدات)")
# (rule, fixed count, needs the summary to speak of parts too)
_TITLE_RULES = [
    # «(7 أجزاء)», «(11 جزءًا)», «(5 كتب)», «(3 مجلدات)»
    (re.compile(r"\(\s*(\d{1,2})\s*(?:أجزاء|اجزاء|جزءًا|جزءا|جزء|مجلدات|مجلدًا|مجلدا|كتب|كتابًا|كتابا)\s*\)"), None, False),
    # «(جزءان)», «(جزآن)», «(مجلدان)»
    (re.compile(r"\(\s*(?:جزءان|جزآن|جزأين|جزئين|جزءين|مجلدان|مجلدين|كتابان|كتابين)\s*\)"), 2, False),
    # «(1-3)»
    (re.compile(r"\(\s*1\s*[-–]\s*(\d{1,2})\s*\)"), None, False),
    # «(ج1-ج10)», «(ج1، ج2)»
    (re.compile(r"ج\s*1\s*[-–]\s*ج\s*(\d{1,2})"), None, False),
    (re.compile(r"\(\s*ج\s*1\s*[،,]\s*ج\s*2\s*\)"), 2, False),
    # «1/3», «2/1», «4/1»: the parts of the set (written either way)
    (re.compile(r"(?<![\d/])1\s*/\s*(\d{1,2})(?![\d/])"), None, False),
    (re.compile(r"(?<![\d/])(\d{1,2})\s*/\s*1(?![\d/])"), None, False),
    # "(2 volumes)", "3 vols"
    (re.compile(r"(\d{1,2})\s*(?:volumes|vols?\b\.?)", re.I), None, False),
    # "1-4 Boxed Set", "1-4 Treasury Boxed Set"
    (re.compile(r"\b1\s*[-–]\s*(\d{1,2})\b(?=.*\b(?:box(?:ed)?\s*set|boxset|treasury|collection)\b)", re.I), None, False),
    # «(1: أيام البلاد، 2: حكايا المخيم)»: the names of the two parts — a set only when the summary says it is in parts too
    # («دوريت الصغيرة (1: الفقر، 2: الغنى)» names the novel's own two books)
    (re.compile(r"\(\s*1\s*:\s*[^)]*?[،,]\s*2\s*:"), 2, True),
]
# a summary that counts the books of the record: «مجموعة من خمسة كتب», «أربعة كتيبات مصورة», «في أربعة مجلدات», «في مجلدين»
_SUMMARY_SURE = re.compile(
    r"(?:(?:من|في)\s+|^|[:：]\s*)(?P<n>\d{1,2}|" + _NUMS + r")\s+"
    r"(?P<w>كتب|كتيبات|كتيّبات|قصص\s+مصورة|مجلدات)(?=\s|$|[،.:])"
    r"|(?:من|في)\s+(?P<two>مجلدين|كتابين)")
_SUMMARY_DOUBT = re.compile(r"في\s+(?:جزأين|جزءين|جزئين|(?:\d{1,2}|" + _NUMS + r")\s+أجزاء)|مجلدات")
_TITLE_DOUBT = re.compile(r"(?:الأعمال|الاعمال)\s+(?:الكاملة|القصصية|الروائية|الشعرية|النثرية)|with\s+box|بالعلبة|\bbox\b", re.I)
# one volume said in so many words: «(مجلد)», «ثلاثية …» in one volume, «الجزء الثاني», «ج3», «مجلد يجمع …»
_ONE_VOLUME = re.compile(r"\(\s*مجلد\s*\)|ثلاثية|رباعية|أحد\s+المجلدات|الجزء\s+(?:\d+|الأول|الثاني|الثالث|الرابع|الخامس)"
                         r"|(?<![\w])ج\s*\d+(?!\s*[-–،,]\s*ج)|^مجلد\s|مجلد\s+(?:واحد|يجمع)")
# «ضمن سلسلة من ثمانية كتب»: the series has eight books, the record is one of them
_ONE_OF_SERIES = re.compile(r"(?:ضمن|إحدى|احدى|أحد|واحدة?\s+من)\s+(?:ال)?سلسلة\s*$")
_CHILD_GENRES = {"أطفال", "قصص مصورة", "يافعين"}


# DECIDE1115 (Ibrahim 2026-11-15 «موافق عالتوصيات» on SETS question 2): a shop import marks a set by itself only when its title says so
# plainly — «(جزءان / جزآن)»، «(N أجزاء / كتب / مجلدات)»، «(1-N)»، «N/1» or «1/N»، "N volumes"، "1-N Boxed Set" (the _TITLE_RULES above at
# 0, 1, 2, 5, 6, 7, 8). Not «(ج1-ج10)» / «(ج1، ج2)» (staff check), not the names of two parts that need the summary, not a summary count
CLEAR_TITLE_RULES = frozenset({0, 1, 2, 5, 6, 7, 8})


def clear_from_title(title):
    """-> {"volumes", "kind", "evidence"} when the title alone says plainly that the record holds several books / parts, else None."""
    d = detect(title, "")
    if not d or d.get("source") != "title" or not d.get("sure") or d.get("rule") not in CLEAR_TITLE_RULES or not d.get("volumes"):
        return None
    return {"volumes": d["volumes"], "kind": d["kind"], "evidence": d["evidence"]}


def _n(text):
    t = str(text or "").translate(_DIG)
    return _NUM_WORDS.get(t, int(t) if t.isdigit() else 0)


def detect(title, summary="", genre=None):
    """Read a record's title (then its summary) for a set -> {"volumes", "kind", "source", "evidence", "sure"} or None.
    sure=False marks a record staff should check (no count said, «في جزأين» that can be one volume, «في ثمانية كتب» that can
    be the books inside one work such as Aristotle's Politics)."""
    t = " ".join(str(title or "").translate(_DIG).split())
    s = " ".join(str(summary or "").split())
    for rule, (rx, fixed, needs_summary) in enumerate(_TITLE_RULES):
        m = rx.search(t)
        if m:
            n = fixed or _n(m.group(1))
            if 2 <= n <= 30:
                listed = t.count("،") >= 1 and bool(re.search(r"\(\s*1\s*[-–]", t))
                k = "collection" if (_COLLECTION_TITLE.search(t) or listed) else "one_work"
                sure = not needs_summary or bool(_PARTS_IN_SUMMARY.search(s))
                return {"volumes": n, "kind": k, "source": "title", "evidence": m.group(0).strip(), "sure": sure, "rule": rule}
    one_volume = bool(_ONE_VOLUME.search(t) or _ONE_VOLUME.search(s[:40]))
    for m in _SUMMARY_SURE.finditer(s):
        if _ONE_OF_SERIES.search(s[max(0, m.start() - 30):m.start()]):
            continue
        n = 2 if m.group("two") else _n(m.group("n"))
        w = m.group("w") or m.group("two") or ""
        if not 2 <= n <= 30 or one_volume:
            continue
        volume_word = "مجلد" in w
        k = "one_work" if (volume_word and not _COLLECTION_TITLE.search(t)) else "collection"
        # «كتاب أرسطو … في ثمانية كتب», «رواية ثربانتس … في كتابين»: the books inside one work, unless the record is a
        # children's set or its title names a set
        sure = volume_word or (genre in _CHILD_GENRES) or bool(_COLLECTION_TITLE.search(t))
        return {"volumes": n, "kind": k, "source": "summary", "evidence": m.group(0).strip(" :："), "sure": sure}
    if one_volume:
        return None
    m = _TITLE_DOUBT.search(t)
    if m:
        return {"volumes": None, "kind": "collection" if re.search("الأعمال|الاعمال", t) else None, "source": "title",
                "evidence": m.group(0), "sure": False}
    m = _SUMMARY_DOUBT.search(s)
    if m and not _ONE_VOLUME.search(s[max(0, m.start() - 30):m.end() + 10]):
        return {"volumes": None, "kind": None, "source": "summary", "evidence": m.group(0), "sure": False}
    return None
