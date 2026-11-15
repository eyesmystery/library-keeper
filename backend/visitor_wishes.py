"""«طلبات واقتراحات الزوار» — one admin view of what visitors want the library to have (ADMINWISH, Ibrahim 2026-11-13:
«نبغى نخلي اقتراحات الزوار اوضح في الادارة. سواءا اقتراحات مباشرة او اسئلة عن كتب غير متوفرة»).

A visitor's wish for a book used to sit in four places under two tabs. Here the same book (or author) is ONE row,
whatever way it reached us:
  * candidate_books   «اقتراح مباشر»: «اقترح كتابًا لرفوفنا» on the start page (confirmed outside lookup, or one of our
                      books that is out of stock) — backend/shelf_suggestions.py;
  * visitor_notes     «اقتراح مباشر»: the same form when nothing was found outside, kept as typed («كتاب مقترح للرفوف:
                      …»); any other suggestion (not about a book) is listed apart, under the rows («اقتراحات عامة»);
  * open_questions    «سؤال في المحادثة»: missing_book / missing_author, a title or author asked for in the chat that we
                      did not have (topic / wish / question / award stay in «أسئلة بلا جواب», for the developer);
  * outside_books     «غير متوفر عندنا»: how often Manar looked the title up outside, with the year and publisher found.
                      Only attached to a row that one of the three above made: a lookup alone is not a wish (the chat
                      also looks up books a visitor names as «مثل …»).
Rows are merged by title: the same title key (chat_rules.title_key), or one title starting with the other as a whole
word (outside_books.same_title: «قوة الآن» = «قوة الآن: الدليل إلى التنوير الروحي»); two named authors that differ keep
two books apart. Authors are merged by name (outside_books._same_name).

One status for every row, written to all the rows merged in it (and to the audit log):
  جديد (new) = open · قيد المتابعة (following) · تم توفيره (provided) = done / added · مرفوض (declined).
«متوفر الآن» is checked live against the catalogue each time: a book in stock whose title is the asked one or starts
with it («قوة الآن» -> «قوة الآن: الدليل…»), by the same author when both are known.
Read only, except apply(); library data only (these tables are never in the catalog or a release package).
"""
import json
import re
from datetime import datetime, timezone

import chat_rules
import open_questions
import outside_books
import shelf_suggestions
import visitor_notes
from models import get_connection

STATUSES = ("new", "following", "provided", "declined")
STATUS_AR = {"new": "جديد", "following": "قيد المتابعة", "provided": "تم توفيره", "declined": "مرفوض"}
GENERAL_AR = dict(STATUS_AR, provided="تمت المعالجة")      # a general suggestion is not «provided»
GROUPS = {"open": ("new", "following"), "done": ("provided", "declined"), "all": STATUSES}
SOURCES_AR = {"direct": "اقتراح مباشر", "chat": "سؤال في المحادثة", "outside": "غير متوفر عندنا"}
# unified status -> what each table stores; q = open_questions, n = visitor_notes, c = candidate_books
TO_TABLE = {"q": {"new": "open", "following": "following", "provided": "done", "declined": "declined"},
            "n": {"new": "open", "following": "following", "provided": "done", "declined": "declined"},
            "c": {"new": "open", "following": "following", "provided": "added", "declined": "declined"}}
FROM_TABLE = {t: {v: k for k, v in m.items()} for t, m in TO_TABLE.items()}
TABLES = {"q": ("open_questions", "q_id", "kind IN ('missing_book', 'missing_author')"),
          "n": ("visitor_notes", "note_id", "kind = 'suggestion'"),
          "c": ("candidate_books", "candidate_id", None)}
# the visitor page's words for a book suggested as typed (frontend/app.js suggestionNote)
PREFIXES = ("كتاب مقترح للرفوف:", "Book suggested for the shelves:")
MAX_NOTE = 500
MAX_REFS = 300
_REF = re.compile(r"^([qnc]):(\d{1,10})$")
_LATIN = re.compile(r"[A-Za-z]")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_suggestion(note):
    """«كتاب مقترح للرفوف: العنوان — المؤلف» -> (title, author or None); any other text -> None."""
    text = " ".join(str(note or "").split())
    for p in PREFIXES:
        if text.startswith(p):
            rest = text[len(p):].strip()
            title, _, author = rest.partition(" — ")
            title = title.strip()
            if len(outside_books.norm(title)) >= 2:
                return title, (author.strip() or None)
    return None


def _loads(v):
    try:
        out = json.loads(v or "[]")
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []


def _ensure(conn):
    open_questions.ensure_table(conn)
    visitor_notes.ensure_table(conn)
    shelf_suggestions.ensure_table(conn)
    outside_books.ensure_table(conn)


# ---------------------------------------------------------------- reading
def _entries(conn):
    """-> (wish entries, general suggestions). An entry is one stored row about one book or author."""
    out, general = [], []
    for r in conn.execute("SELECT * FROM candidate_books"):
        d = dict(r)
        aliases = [d["title"]] + ([d["asked_as"]] if d.get("asked_as") else [])
        out.append({"ref": f"c:{d['candidate_id']}", "kind": "book", "source": "direct", "asked": d.get("asked_as") or d["title"],
                    "found_title": d["title"], "author": d.get("author"), "aliases": aliases,
                    "times": int(d.get("times_suggested") or 1), "first_at": d["first_at"], "last_at": d["last_at"],
                    "status": FROM_TABLE["c"].get(d["status"], "new"), "staff_note": d.get("staff_note"),
                    "handled_by": d.get("handled_by"), "handled_at": d.get("handled_at"),
                    "book_id": d.get("book_id") if d.get("source") == "catalog" else None,
                    "year": d.get("year"), "publisher": d.get("publisher"), "isbn": d.get("isbn"),
                    "outside_key": d.get("outside_key"), "visitor_notes": _loads(d.get("notes")),
                    "sources": _loads(d.get("sources")), "summary": d.get("summary")})
    for r in conn.execute("SELECT * FROM open_questions WHERE kind IN ('missing_book', 'missing_author')"):
        d = dict(r)
        out.append({"ref": f"q:{d['q_id']}", "kind": "author" if d["kind"] == "missing_author" else "book", "source": "chat",
                    "asked": d["phrase"], "author": None, "aliases": [d["phrase"]], "times": int(d.get("times") or 1),
                    "first_at": d["first_at"], "last_at": d["last_at"], "status": FROM_TABLE["q"].get(d["status"], "new"),
                    "staff_note": d.get("staff_note"), "handled_by": d.get("handled_by"), "handled_at": d.get("handled_at")})
    for r in conn.execute("SELECT * FROM visitor_notes WHERE kind = 'suggestion'"):
        d = dict(r)
        status = FROM_TABLE["n"].get(d["status"], "new")
        parsed = parse_suggestion(d.get("note"))
        if parsed is None:
            general.append({"ref": f"n:{d['note_id']}", "note": d.get("note") or "", "created_at": d["created_at"],
                            "language": d.get("language"), "status": status, "status_label": GENERAL_AR[status],
                            "staff_note": d.get("staff_note"), "handled_by": d.get("handled_by"), "handled_at": d.get("handled_at")})
            continue
        title, author = parsed
        out.append({"ref": f"n:{d['note_id']}", "kind": "book", "source": "direct", "asked": title, "author": author,
                    "aliases": [title], "times": 1, "first_at": d["created_at"], "last_at": d["created_at"], "status": status,
                    "staff_note": d.get("staff_note"), "handled_by": d.get("handled_by"), "handled_at": d.get("handled_at")})
    return out, general


def _script_differs(a, b):
    return bool(_LATIN.search(a)) != bool(_LATIN.search(b))


def _authors_conflict(a, b):
    """Two named authors in one script that are not the same person keep two books with one title apart."""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b or _script_differs(a, b):
        return False
    return not outside_books._same_name(a, b)


def _title_match(a, f):
    """Normalized titles (outside_books.norm): the same, or one starts with the other as whole words (6+ letters)."""
    return bool(a and f) and (a == f or (f.startswith(a + " ") and len(a) >= 6) or (a.startswith(f + " ") and len(f) >= 6))


class _Union:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def join(self, i, j):
        a, b = self.find(i), self.find(j)
        if a != b:
            self.p[max(a, b)] = min(a, b)


def _merge(entries):
    """-> lists of entries, one list per book / author."""
    for e in entries:
        e["_norms"] = {outside_books.norm(x) for x in e["aliases"] if x} - {""}
        e["_keys"] = {chat_rules.title_key(x) for x in e["aliases"] if x} - {""}
    u = _Union(len(entries))
    books = [i for i, e in enumerate(entries) if e["kind"] == "book"]
    # a whole-word prefix shares the first word: compare within those buckets only
    buckets, by_key = {}, {}
    for i in books:
        for n in entries[i]["_norms"]:
            buckets.setdefault(n.split()[0], set()).add(i)
        for k in entries[i]["_keys"]:
            by_key.setdefault(k, set()).add(i)
    pairs = set()
    for group in list(buckets.values()) + list(by_key.values()):
        g = sorted(group)
        for x in range(len(g)):
            for y in range(x + 1, len(g)):
                pairs.add((g[x], g[y]))
    for i, j in sorted(pairs):
        a, b = entries[i], entries[j]
        if _authors_conflict(a.get("author"), b.get("author")):
            continue
        if a["_keys"] & b["_keys"] or any(_title_match(x, y) for x in a["_norms"] for y in b["_norms"]):
            u.join(i, j)
    authors = [i for i, e in enumerate(entries) if e["kind"] == "author"]
    for x in range(len(authors)):
        for y in range(x + 1, len(authors)):
            a, b = entries[authors[x]], entries[authors[y]]
            if a["_norms"] & b["_norms"] or outside_books._same_name(a["asked"], b["asked"]):
                u.join(authors[x], authors[y])
    groups = {}
    for i, e in enumerate(entries):
        groups.setdefault(u.find(i), []).append(e)
    return list(groups.values())


def _group_status(statuses):
    """A new request reopens a row; otherwise the furthest state the staff gave it."""
    s = set(statuses)
    for st in ("new", "following", "provided", "declined"):
        if st in s:
            return st
    return "new"


def _outside_rows(conn):
    rows = {}
    for r in conn.execute("SELECT key, asked_title, status, title, author, year, publisher, sources, times_asked FROM outside_books"):
        d = dict(r)
        d["sources"] = _loads(d.get("sources"))
        rows[d["key"]] = d
    return rows


def _source_names(urls):
    out = []
    for u in urls or []:
        u = str(u)
        name = ("Goodreads" if "goodreads" in u else "ويكيبيديا" if "wikipedia" in u else "ويكي بيانات" if "wikidata" in u
                else "Open Library" if "openlibrary" in u else u if not u.startswith("http") else None)
        if name and name not in out:
            out.append(name)
    return out


def _row(entries, outside):
    entries = sorted(entries, key=lambda e: (-e["times"], e["first_at"] or ""))
    kind = entries[0]["kind"]
    sources = {}
    for e in entries:
        sources[e["source"]] = sources.get(e["source"], 0) + e["times"]
    cand = next((e for e in entries if e["ref"].startswith("c:")), None)
    author = next((e["author"] for e in entries if e.get("author")), None)
    row = {"key": "|".join(sorted(e["ref"] for e in entries)), "refs": sorted(e["ref"] for e in entries), "kind": kind,
           "kind_label": "مؤلف" if kind == "author" else "كتاب", "title": entries[0]["asked"], "author": author,
           "found_title": cand["found_title"] if cand and outside_books.norm(cand["found_title"]) != outside_books.norm(entries[0]["asked"]) else None,
           "asked_as": sorted({e["asked"] for e in entries} - {entries[0]["asked"]}),
           "total": sum(e["times"] for e in entries),
           "first_at": min(e["first_at"] for e in entries if e.get("first_at")),
           "last_at": max(e["last_at"] for e in entries if e.get("last_at")),
           "status": _group_status([e["status"] for e in entries]),
           "staff_note": None, "handled_by": None, "handled_at": None,
           "catalog_book_id": next((e["book_id"] for e in entries if e.get("book_id")), None),
           "visitor_notes": [n for e in entries for n in e.get("visitor_notes") or []][-10:],
           "year": next((e["year"] for e in entries if e.get("year")), None),
           "publisher": next((e["publisher"] for e in entries if e.get("publisher")), None),
           "isbn": next((e["isbn"] for e in entries if e.get("isbn")), None),
           "summary": (cand or {}).get("summary"), "info_sources": _source_names((cand or {}).get("sources")), "outside": None,
           "outside_title": None}
    # the staff note and who handled it: from the row written last
    last = max(entries, key=lambda e: (e.get("handled_at") or "", bool(e.get("staff_note"))))
    row["handled_by"], row["handled_at"] = last.get("handled_by"), last.get("handled_at")
    row["staff_note"] = next((e["staff_note"] for e in sorted(entries, key=lambda e: e.get("handled_at") or "", reverse=True)
                              if e.get("staff_note")), None)
    if kind == "book":
        keys = {e.get("outside_key") for e in entries if e.get("outside_key")} | {outside_books.key_of(a) for e in entries for a in e["aliases"]}
        found = [outside[k] for k in sorted(keys) if k in outside]
        if found:
            hit = next((f for f in found if f.get("status") == "found"), found[0])
            asked = sum(int(f.get("times_asked") or 0) for f in found)
            row["outside"] = {"times": asked, "found": hit.get("status") == "found", "title": hit.get("title"),
                              "author": hit.get("author"), "year": hit.get("year"), "publisher": hit.get("publisher"),
                              "sources": _source_names(hit.get("sources"))}
            # the title found outside, when it says more than the asked one («قوة الآن: الدليل إلى التنوير الروحي»)
            ot = hit.get("title") if hit.get("status") == "found" else None
            if ot and outside_books.norm(ot) not in {outside_books.norm(row["title"]), outside_books.norm(row["found_title"] or "")}:
                row["outside_title"] = ot
            if asked:
                sources["outside"] = asked
            row["author"] = row["author"] or hit.get("author")
            row["year"] = row["year"] or hit.get("year")
            row["publisher"] = row["publisher"] or hit.get("publisher")
            row["info_sources"] = row["info_sources"] or row["outside"]["sources"]
    row["sources"] = [{"key": k, "label": SOURCES_AR[k], "times": sources[k]} for k in ("direct", "chat", "outside") if k in sources]
    row["status_label"] = STATUS_AR[row["status"]]
    return row


def _stock(rows, books):
    """«متوفر الآن», checked live: books: dicts with book_id, title, author and available (bool)."""
    here = [b for b in books if b.get("available")]
    by_id = {b["book_id"]: b for b in books}
    idx = {}
    for b in here:
        n = outside_books.norm(b.get("title") or "")
        if n:
            idx.setdefault(n.split()[0], []).append((n, chat_rules.title_key(b.get("title") or ""), b))
    names = None
    for row in rows:
        hits = []
        if row["kind"] == "book":
            b = by_id.get(row.get("catalog_book_id"))
            if b and b.get("available"):
                hits.append(b)
            for alias in [row["title"]] + row["asked_as"] + ([row["found_title"]] if row.get("found_title") else []):
                a, k = outside_books.norm(alias), chat_rules.title_key(alias)
                if len(a) < 2:
                    continue
                for n, bk, b in idx.get(a.split()[0], []):
                    # the book's title is the asked one, or starts with it («قوة الآن» -> «قوة الآن: الدليل…»); never the
                    # other way («اللؤلؤة» is not «اللؤلؤة التي كسرت محارتها»)
                    if not (n == a or bk == k or (len(a) >= 6 and n.startswith(a + " "))):
                        continue
                    if row.get("author") and _authors_conflict(row["author"], b.get("author")):
                        continue
                    hits.append(b)
        else:
            want = outside_books._name_words(row["title"])
            if want:
                if names is None:   # each book's author names, split and normalized once
                    names = [(b, [(x, set(outside_books._name_words(x))) for x in re.split(r"[،,؛;/&]| و ", b.get("author") or "")
                                  if x.strip()]) for b in here]
                for b, parts in names:
                    if any(want[-1] in words and outside_books._same_name(row["title"], x) for x, words in parts):
                        hits.append(b)
        seen, out = set(), []
        for b in hits:
            if b["book_id"] not in seen:
                seen.add(b["book_id"])
                out.append({"book_id": b["book_id"], "title": b.get("title"), "author": b.get("author")})
        row["in_stock_now"] = out[:3]
        row["in_stock_count"] = len(out)
        row["suggest_provided"] = bool(out) and row["status"] in GROUPS["open"]
    return rows


def _collect():
    conn = get_connection()
    try:
        _ensure(conn)
        entries, general = _entries(conn)
        outside = _outside_rows(conn)
    finally:
        conn.close()
    rows = [_row(g, outside) for g in _merge(entries)]
    return rows, general


def _counts(items):
    c = {s: 0 for s in STATUSES}
    for it in items:
        c[it["status"]] = c.get(it["status"], 0) + 1
    c["open"], c["done"], c["all"] = c["new"] + c["following"], c["provided"] + c["declined"], len(items)
    return c


def build(status="open", kind="all", books=None, limit=300, offset=0):
    """The admin list. status: open (new + following) | done (provided + declined) | all | one of STATUSES;
    kind: all | book | author; books: the catalogue for «متوفر الآن» (None = not checked)."""
    want = GROUPS.get(status) or ((status,) if status in STATUSES else GROUPS["open"])
    rows, general = _collect()
    counts, general_counts = _counts(rows), _counts(general)
    items = [r for r in rows if r["status"] in want and (kind not in ("book", "author") or r["kind"] == kind)]
    # what waits first (new, then being followed), the most asked first, the latest first
    items.sort(key=lambda r: r["last_at"] or "", reverse=True)
    items.sort(key=lambda r: (STATUSES.index(r["status"]), -r["total"]))
    total = len(items)
    offset = max(0, int(offset or 0))
    page = items[offset:offset + max(1, min(int(limit or 300), 500))]
    if books is not None:
        _stock(page, books)
    gen = sorted((g for g in general if g["status"] in want), key=lambda g: g["created_at"] or "", reverse=True)
    gen.sort(key=lambda g: STATUSES.index(g["status"]))
    return {"items": page, "total": total, "offset": offset, "has_more": offset + len(page) < total,
            "counts": counts, "general": gen[:200], "general_total": len(gen), "general_counts": general_counts,
            "statuses": STATUS_AR, "general_statuses": GENERAL_AR, "sources": SOURCES_AR}


def counts():
    """For the badge: distinct books / authors still open, open general suggestions, and the latest of them."""
    rows, general = _collect()
    open_rows = [r for r in rows if r["status"] in GROUPS["open"]]
    open_gen = [g for g in general if g["status"] in GROUPS["open"]]
    latest = max([r["last_at"] for r in open_rows if r.get("last_at")] + [g["created_at"] for g in open_gen if g.get("created_at")],
                 default=None)
    return {"open": len(open_rows), "new": sum(1 for r in open_rows if r["status"] == "new"),
            "general_open": len(open_gen), "attention": len(open_rows) + len(open_gen), "latest": latest}


# ---------------------------------------------------------------- writing
def parse_refs(refs):
    """["c:1", "q:30", "n:5"] -> [("c", 1), …]; anything else -> ValueError."""
    if not isinstance(refs, list) or not refs or len(refs) > MAX_REFS:
        raise ValueError("refs required")
    out = []
    for r in refs:
        m = _REF.match(str(r))
        if not m:
            raise ValueError("bad ref")
        out.append((m.group(1), int(m.group(2))))
    return list(dict.fromkeys(out))


def clean_note(note):
    return visitor_notes.clean_text(note)[:MAX_NOTE] if isinstance(note, str) else None


def apply(refs, status=None, note=None, username=None):
    """Write one status (unified) and/or one staff note to every stored row of a merged request.
    -> {"updated": [refs], "missing": [refs]}. ValueError on a bad status or refs."""
    pairs = parse_refs(refs)
    if status is not None and status not in STATUSES:
        raise ValueError("invalid status")
    if status is None and note is None:
        raise ValueError("nothing to change")
    note = clean_note(note) if note is not None else None
    now = _now()
    conn = get_connection()
    try:
        _ensure(conn)
        updated, missing = [], []
        for t, rid in pairs:
            table, id_col, cond = TABLES[t]
            where = f"{id_col} = ?" + (f" AND {cond}" if cond else "")
            if conn.execute(f"SELECT 1 FROM {table} WHERE {where}", (rid,)).fetchone() is None:
                missing.append(f"{t}:{rid}")
                continue
            if status is not None:
                stored = TO_TABLE[t][status]
                handled = status != "new"
                conn.execute(f"UPDATE {table} SET status = ?, handled_by = ?, handled_at = ? WHERE {where}",
                             (stored, username if handled else None, now if handled else None, rid))
            if note is not None:
                conn.execute(f"UPDATE {table} SET staff_note = ? WHERE {where}", (note or None, rid))
            updated.append(f"{t}:{rid}")
        conn.commit()
    finally:
        conn.close()
    return {"updated": updated, "missing": missing}
