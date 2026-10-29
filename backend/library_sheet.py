"""The library's own book sheet (Thalath's Excel: shelf code, publisher, title, author, category,
keywords, description) linked to our books — Ibrahim 2026-10-11 22:52, track SHELF.

His decisions for this sheet:
  1. the shelf code is the book's location; the place's category is added to it («روايات · الرف R04»);
     it will be checked later in the library;
  2. an empty publisher is filled from the sheet (the sheet comes from the library itself); a publisher
     that differs is only reported, he decides;
  3. an empty genre is filled from the library's category;
  4. the sheet's keywords are adopted for search and for linking books (books.keywords);
  5. the descriptions are NOT written here: they are rewritten as our own summaries after checking
     (research work, not this importer);
  6. the import can be repeated whenever the library sends a new sheet.

Matching (no barcode or SKU in the sheet): title + publisher, then title + author, then a title that
only one of our books has — «sure»; then, inside the same publisher, a very close title (≥ 0.85) or a
title that begins with the other — «probable», listed for checking. Volume numbers must agree.
Columns are found by their content (the sheet's headers are mostly «Column N»), so a reordered sheet
still works. Dry run first; apply = backup + one transaction + an import_runs record (source
'library_sheet', shown in the staff inbox file). No third-party dependency.
"""
import difflib
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone

import models
import rewaa_import
from models import get_connection

SOURCE = "library_sheet"
UPLOAD_TTL = 30 * 60
_uploads = {}

# The place each shelf letter holds, read from what the books on those shelves are (2026-10-11, to be
# checked in the library). A letter that is not listed gets the code alone.
PLACES = {
    "A": "تاريخ", "B": "سير ورحلات", "C": "شعر وأدب", "D": "فلسفة", "E": "فكر", "F": "فكر ودراسات",
    "G": "فكر وسياسة", "H": "روايات إنجليزية", "I": "روايات إنجليزية", "J": "كتب إنجليزية",
    "K": "روايات", "L": "روايات", "M": "روايات", "N": "روايات", "O": "روايات", "P": "روايات",
    "Q": "روايات", "R": "روايات", "S": "قصص مصورة وروايات", "Kids": "أطفال ويافعين",
    "XC": "متنوعات", "XD": "تطوير الذات وعلم النفس", "XG": "علوم وعلم نفس", "XH": "كتب إنجليزية",
}
GENRES = ("رواية", "قصص قصيرة", "أطفال", "يافعين", "تطوير ذاتي", "علم نفس", "تاريخ", "سيرة ذاتية", "دين",
          "فكر", "علوم", "أعمال", "شعر", "مسرحية", "أدب رحلات", "قصص مصورة", "مقالات ونصوص", "نقد أدبي",
          "فنون وسينما", "علم اجتماع")
GENRE_FORMAT = {"رواية": "novel", "قصص قصيرة": "short_stories", "أطفال": "children", "يافعين": "young_adult",
                "تطوير ذاتي": "self_dev", "علم نفس": "psychology", "تاريخ": "history", "سيرة ذاتية": "biography",
                "دين": "religious", "فكر": "thought", "علوم": "science", "أعمال": "business", "شعر": "poetry",
                "مسرحية": "play", "أدب رحلات": "travel", "قصص مصورة": "comics", "مقالات ونصوص": "essays",
                "نقد أدبي": "criticism", "فنون وسينما": "arts", "علم اجتماع": "sociology"}
MAX_KEYWORDS = 8


# ------------------------------------------------------------------ text helpers

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NOISE = {"دار", "منشورات", "مكتبه", "للنشر", "والتوزيع", "و", "الناشر"}
_PUB_ALIAS = {"afaq": "افاق", "tanweer": "تنوير"}


def _norm(text):
    s = str(text or "").translate(_DIGITS)
    if re.fullmatch(r"\d+\.0", s.strip()):
        s = s.strip()[:-2]
    s = re.sub(r"[ً-ْـ]", "", s)
    s = re.sub("[إأآٱ]", "ا", s).replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي").replace("ی", "ي").replace("ک", "ك").replace("ۀ", "ه")
    s = re.sub(r"[^\w\s]", " ", s.lower())
    words = [w for w in s.split() if w not in _NOISE]
    words = [w[2:] if w.startswith("ال") and len(w) > 4 else w for w in words]
    return " ".join(_PUB_ALIAS.get(w, w) for w in words)


def key(text):
    return _norm(text).replace(" ", "")


def numbers(text):
    return set(re.findall(r"\d+", str(text or "").translate(_DIGITS)))


def shelf_code(value):
    """«R04» / «r4» / «CO3» / «Kids01» -> normalized code, or None for anything else (a bare number
    such as «319» is not a shelf: it is left out and listed)."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(value or "").translate(_DIGITS))   # «.XC01»، «٫C05»: stray marks dropped
    m = re.fullmatch(r"([A-Za-z]+?)-?([0-9Oo]{1,2})", s)
    if not m:
        return None
    letters, digits = m.group(1), m.group(2).replace("O", "0").replace("o", "0")
    letters = "Kids" if letters.lower() == "kids" else letters.upper()
    if letters != "Kids" and len(letters) > 2:
        return None
    return letters + digits.zfill(2)


def place_of(code):
    return PLACES.get(re.match(r"[A-Za-z]+", code).group(0)) if code else None


def location_text(codes):
    """[«R04»] -> «روايات · الرف R04»; two shelves -> «روايات · الرف K02 / K05»."""
    codes = list(dict.fromkeys(c for c in codes if c))
    if not codes:
        return None
    place = place_of(codes[0])
    shelf = "الرف " + " / ".join(codes)
    return f"{place} · {shelf}" if place else shelf


_SHELF_WORD = re.compile(r"(?:^|(?<=\s))(?:ال)?رف(?:وف)?(?=\s|$)")


def normalize_location(text):
    """BKREV (Ibrahim 2026-10-29: «لما اعدل موقع الكتاب واحطه في الرف … احط الحروف بس لكن ابغاك ترجع تحط اسم القسم»):
    a place typed as shelf codes only — «R02», «q4», «R02 / R05», «الرف R02» — gets its place name back from PLACES:
    «روايات · الرف R02». Text already written as «<مكان> · …», or anything that is not only shelf codes
    («رف ٣ – الطابق الأرضي»), is kept as typed (spaces trimmed)."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s or "·" in s:
        return s
    parts = [p for p in re.split(r"[\s/،,+]+", _SHELF_WORD.sub(" ", s)) if p]
    codes = [shelf_code(p) for p in parts]
    if not parts or not all(codes):
        return s
    return location_text(codes) or s


def normalize_saved_locations(conn):
    """Startup pass (models.init_db): every saved place that is shelf codes only gets its place name, as
    normalize_location does for a new edit — so the places staff typed before this update are fixed too. Writes one
    audit_log row «shelf.normalized» (before → after) when it changed anything. -> number of books changed."""
    try:
        rows = conn.execute("SELECT book_id, shelf_location FROM books WHERE COALESCE(shelf_location, '') <> ''").fetchall()
    except sqlite3.OperationalError:
        return 0
    changed = []
    for book_id, loc in rows:
        new = normalize_location(loc)
        if new and new != loc:
            changed.append([book_id, loc, new])
    if not changed:
        return 0
    conn.executemany("UPDATE books SET shelf_location = ? WHERE book_id = ?", [(n, b) for b, _, n in changed])
    try:
        conn.execute("INSERT INTO audit_log (at, username, action, target, details) VALUES (?, ?, ?, ?, ?)",
                     (datetime.now(timezone.utc).isoformat(timespec="seconds"), "منار (تلقائي)", "shelf.normalized",
                      str(len(changed)), json.dumps({"count": len(changed), "books": changed[:300]}, ensure_ascii=False)))
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return len(changed)


def keywords_of(value):
    out, seen = [], set()
    for part in re.split(r"[،,؛;/|\n]+|\s+[-–]\s+", str(value or "")):   # «دمشق - حب - حرب» too
        w = re.sub(r"\s+", " ", part).strip(" .-–\"'«»")
        k = key(w)
        if not w or not k or len(w) > 40 or k in seen:
            continue
        seen.add(k)
        out.append(w)
        if len(out) >= MAX_KEYWORDS:
            break
    return out


# The library's free-text category («رواية (أدب عالمي)»، «6 -9 Years»، «فلسفة») -> one of our genres.
# Order matters («رواية يافعين» is young adult, «سيرة روائية» is a novel). Anything unclear -> None.
_GENRE_RULES = [
    ("أطفال", r"years|سنوات|اطفال|طفل|kids|children|picture book"),
    ("يافعين", r"يافع|young adult|teen"),
    ("قصص مصورة", r"مصور|كوميكس|كومكس|graphic|manga|مانغا|مانجا|comic"),
    ("قصص قصيرة", r"مجموعه قصصيه|قصص قصيره|^قصص$|^قصص\b(?!.*مصور)|short stor"),
    ("رواية", r"روا[يئ]|novel|thriller|fantasy|sci.?fi|dystopian|^(?!.*non).*fiction|تشويق|رعب"),
    ("شعر", r"شعر|ديوان|poetry|poem"),
    ("مسرحية", r"مسرح|\bplay\b|drama"),
    ("أدب رحلات", r"رحلات|رحله|travel"),
    ("دين", r"سيره نبويه|(^|\s)دين|ديني|اسلام|فقه|قران|عقيده|religio"),
    ("سيرة ذاتية", r"سيره|سير\b|مذكرات|memoir|biograph"),
    ("نقد أدبي", r"نقد"),
    ("مقالات ونصوص", r"مقالات|نصوص|essay"),
    ("تاريخ", r"تاريخ|history"),
    ("علم نفس", r"علم نفس|نفسي|psycholog"),
    ("تطوير ذاتي", r"تنميه|تطوير|self.?help|self.?dev"),
    ("أعمال", r"اداره|رياده|اقتصاد|business|تسويق|^اعمال$"),
    ("علم اجتماع", r"اجتماع|sociolog"),
    ("فكر", r"سياس|politic|علاقات دوليه"),
    ("فنون وسينما", r"فنون|\bفن\b|سينما|موسيقي|\bart\b|film|cinema"),
    ("علوم", r"علوم|فيزياء|فضاء|احياء|كيمياء|رياضيات|science"),
    ("فكر", r"فلسف|فكر|philosoph|انثروبولوج|استشراق|انسانيات"),
]


def genre_from_category(category):
    c = _norm(category)
    raw = str(category or "").lower()
    if not c:
        return None
    for genre, pattern in _GENRE_RULES:
        if re.search(pattern, c) or re.search(pattern, raw):
            return genre
    return None


# ------------------------------------------------------------------ reading the sheet

_SHELF_LIKE = re.compile(r"^(kids|[a-z]{1,2})\s*-?[0-9o]{1,2}$", re.I)


def find_columns(headers, rows, books):
    """-> {field: column index}. Headers first («المؤلف»، «كلمات مفتاحية»، «وصف»), then content."""
    width = len(headers)
    cols = {i: [r[i] for r in rows if i < len(r) and str(r[i]).strip()] for i in range(width)}
    hk = [_norm(h) for h in headers]
    out = {}

    def by_header(field, words):
        for i, h in enumerate(hk):
            if i not in out.values() and any(w in h for w in words):
                out[field] = i
                return

    by_header("keywords", ["كلمات مفتاحيه", "كلمات", "keywords", "tags"])
    by_header("description", ["وصف", "نبذه", "description", "summary"])
    by_header("author", ["مولف", "المولف", "كاتب", "author"])
    by_header("category", ["تصنيف", "category", "genre", "نوع"])

    def share(i, test):
        vals = cols[i][:600]
        return sum(1 for v in vals if test(v)) / len(vals) if len(vals) >= 5 else 0.0

    titles = {key(b["title"]) for b in books if b.get("title")}
    pubs = {key(b["publisher"]) for b in books if b.get("publisher")}
    authors = {key(b["author"]) for b in books if b.get("author")}
    free = [i for i in range(width) if i not in out.values()]
    tests = [("shelf", lambda v: bool(_SHELF_LIKE.match(str(v).strip())), 0.5),
             ("title", lambda v: key(v) in titles, 0.3),
             ("publisher", lambda v: key(v) in pubs, 0.3),
             ("author", lambda v: key(v) in authors, 0.3)]
    for field, test, need in tests:
        if field in out:
            continue
        scored = sorted(((share(i, test), i) for i in free if i not in out.values()), reverse=True)
        if scored and scored[0][0] >= need:
            out[field] = scored[0][1]
    if "category" not in out:      # short labels that our genre rules understand
        scored = sorted(((share(i, lambda v: len(str(v)) < 40 and genre_from_category(v) is not None), i)
                         for i in free if i not in out.values()), reverse=True)
        if scored and scored[0][0] >= 0.5:
            out["category"] = scored[0][1]
    if "quantity" not in out:
        scored = sorted(((share(i, lambda v: re.fullmatch(r"\d{1,3}(\.0)?", str(v).strip()) is not None), i)
                         for i in free if i not in out.values() and i < (out.get("description", width))), reverse=True)
        if scored and scored[0][0] >= 0.8:
            out["quantity"] = scored[0][1]
    return out


def read_sheet(file_name, data):
    headers, rows = rewaa_import.read_table(file_name, data)
    return headers, rows


# ------------------------------------------------------------------ matching

class Catalog:
    def __init__(self, books):
        self.books = {b["book_id"]: b for b in books}
        self.tp, self.ta, self.t, self.pub = {}, {}, {}, {}
        for b in books:
            t = key(b.get("title"))
            if not t:
                continue
            self.tp.setdefault((t, key(b.get("publisher"))), []).append(b)
            self.ta.setdefault((t, key(b.get("author"))), []).append(b)
            self.t.setdefault(t, []).append(b)
            self.pub.setdefault(key(b.get("publisher")), []).append(b)

    def match(self, title, publisher, author):
        """-> (kind, [books]); kind: tp | ta | t | close | starts | ambiguous | none."""
        t = key(title)
        if not t:
            return "none", []
        p, a = key(publisher), key(author)
        if p and (t, p) in self.tp:
            return "tp", self.tp[(t, p)]          # same title and publisher: every copy (editions kept as they are)
        if a and len(self.ta.get((t, a), [])) == 1:
            return "ta", self.ta[(t, a)]
        if len(self.t.get(t, [])) == 1:
            only = self.t[t][0]
            if p and key(only.get("publisher")) and key(only.get("publisher")) != p \
                    and not (a and key(only.get("author")) == a):
                return "none", []     # same title, another publisher and author: another book («أصل الأنواع»)
            return "t", self.t[t]
        if len(self.t.get(t, [])) > 1:
            return "ambiguous", self.t[t]
        if not p or len(t) < 4:
            return "none", []
        nums = numbers(title)
        pool = [b for b in self.pub.get(p, []) if numbers(b["title"]) == nums]
        best, score = None, 0.0
        for b in pool:
            sm = difflib.SequenceMatcher(None, t, key(b["title"]))
            if sm.real_quick_ratio() < 0.85 or sm.quick_ratio() < 0.85:
                continue
            r = sm.ratio()
            if r > score:
                best, score = b, r
        if best is not None and score >= 0.85:
            return "close", [best]
        if len(t) >= 6:
            starts = [b for b in pool if len(key(b["title"])) >= 6
                      and (t.startswith(key(b["title"])) or key(b["title"]).startswith(t))]
            if len(starts) == 1:
                return "starts", starts
        return "none", []


SURE = ("tp", "ta", "t")
KIND_AR = {"tp": "العنوان والدار", "ta": "العنوان والمؤلف", "t": "العنوان (كتاب واحد بهذا الاسم)",
           "close": "عنوان قريب جدًا في الدار نفسها", "starts": "عنوان يبدأ بالآخر في الدار نفسها"}


def _load_books(conn):
    return [dict(r) for r in conn.execute(
        "SELECT book_id, title, author, publisher, genre, shelf_location, keywords, active, quantity, summary, subjects, hook FROM books")]


_STOP = None


def _content(text):
    global _STOP
    if _STOP is None:
        _STOP = set(_norm("في من على الى عن مع هذا هذه التي الذي كتاب روايه قصه مجموعه حول بين كيف حياه عالم خلال اسلوب").split())
    return {w for w in _norm(text).split() if len(w) >= 3 and w not in _STOP}


def shifted_rows(items, gap=10, run=4):
    """Rows whose description and keywords belong to a neighbouring row (the 2026-10-11 sheet has whole
    shifted blocks). items: [(row, own words, description words)] in sheet order. A row is a candidate when a
    row within two places fits its description clearly better than its own book. Four or more candidates
    close together mark the whole stretch (rows there without our own text cannot be scored); a lone
    candidate counts only when its description shares nothing with its own book."""
    cand, strong, mine_of = [], set(), {}
    for i, (n, own, desc) in enumerate(items):
        mine_of[i] = len(desc & own)
        if not desc:
            continue
        mine = mine_of[i]
        best = max((len(desc & items[j][1]) for j in (i - 2, i - 1, i + 1, i + 2) if 0 <= j < len(items) and j != i), default=0)
        if best >= 2 and best >= mine + 2:
            cand.append(i)
            if mine == 0 and best >= 3:
                strong.add(n)
    out = set(strong)
    k = 0
    while k < len(cand):
        m = k
        while m + 1 < len(cand) and items[cand[m + 1]][0] - items[cand[m]][0] <= gap:
            m += 1
        span = range(cand[k], cand[m] + 1)
        scored = [mine_of[x] for x in span if items[x][2]]
        # a real shifted block: most of its rows share (almost) nothing with their own book; rows that
        # clearly fit their own book (3+ shared words) stay out of it
        if m - k + 1 >= run and scored and sum(1 for v in scored if v <= 1) >= 0.6 * len(scored):
            out |= {items[x][0] for x in span if mine_of[x] < 3}
        k = m + 1
    return out


def build_plan(file_name, headers, rows, conn=None):
    own = conn is None
    conn = conn or get_connection()
    try:
        books = _load_books(conn)
        has_profile = {r[0]: r[1] for r in conn.execute("SELECT book_id, format FROM book_profiles")}
    finally:
        if own:
            conn.close()
    cols = find_columns(headers, rows, books)
    if "title" not in cols:
        raise ValueError("ما قدرنا نلقى عمود عناوين الكتب في الملف")
    cat = Catalog(books)

    def cell(r, field):
        i = cols.get(field)
        return str(r[i]).strip() if i is not None and i < len(r) else ""

    per_book = {}        # book_id -> collected sheet values
    items, row_kws = [], {}
    unmatched, ambiguous, numeric_codes, probable = [], [], [], []
    for n, r in enumerate(rows, start=2):
        title = cell(r, "title")
        if not title:
            continue
        pub, author = cell(r, "publisher"), cell(r, "author")
        kind, found = cat.match(title, pub, author)
        own_book = found[0] if kind not in ("none", "ambiguous") and found else None
        own_text = (" ".join(str(own_book.get(k) or "") for k in ("title", "author", "summary", "subjects", "hook"))
                    if own_book else title + " " + author)
        items.append((n, _content(own_text), _content(cell(r, "description") + " " + cell(r, "keywords"))))
        raw_shelf = cell(r, "shelf")
        code = shelf_code(raw_shelf)
        if raw_shelf and not code:
            numeric_codes.append({"row": n, "title": title, "value": raw_shelf})
        if kind == "none":
            unmatched.append({"row": n, "title": title, "publisher": pub, "shelf": code or raw_shelf})
            continue
        if kind == "ambiguous":
            ambiguous.append({"row": n, "title": title, "publisher": pub,
                              "candidates": [{"book_id": b["book_id"], "publisher": b["publisher"]} for b in found]})
            continue
        if kind not in SURE:
            probable.append({"row": n, "title": title, "publisher": pub, "book_id": found[0]["book_id"],
                             "our_title": found[0]["title"], "kind": kind, "kind_ar": KIND_AR[kind]})
        row_kws[n] = keywords_of(cell(r, "keywords"))
        for b in found:
            v = per_book.setdefault(b["book_id"], {"rows": [], "codes": [], "publishers": [], "categories": [],
                                                   "keywords": [], "kw_rows": [], "kind": kind})
            v["rows"].append(n)
            if code:
                v["codes"].append(code)
            if pub:
                v["publishers"].append(pub)
            cat_value = cell(r, "category")
            if cat_value:
                v["categories"].append(cat_value)
            v["kw_rows"].append(n)
            if kind in SURE:
                v["kind"] = kind if v["kind"] not in SURE else v["kind"]

    shifted = shifted_rows(items)
    title_of = {n: cell(rows[n - 2], "title") for n, _o, _d in items}
    updated, unchanged, publisher_differs, several_shelves, genre_unclear = [], [], [], [], []
    for bid, v in per_book.items():
        b = cat.books[bid]
        changes = {}
        codes = list(dict.fromkeys(v["codes"]))
        if len(codes) > 1:
            several_shelves.append({"book_id": bid, "title": b["title"], "codes": codes})
        loc = location_text(codes)
        if loc and loc != (b["shelf_location"] or ""):
            changes["shelf_location"] = [b["shelf_location"], loc]
        pubs = list(dict.fromkeys(v["publishers"]))
        if pubs and not (b["publisher"] or "").strip():
            changes["publisher"] = [b["publisher"], pubs[0]]
        else:
            for other in [x for x in pubs if key(x) != key(b["publisher"])][:1]:
                publisher_differs.append({"book_id": bid, "title": b["title"], "ours": b["publisher"], "sheet": other,
                                          "rows": v["rows"]})
        if not (b["genre"] or "").strip() and v["categories"]:
            g = next((genre_from_category(c) for c in v["categories"] if genre_from_category(c)), None)
            if g:
                changes["genre"] = [b["genre"], g]
            else:
                genre_unclear.append({"book_id": bid, "title": b["title"], "category": v["categories"][0]})
        good = [n for n in v["kw_rows"] if n not in shifted]
        kws = keywords_of("،".join(k for n in good for k in row_kws.get(n, [])))
        old_kws = _json_list(b.get("keywords"))
        if kws and kws != old_kws:
            changes["keywords"] = [old_kws or None, kws]
        elif not kws and old_kws and v["kw_rows"] and not good:
            changes["keywords"] = [old_kws, None]      # its only rows are shifted: their words are another book's
        item = {"book_id": bid, "title": b["title"], "matched_by": KIND_AR[v["kind"]], "kind": v["kind"],
                "has_profile": bid in has_profile, "format": has_profile.get(bid)}
        if changes:
            updated.append(dict(item, changes=changes))
        else:
            unchanged.append(item)
    in_stock = [b for b in books if b["active"] and (b["quantity"] or 0) > 0]
    not_in_sheet = [{"book_id": b["book_id"], "title": b["title"], "publisher": b["publisher"]}
                    for b in in_stock if b["book_id"] not in per_book]
    count = lambda f: sum(1 for u in updated if f in u["changes"])
    return {
        "file_name": file_name, "columns": {f: headers[i] for f, i in cols.items()}, "column_index": cols,
        "rows_read": len(rows), "matched_books": len(per_book),
        "sure": sum(1 for v in per_book.values() if v["kind"] in SURE), "probable": probable,
        "updated": updated, "unchanged": unchanged, "unmatched": unmatched, "ambiguous": ambiguous,
        "numeric_codes": numeric_codes, "several_shelves": several_shelves,
        "publisher_differs": publisher_differs, "genre_unclear": genre_unclear,
        "in_stock_not_in_sheet": not_in_sheet,
        "shifted_rows": [{"row": n, "title": title_of.get(n, "")} for n in sorted(shifted)],
        "counts": {"shelf_location": count("shelf_location"), "publisher": count("publisher"),
                   "genre": count("genre"), "keywords": count("keywords")},
    }


def _json_list(v):
    try:
        x = json.loads(v or "[]")
        return x if isinstance(x, list) else []
    except (TypeError, ValueError):
        return []


# ------------------------------------------------------------------ upload cache (admin page)

def store_upload(file_name, data):
    headers, rows = read_sheet(file_name, data)
    now = time.time()
    for k in [k for k, v in _uploads.items() if now - v["at"] > UPLOAD_TTL]:
        _uploads.pop(k, None)
    plan = build_plan(file_name, headers, rows)
    upload_id = secrets.token_urlsafe(12)
    _uploads[upload_id] = {"at": now, "file_name": file_name, "headers": headers, "rows": rows}
    return dict(summary(plan), upload_id=upload_id)


def summary(plan, cap=400):
    """What the admin page shows: counts and capped lists."""
    out = {k: plan[k] for k in ("file_name", "columns", "rows_read", "matched_books", "sure", "counts")}
    for k in ("probable", "updated", "unmatched", "ambiguous", "numeric_codes", "several_shelves",
              "publisher_differs", "genre_unclear", "in_stock_not_in_sheet", "shifted_rows"):
        out[k] = plan[k][:cap]
        out[k + "_total"] = len(plan[k])
    out["unchanged_total"] = len(plan["unchanged"])
    return out


# ------------------------------------------------------------------ apply

def backup():
    folder = os.path.join(os.path.dirname(models.DB_PATH), "..", "backups")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "library-%s-sheet.db" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    src, out = sqlite3.connect(models.DB_PATH), sqlite3.connect(dest)
    try:
        src.backup(out)
        if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup failed the integrity check")
    finally:
        out.close()
        src.close()
    return os.path.basename(dest)


def apply_plan(plan, applied_by=None):
    """Backup, then one transaction. Returns counts and the backup's name."""
    name = backup()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = get_connection()
    try:
        conn.execute("BEGIN")
        for u in plan["updated"]:
            sets = {}
            for f, (_old, new) in u["changes"].items():
                sets[f] = json.dumps(new, ensure_ascii=False) if (f == "keywords" and new) else new
            conn.execute(f"UPDATE books SET {', '.join(f + ' = ?' for f in sets)} WHERE book_id = ?",
                         (*sets.values(), u["book_id"]))
            if "genre" in u["changes"]:
                conn.execute("UPDATE book_profiles SET format = ? WHERE book_id = ? AND (format IS NULL OR format = '' OR format = 'other')",
                             (GENRE_FORMAT[u["changes"]["genre"][1]], u["book_id"]))
        details = {"updated": [{"book_id": u["book_id"], "title": u["title"],
                                "changes": {f: [_show(a), _show(b)] for f, (a, b) in u["changes"].items()}}
                               for u in plan["updated"]],
                   "publisher_differs": plan["publisher_differs"], "unmatched": len(plan["unmatched"]),
                   "numeric_codes": len(plan["numeric_codes"]), "probable": len(plan["probable"])}
        conn.execute(
            """INSERT INTO import_runs (source, file_name, applied_at, rows_read, added, updated, unchanged,
                                        missing, marked_out_of_stock, invalid, mapping, applied_by, details)
               VALUES (?, ?, ?, ?, 0, ?, ?, ?, 0, ?, ?, ?, ?)""",
            (SOURCE, plan["file_name"], now, plan["rows_read"], len(plan["updated"]), len(plan["unchanged"]),
             len(plan["in_stock_not_in_sheet"]), len(plan["unmatched"]),
             ",".join(f"{k}:{v}" for k, v in plan["column_index"].items()), applied_by,
             json.dumps(details, ensure_ascii=False)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if any("keywords" in u["changes"] for u in plan["updated"]):
        try:
            import relations
            relations.rebuild()
        except Exception:  # relations are rebuilt on every server start anyway
            pass
    return {"applied_at": now, "backup": name, "updated": len(plan["updated"]), **plan["counts"]}


def _show(v):
    if isinstance(v, list):
        return "، ".join(str(x) for x in v)
    return v


def apply_upload(upload_id, applied_by=None):
    up = _uploads.get(upload_id)
    if not up:
        raise ValueError("انتهت صلاحية الملف المرفوع؛ ارفعه مرة ثانية")
    plan = build_plan(up["file_name"], up["headers"], up["rows"])   # re-planned on the current data
    res = apply_plan(plan, applied_by)
    _uploads.pop(upload_id, None)
    return res
