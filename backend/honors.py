"""Awards and screen adaptations of books and authors (Ibrahim, 2026-10-08: «اعتمد الخطة»).

Three tables, each row with its source (never guessed):
  book_awards       a book won / was shortlisted / longlisted / nominated for a prize, with the year
  author_awards     a person's own prize (Nobel, a lifetime prize ...), optionally naming the work
  book_adaptations  a film / series / animated film made from the book, with the year

Award keys are fixed so the visitor chat can ask for them («البوكر ٢٠٢٢», «حائزة على نوبل»);
anything else is kept under the key "other" with its own name.
"""
import re
from datetime import datetime, timezone

from models import get_connection

AWARDS = {   # key -> Arabic name shown to staff and visitors
    "ipaf": "الجائزة العالمية للرواية العربية (البوكر العربية)",
    "booker": "جائزة البوكر",
    "intl_booker": "جائزة البوكر الدولية",
    "nobel": "جائزة نوبل للآداب",
    "zayed": "جائزة الشيخ زايد للكتاب",
    "katara": "جائزة كتارا للرواية العربية",
    "mahfouz": "ميدالية نجيب محفوظ للأدب",
    "sharjah": "جائزة الشارقة للإبداع العربي",
    "pulitzer": "جائزة بوليتزر",
    "goncourt": "جائزة غونكور",
    "hugo": "جائزة هوغو",
    "other": "جائزة أخرى",
}
RESULTS = {"winner": "فائز", "shortlist": "القائمة القصيرة", "longlist": "القائمة الطويلة", "nominated": "مرشّح"}
KINDS = {"film": "فيلم", "series": "مسلسل", "animated": "فيلم رسوم متحركة", "tv_film": "فيلم تلفزيوني", "play": "مسرحية", "other": "عمل مرئي"}
SCREEN = ("film", "series", "animated", "tv_film")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS book_awards (
        row_id INTEGER PRIMARY KEY AUTOINCREMENT, book_id INTEGER NOT NULL, award_key TEXT NOT NULL,
        award_name TEXT, result TEXT NOT NULL, year INTEGER, category TEXT,
        source TEXT NOT NULL, source_url TEXT, note TEXT, created_by TEXT, created_at TEXT,
        FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS author_awards (
        row_id INTEGER PRIMARY KEY AUTOINCREMENT, author_id INTEGER NOT NULL, award_key TEXT NOT NULL,
        award_name TEXT, result TEXT NOT NULL, year INTEGER, work_title TEXT,
        source TEXT NOT NULL, source_url TEXT, note TEXT, created_by TEXT, created_at TEXT,
        FOREIGN KEY (author_id) REFERENCES authors(author_id) ON DELETE CASCADE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS book_adaptations (
        row_id INTEGER PRIMARY KEY AUTOINCREMENT, book_id INTEGER NOT NULL, kind TEXT NOT NULL,
        title TEXT, year INTEGER, country TEXT,
        source TEXT NOT NULL, source_url TEXT, note TEXT, created_by TEXT, created_at TEXT,
        FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_book_awards_book ON book_awards(book_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_author_awards_author ON author_awards(author_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_book_adaptations_book ON book_adaptations(book_id)")


_YEAR = re.compile(r"^(1[5-9]\d\d|20\d\d)$")
_cache = {"at": 0.0, "data": None}


def _int_year(v):
    v = str(v or "").strip()
    return int(v) if _YEAR.match(v) else None


def award_name(row):
    return (row.get("award_name") or "").strip() if row.get("award_key") == "other" else AWARDS.get(row.get("award_key"), row.get("award_name") or "")


def award_label(row, with_work=False):
    """«الجائزة العالمية للرواية العربية (البوكر العربية): فائز 2022»."""
    text = award_name(row) + ": " + RESULTS.get(row.get("result"), "")
    if row.get("year"):
        text += f" {row['year']}"
    if with_work and row.get("work_title"):
        text += f" عن «{row['work_title']}»"
    return text


def adaptation_label(row):
    text = KINDS.get(row.get("kind"), "عمل مرئي")
    if row.get("title"):
        text += f" «{row['title']}»"
    if row.get("year"):
        text += f" ({row['year']})"
    return text


def load(max_age=60):
    """All rows, cached for a minute: {"book": {bid: [..]}, "author": {aid: [..]}, "adapt": {bid: [..]},
    "book_authors": {bid: [aid, ...]}}. Small tables; read whole."""
    import time
    if _cache["data"] is not None and time.time() - _cache["at"] < max_age:
        return _cache["data"]
    conn = get_connection()
    try:
        ensure_table(conn)
        data = {"book": {}, "author": {}, "adapt": {}, "book_authors": {}}
        for r in conn.execute("SELECT * FROM book_awards ORDER BY year DESC"):
            data["book"].setdefault(r["book_id"], []).append(dict(r))
        for r in conn.execute("SELECT * FROM author_awards ORDER BY year DESC"):
            data["author"].setdefault(r["author_id"], []).append(dict(r))
        for r in conn.execute("SELECT * FROM book_adaptations ORDER BY year"):
            data["adapt"].setdefault(r["book_id"], []).append(dict(r))
        if data["author"]:
            for r in conn.execute("SELECT book_id, author_id FROM book_authors WHERE role='author'"):
                if r["author_id"] in data["author"]:
                    data["book_authors"].setdefault(r["book_id"], []).append(r["author_id"])
    finally:
        conn.close()
    _cache.update(at=time.time(), data=data)
    return data


def clear_cache():
    _cache["data"] = None


def for_book(book_id):
    """-> {"awards": [labels], "author_awards": [labels], "adaptations": [labels]} (empty lists when none)."""
    d = load()
    return {"awards": [reason_label(r) for r in d["book"].get(book_id, [])],
            "author_awards": [reason_label(r, True) for aid in d["book_authors"].get(book_id, []) for r in d["author"].get(aid, [])],
            "adaptations": [adaptation_label(r) for r in d["adapt"].get(book_id, [])]}


def reason_label(row, author=False):
    """Visitor wording: «فاز بالجائزة العالمية للرواية العربية (البوكر العربية) 2022»،
    «وصل إلى القائمة القصيرة لـ…»، «مؤلفه فاز بجائزة نوبل للآداب 1988»."""
    name = award_name(row)
    bi = "ب" + name                                           # «بالجائزة»، «بجائزة نوبل»
    li = ("لل" + name[2:]) if name.startswith("ال") else "ل" + name   # «للجائزة»، «لجائزة بوكر»
    if name[:1].isascii():   # «فاز بجائزة «Eisner Award»» rather than «فاز بEisner Award» (2026-10-08)
        bi, li = f"بجائزة «{name}»", f"لجائزة «{name}»"
    verb = {"winner": "فاز " + bi, "shortlist": "وصل إلى القائمة القصيرة " + li,
            "longlist": "وصل إلى القائمة الطويلة " + li, "nominated": "رُشّح " + li}.get(row.get("result"), name)
    text = ("مؤلفه " if author else "") + verb + (f" {row['year']}" if row.get("year") else "")
    if author and row.get("work_title"):
        text += f" عن «{row['work_title']}»"
    return text


_AR = str.maketrans("أإآٱىة", "اااايه")
_TASHKEEL = re.compile(r"[\u064B-\u0652\u0640]")


def _nw(text):
    """Normalized words of a prize name, without «ال» («الملك فيصل» = «ملك فيصل»)."""
    words = _TASHKEEL.sub("", str(text or "")).translate(_AR).casefold().split()
    return [w[2:] if w.startswith("ال") and len(w) > 4 else w for w in words]


def named(phrase):
    """The key for a prize asked for by its own name («جائزة الملك فيصل»), matched against the names of
    prizes stored under «other» (QA2 2026-10-09, case 34)."""
    return "name:" + " ".join(str(phrase or "").split())


def _wanted(r, keys, want_any):
    if r["award_key"] in keys or (want_any and r["result"] == "winner"):
        return True
    for k in keys:
        if k.startswith("name:") and r["award_key"] == "other":
            have = set(_nw(r.get("award_name")))
            words = [w for w in _nw(k[5:]) if w not in ("جائزه", "جايزه")]
            if words and all(w in have for w in words):
                return True
    return False


def _in_years(r, year=None, min_year=None, max_year=None):
    y = _int_year(r.get("year"))
    return ((not year or r.get("year") == year) and (not min_year or (y or 0) >= min_year)
            and (not max_year or (y is not None and y <= max_year)))


def matches_award(book_id, keys, year=None, min_year=None, max_year=None):
    """Reasons («فاز بـ…»، «وصل إلى القائمة القصيرة لـ…») that make this book answer the prize wish;
    empty = it does not, winners first. keys: award keys, or ["any"]: «حائزة على جائزة» means a prize
    actually won (by the book or by its author), so lists and nominations count only for a NAMED prize;
    "name:<words>" is a prize stored under «other» by its name. A person's prize (Nobel ...) counts for
    each of their books. max_year: «قبل ٢٠١٥» (QA2 2026-10-09)."""
    d = load()
    want_any = "any" in keys
    rows = []
    for r in d["book"].get(book_id, []):
        if _wanted(r, keys, want_any) and _in_years(r, year, min_year, max_year):
            rows.append((r["result"] != "winner", reason_label(r)))
    for aid in d["book_authors"].get(book_id, []):
        for r in d["author"].get(aid, []):
            if _wanted(r, keys, want_any) and _in_years(r, year, min_year, max_year):
                rows.append((True, reason_label(r, True)))
    return [label for _, label in sorted(rows, key=lambda x: x[0])]


def newest_award_year(book_id, keys, min_year=None, max_year=None):
    """The latest year among the prizes that answer the wish (the book's own first), or 0:
    «فاز بجائزة حديثًا» ranks 2024 before 1984 (library trial, 2026-10-08)."""
    d = load()
    want_any = "any" in keys
    rows = list(d["book"].get(book_id, [])) + [r for aid in d["book_authors"].get(book_id, []) for r in d["author"].get(aid, [])]
    years = [_int_year(r.get("year")) or 0 for r in rows if _wanted(r, keys, want_any) and _in_years(r, None, min_year, max_year)]
    return max(years) if years else 0


def name_exists(phrase):
    """Is a prize of that name stored under «other» for any book or author?"""
    d = load()
    k = [named(phrase)]
    return any(_wanted(r, k, False) for rows in list(d["book"].values()) + list(d["author"].values()) for r in rows)


def matches_adaptation(book_id, kind):
    """kind: film / series / screen (any of the two) -> labels of the matching adaptations."""
    kinds = {"film": ("film", "animated", "tv_film"), "series": ("series",), "screen": SCREEN}.get(kind, ())
    return [adaptation_label(r) for r in load()["adapt"].get(book_id, []) if r["kind"] in kinds]


def _clean_url(v):
    v = str(v or "").strip()[:500]
    return v if v.startswith(("http://", "https://")) else None


def _clean_text(v, n=200):
    v = re.sub(r"[\x00-\x1f\x7f]", " ", str(v or "")).strip()
    return v[:n] or None


def validate(table, data):
    """-> clean row for book_awards / author_awards / book_adaptations; ValueError (Arabic) when wrong."""
    year = _int_year(data.get("year")) if str(data.get("year") or "").strip() else None
    if str(data.get("year") or "").strip() and year is None:
        raise ValueError("السنة غير صحيحة")
    url = _clean_url(data.get("source_url"))
    if table == "book_adaptations":
        kind = data.get("kind")
        if kind not in KINDS:
            raise ValueError("نوع العمل غير معروف")
        title = _clean_text(data.get("title"))
        return {"kind": kind, "title": title, "year": year, "country": _clean_text(data.get("country"), 60),
                "source_url": url, "note": _clean_text(data.get("note"), 300)}
    key = data.get("award_key")
    if key not in AWARDS:
        raise ValueError("الجائزة غير معروفة")
    if data.get("result") not in RESULTS:
        raise ValueError("النتيجة غير معروفة")
    name = _clean_text(data.get("award_name"))
    if key == "other" and not name:
        raise ValueError("اكتب اسم الجائزة")
    row = {"award_key": key, "award_name": name if key == "other" else AWARDS[key], "result": data["result"],
           "year": year, "source_url": url, "note": _clean_text(data.get("note"), 300)}
    if table == "author_awards":
        row["work_title"] = _clean_text(data.get("work_title"))
    else:
        row["category"] = _clean_text(data.get("category"), 80)
    return row


def add(table, owner_id, data, source, created_by=None, conn=None):
    """Insert one validated row; owner_id is book_id (book tables) or author_id (author_awards).
    Exact duplicates (same owner, key/kind, result, year, title) are skipped. -> row_id or None."""
    if table not in ("book_awards", "author_awards", "book_adaptations"):
        raise ValueError("table")
    row = validate(table, data)
    owner = "author_id" if table == "author_awards" else "book_id"
    own = conn is None
    conn = conn or get_connection()
    try:
        ensure_table(conn)
        if table == "book_adaptations":
            dup = conn.execute("SELECT row_id FROM book_adaptations WHERE book_id=? AND kind=? AND IFNULL(year,0)=IFNULL(?,0) "
                               "AND IFNULL(title,'')=IFNULL(?,'')", (owner_id, row["kind"], row["year"], row["title"])).fetchone()
        else:
            dup = conn.execute(f"SELECT row_id FROM {table} WHERE {owner}=? AND award_key=? AND result=? AND IFNULL(year,0)=IFNULL(?,0) "
                               "AND IFNULL(award_name,'')=IFNULL(?,'')", (owner_id, row["award_key"], row["result"], row["year"], row["award_name"])).fetchone()
        if dup:
            return None
        row.update({owner: owner_id, "source": source, "created_by": created_by, "created_at": _now()})
        cols = ", ".join(row)
        cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))
        if own:
            conn.commit()
        clear_cache()
        return cur.lastrowid
    finally:
        if own:
            conn.close()


def delete(table, row_id, owner_id=None):
    """-> the deleted row (dict) or None (also when it belongs to another book / author than owner_id).
    The admin route records it in the audit log."""
    if table not in ("book_awards", "author_awards", "book_adaptations"):
        raise ValueError("table")
    conn = get_connection()
    try:
        ensure_table(conn)
        r = conn.execute(f"SELECT * FROM {table} WHERE row_id=?", (row_id,)).fetchone()
        owner = "author_id" if table == "author_awards" else "book_id"
        if r is None or (owner_id is not None and r[owner] != owner_id):
            return None
        conn.execute(f"DELETE FROM {table} WHERE row_id=?", (row_id,))
        conn.commit()
        clear_cache()
        return dict(r)
    finally:
        conn.close()


def rows_for_book(book_id):
    """Rows for the admin book card (with ids, sources and labels)."""
    d = load(0)
    aw = [dict(r, label=award_label(r)) for r in d["book"].get(book_id, [])]
    ad = [dict(r, label=adaptation_label(r)) for r in d["adapt"].get(book_id, [])]
    auth = [dict(r, label=award_label(r, True)) for aid in d["book_authors"].get(book_id, []) for r in d["author"].get(aid, [])]
    return {"awards": aw, "adaptations": ad, "author_awards": auth}


def rows_for_author(author_id):
    return [dict(r, label=award_label(r, True)) for r in load(0)["author"].get(author_id, [])]


def options():
    return {"awards": [{"value": k, "label": v} for k, v in AWARDS.items()],
            "results": [{"value": k, "label": v} for k, v in RESULTS.items()],
            "kinds": [{"value": k, "label": v} for k, v in KINDS.items()]}


AWARDS_EN = {"ipaf": "International Prize for Arabic Fiction (Arabic Booker)", "booker": "Booker Prize",
             "intl_booker": "International Booker Prize", "nobel": "Nobel Prize in Literature",
             "zayed": "Sheikh Zayed Book Award", "katara": "Katara Prize for Arabic Novel",
             "mahfouz": "Naguib Mahfouz Medal for Literature", "sharjah": "Sharjah Award for Arab Creativity",
             "pulitzer": "Pulitzer Prize", "goncourt": "Prix Goncourt", "hugo": "Hugo Award", "other": "another prize"}
_EN_VERBS = [("مؤلفه فاز ب", "Its author won "), ("فاز ب", "Won "),
             ("وصل إلى القائمة القصيرة لل", "Shortlisted for ال"), ("وصل إلى القائمة القصيرة ل", "Shortlisted for "),
             ("وصل إلى القائمة الطويلة لل", "Longlisted for ال"), ("وصل إلى القائمة الطويلة ل", "Longlisted for "),
             ("رُشّح لل", "Nominated for ال"), ("رُشّح ل", "Nominated for "), ("له صلة بـ", "Listed for "),
             ("حائز على ", "Won "), ("المؤلف: ", "Author: "), ("تحوّل إلى ", "Adapted into ")]
_EN_WORDS = [(" أو ", " or "), ("فيلم رسوم متحركة", "an animated film"), ("فيلم تلفزيوني", "a TV film"), ("مسلسل", "a series"),
             ("مسرحية", "a play"), ("عمل مرئي", "a screen work"), ("فيلم", "a film"), ("جائزة", "a prize")]


def to_english(text):
    """English wording of a prize / adaptation label (titles and other prizes' names stay as stored)."""
    t = str(text or "")
    for ar, en in _EN_VERBS:
        t = t.replace(ar, en)
    for k, ar in AWARDS.items():
        t = t.replace(ar, AWARDS_EN.get(k, ar))
    for k, ar in RESULTS.items():
        t = t.replace(": " + ar, ": " + {"winner": "winner", "shortlist": "shortlisted", "longlist": "longlisted",
                                          "nominated": "nominated"}[k])
    t = t.replace(" عن «", " for «")
    for ar, en in _EN_WORDS:
        t = t.replace(ar, en)
    return t
