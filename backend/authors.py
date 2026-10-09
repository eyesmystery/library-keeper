"""Author profiles (approved by Ibrahim 2026-09-30; docs/author-profile-proposal.md).

Two tables (created in models.init_db):
  authors       one row per person: canonical Arabic name, Latin name, aliases, researched
                style fields (filled by author research batches) and `catalog_style`, which is
                computed here from our researched book cards.
  book_authors  book <-> person with a role (author / translator / illustrator / editor).

`sync()` splits the free-text `books.author` / `books.translator` strings into people and roles,
merges exact normalized spellings, links books, and recomputes catalog styles. It never edits
`books` and never removes a researched or staff link; probable duplicate spellings are only
reported (`probable_duplicates()`), to be resolved in an author research batch.
"""
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone

from models import get_connection

ROLES = ("author", "translator", "illustrator", "editor")
UNKNOWN = {"غير معروف", "unknown", "مجهول", ""}
RESEARCHED = ("enriched", "needs_review", "approved")
_ROLE_WORDS = {"رسوم": "illustrator", "رسم": "illustrator", "ترجمة": "translator", "تحرير": "editor",
               "إعداد": "editor", "اعداد": "editor", "تقديم": None, "مراجعة": None}
_COMMON_FIRST = {"محمد", "احمد", "عبد", "علي"}
# name words that start with «و» and must not be read as the joining «و» (2026-10-01)
_WAW_WORDS = {"ولد", "وليد", "وائل", "وفاء", "وجدي", "وجيه", "وداد", "وسام", "وضاح", "وعد", "ورد", "ورده",
              "وصفي", "وهيب", "وهبه", "ولاء", "وحيد", "وحيده", "وديع", "ودود", "وسيم", "وسيمه", "وليم", "وليام"}


def key(text):
    """Normalized comparison key: no diacritics/tatweel, unified alef/yaa/taa marbuta, lower case."""
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("ـ", "")
    text = text.translate(str.maketrans("أإآٱىة", "اااايه"))
    return " ".join(re.findall(r"\w+", text))


def _is_latin(text):
    return bool(re.search(r"[A-Za-z]", text)) and not re.search(r"[؀-ۿ]", text)


def _split_names(text):
    """«أ، ب و ج» -> [أ, ب, ج]. A joining «و» splits only between two multi-word names."""
    out = []
    for part in re.split(r"\s*[،,;]\s*|\s+(?:and|&)\s+", text):
        part = part.strip(" .-")
        if not part:
            continue
        pieces = re.split(r"\s+و(?=[^\s])", part)
        if len(pieces) == 2 and key("و" + pieces[1].split()[0]) in _WAW_WORDS:
            pieces = [part]  # «عبد الله ولد محمدي»: «ولد»/«وليد»… is part of the name, not «و» + name
        if len(pieces) == 2 and all(len(p.split()) >= 2 for p in (pieces[0], "و" + pieces[1])) \
                and len(pieces[1].split()) >= 2:
            out += [pieces[0].strip(), pieces[1].strip()]
        else:
            out.append(part)
    return [n for n in out if key(n) and key(n) not in {key(u) for u in UNKNOWN}]


_ROLE_LABELS = {"تأليف": "author", "رسوم": "illustrator", "رسم": "illustrator", "ترجمة": "translator",
                "اختيار وترجمة": "translator", "ترجمة وتقديم": "translator", "ترجمة وتحقيق": "translator",
                "تحرير": "editor", "إعداد": "editor", "اعداد": "editor"}


def parse_people(author_text, translator_text=None):
    """-> [(name, role, position)] from the free-text author (and translator) fields."""
    text = str(author_text or "").strip()
    people = []
    if text and key(text) not in {key(u) for u in UNKNOWN}:
        inners = [i.strip() for i in re.findall(r"\(([^)]*)\)", text)]
        if inners and all(i in _ROLE_LABELS for i in inners):
            # every bracket is a bare role label that applies to the names before it:
            # «سارة المصري (تحرير)», «غارون تسوتشيا (تأليف) ونوبواكي مينيغيشي (رسوم)»
            parts = re.split(r"\(([^)]*)\)", text)
            for i in range(0, len(parts) - 1, 2):
                seg = parts[i].strip(" ،,")
                if i and seg.startswith("و"):  # the joining «و» right after a closing bracket
                    seg = seg[1:].strip()
                people += [(n, _ROLE_LABELS[parts[i + 1].strip()]) for n in _split_names(seg)]
            people += [(n, "author") for n in _split_names(parts[-1].strip(" ،,").lstrip("و").strip())]
        else:
            # «آلان مور (رسوم ديف غيبونز)»: a role label followed by names inside the bracket
            for inner in inners:
                words = inner.split(maxsplit=1)
                role = _ROLE_WORDS.get(words[0]) if words else None
                if role and len(words) == 2:
                    people += [(n, role) for n in _split_names(words[1])]
            base = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", text)).strip()
            people = [(n, "author") for n in _split_names(base)] + people
    people += [(n, "translator") for n in _split_names(str(translator_text or ""))]
    seen, out, count = set(), [], Counter()
    for name, role in people:
        k = (key(name), role)
        if k not in seen:
            seen.add(k)
            out.append((name, role, count[role]))
            count[role] += 1
    return out


def _load(value):
    try:
        v = json.loads(value or "[]")
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def _index(conn):
    """normalized name/alias -> author_id"""
    idx = {}
    for r in conn.execute("SELECT author_id, name_ar, name_latin, aliases FROM authors"):
        for n in [r["name_ar"], r["name_latin"]] + _load(r["aliases"]):
            if n and key(n):
                idx.setdefault(key(n), r["author_id"])
    return idx


def sync(conn=None):
    """Create/link authors from the books' text fields; recompute catalog styles.
    -> {"authors": n, "links": n, "new_authors": n}"""
    own = conn is None
    conn = conn or get_connection()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        idx = _index(conn)
        before = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
        wanted = []
        for b in conn.execute("SELECT book_id, author, translator FROM books"):
            for name, role, pos in parse_people(b["author"], b["translator"]):
                k = key(name)
                if k not in idx:
                    latin = _is_latin(name)
                    cur = conn.execute(
                        """INSERT INTO authors (name_ar, name_latin, aliases, research_status, created_at, updated_at)
                           VALUES (?, ?, '[]', 'pending', ?, ?)""", (name, name if latin else None, now, now))
                    idx[k] = cur.lastrowid
                wanted.append((b["book_id"], idx[k], role, pos))
        # parsed links are rebuilt; research/staff links are kept as they are
        conn.execute("DELETE FROM book_authors WHERE source = 'parsed'")
        conn.executemany("""INSERT OR IGNORE INTO book_authors (book_id, author_id, role, position, source)
                            VALUES (?, ?, ?, ?, 'parsed')""", wanted)
        # parsed leftovers nobody links to any more (e.g. a spelling fixed in the books) are removed;
        # researched or staff-touched records are always kept
        conn.execute("""DELETE FROM authors WHERE research_status = 'pending' AND staff_verified = 0
                        AND aliases = '[]' AND known_for = '[]'
                        AND author_id NOT IN (SELECT author_id FROM book_authors)""")
        _catalog_styles(conn, now)
        conn.commit()
        return {"authors": conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0],
                "links": conn.execute("SELECT COUNT(*) FROM book_authors").fetchone()[0],
                "new_authors": conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0] - before}
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def _catalog_styles(conn, now):
    """An author's usual writing as seen in OUR researched cards of their books (role author)."""
    rows = conn.execute(
        f"""SELECT ba.author_id, b.subgenres, b.page_count, p.format, p.moods, p.pace, p.perceived_length, p.themes
            FROM book_authors ba JOIN books b USING(book_id) JOIN book_profiles p USING(book_id)
            WHERE ba.role = 'author' AND p.overall_status IN ({','.join('?' * len(RESEARCHED))})""", RESEARCHED).fetchall()
    per = {}
    for r in rows:
        per.setdefault(r["author_id"], []).append(r)
    conn.execute("UPDATE authors SET catalog_style = NULL")
    for aid, books in per.items():
        n = len(books)
        def top(counter, limit, min_count=1):
            return [v for v, c in counter.most_common(limit) if c >= min_count]
        need = 1 if n < 3 else 2  # with several books, keep what recurs
        lengths = Counter(r["perceived_length"] for r in books if r["perceived_length"])
        paces = Counter(r["pace"] for r in books if r["pace"] and r["pace"] != "unknown")
        style = {
            "books": n,
            "formats": top(Counter(r["format"] for r in books if r["format"]), 3),
            "subgenres": top(Counter(s for r in books for s in _load(r["subgenres"]) if isinstance(s, str)), 4, need),
            "moods": top(Counter(m for r in books for m in _load(r["moods"]) if m != "unknown"), 3, need),
            "pace": paces.most_common(1)[0][0] if paces else None,
            "typical_length": lengths.most_common(1)[0][0] if lengths else None,
            "themes": top(Counter(t for r in books for t in _load(r["themes"]) if isinstance(t, str)), 4, need),
        }
        conn.execute("UPDATE authors SET catalog_style = ?, updated_at = ? WHERE author_id = ?",
                     (json.dumps(style, ensure_ascii=False), now, aid))


# Spelling-only variants (Ibrahim, 2026-09-30): a suggestion means the SAME name written two ways
# (بارغاس/فارغاس، غارون/جارون، إيتشي/إييتشي، جيلبير/جيلبرت، «ر.» for «رودريغيز»). A different or an
# extra name word is a different person: محمد نور الدين ≠ محمد ناصر الدين، أحمد خالد مصطفى ≠ أحمد خالد
# توفيق، إبراهيم عيسى ≠ إبراهيم أحمد عيسى.
_VOWELS = set("اويه")
_TRANSLIT = [set("بفپ"), set("جغگكق"), set("تط"), set("سصش"), set("زذظ"), set("دض"), set("هح")]
_DISTINCT_NAMES = {frozenset(p) for p in [
    ("حسن", "حسين"), ("سعد", "سعيد"), ("سعد", "سعود"), ("محمد", "محمود"), ("حمد", "حامد"), ("حمد", "حمدي"),
    ("عمر", "عمرو"), ("عمر", "عامر"), ("عمر", "عمير"), ("سلمان", "سليمان"), ("سالم", "سليم"), ("كريم", "كرم"),
    ("نور", "نوري"), ("ناصر", "نصر"), ("جمال", "كمال"), ("صالح", "صلاح"), ("عادل", "عدلي"), ("فايز", "فائز"),
    ("خالد", "خليل"), ("علي", "عالي"), ("احمد", "حامد"), ("منى", "مني"), ("هدى", "هند"), ("رشا", "رشيد"),
    ("عبير", "عبيد"), ("سمير", "سمر"), ("سميره", "سمر"), ("نبيل", "نبيله"), ("جميل", "جميله"), ("سامي", "سامر")]}


def _variant_ops(a, b):
    """Edit operations between two words where each op is weighted: vowel insert/delete or a
    transliteration swap (ب/ف، ج/غ …) costs 1; any other change costs 2."""
    prev = list(range(0, 2 * len(b) + 1, 2))
    for i, ca in enumerate(a, 1):
        cur = [prev[0] + (1 if ca in _VOWELS else 2)]
        for j, cb in enumerate(b, 1):
            sub = 0 if ca == cb else (1 if any(ca in g and cb in g for g in _TRANSLIT) else 2)
            cur.append(min(prev[j] + (1 if ca in _VOWELS else 2), cur[j - 1] + (1 if cb in _VOWELS else 2), prev[j - 1] + sub))
        prev = cur
    return prev[-1]


def _same_word(x, y):
    """Is y the same name word as x, only spelled differently?"""
    if x == y:
        return True
    if frozenset((x, y)) in _DISTINCT_NAMES:
        return False
    if min(len(x), len(y)) == 1:  # an initial: «ر.» for «رودريغيز»
        return x[0] == y[0]
    if min(len(x), len(y)) < 5:  # short words: only a single vowel or transliteration letter
        return _variant_ops(x, y) <= 1
    return _variant_ops(x, y) <= 3


def probable_duplicates(conn=None):
    """Pairs that are probably one person spelled twice (e.g. بارغاس/فارغاس يوسا). Reported only.
    Same number of name words, every word the same or a spelling variant of its counterpart."""
    own = conn is None
    conn = conn or get_connection()
    try:
        rows = [dict(r) for r in conn.execute("SELECT author_id, name_ar FROM authors")]
    finally:
        if own:
            conn.close()
    def cls(ch):  # letters that a transliteration may swap share one bucket
        return next((min(g) for g in _TRANSLIT if ch in g), ch)
    groups = {}
    for r in rows:
        words = key(r["name_ar"]).split()
        if len(words) >= 2:  # same word count and a compatible first letter of the family name
            groups.setdefault((len(words), cls(words[-1][0])), []).append((r, words))
    pairs = []
    for group in groups.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                (a, wa), (b, wb) = group[i], group[j]
                if wa != wb and all(_same_word(x, y) for x, y in zip(wa, wb)):
                    pairs.append((a["author_id"], a["name_ar"], b["author_id"], b["name_ar"]))
    return pairs


def _distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------------------
# Admin «المكتبة» → المؤلفين (2026-09-30): list, detail, simple staff edits and merging
# probable duplicates. Edits never touch `books`; a changed or merged spelling is kept as an
# alias so the next sync() still links the books' original text to the same person.
# ---------------------------------------------------------------------------
_TEXT_FIELDS = ("origin", "pace", "typical_length", "audience", "style_note_ar", "research_note")
_LIST_FIELDS = ("known_for", "formats", "subgenres", "moods", "themes", "source_urls")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _author_row(r, stats, dups):
    s = stats.get(r["author_id"], {})
    style = None
    try:
        style = json.loads(r["catalog_style"]) if r["catalog_style"] else None
    except (TypeError, ValueError):
        pass
    return {"id": r["author_id"], "name": r["name_ar"], "latin": r["name_latin"], "aliases": _load(r["aliases"]),
            "origin": r["origin"], "research_status": r["research_status"], "verified": bool(r["staff_verified"]),
            "roles": s.get("roles", {}), "role_stock": s.get("role_stock", {}), "role_researched": s.get("role_researched", {}),
            "books": s.get("books", 0), "in_stock": s.get("in_stock", 0),
            "researched": s.get("researched", 0), "style": style, "duplicates": dups.get(r["author_id"], []),
            "updated_at": r["updated_at"]}


def _stats(conn, author_id=None):
    where, args = ("WHERE ba.author_id = ?", (author_id,)) if author_id else ("", ())
    out = {}
    for r in conn.execute(f"""
            SELECT ba.author_id, ba.role, COUNT(DISTINCT ba.book_id) n,
                   COUNT(DISTINCT CASE WHEN b.active = 1 AND b.quantity > 0 THEN ba.book_id END) stock,
                   COUNT(DISTINCT CASE WHEN p.overall_status IN ('enriched','needs_review','approved') THEN ba.book_id END) res
            FROM book_authors ba JOIN books b USING(book_id) LEFT JOIN book_profiles p USING(book_id)
            {where} GROUP BY ba.author_id, ba.role""", args):
        s = out.setdefault(r["author_id"], {"roles": {}, "books": 0, "in_stock": 0, "researched": 0})
        s["roles"][r["role"]] = r["n"]
        # per-role counts for the «المترجمون» section (2026-10-03): a person's translations only
        s.setdefault("role_stock", {})[r["role"]] = r["stock"]
        s.setdefault("role_researched", {})[r["role"]] = r["res"]
    for r in conn.execute(f"""
            SELECT ba.author_id, COUNT(DISTINCT ba.book_id) n,
                   COUNT(DISTINCT CASE WHEN b.active = 1 AND b.quantity > 0 THEN ba.book_id END) stock,
                   COUNT(DISTINCT CASE WHEN p.overall_status IN ('enriched','needs_review','approved') THEN ba.book_id END) res
            FROM book_authors ba JOIN books b USING(book_id) LEFT JOIN book_profiles p USING(book_id)
            {where} GROUP BY ba.author_id""", args):
        s = out.setdefault(r["author_id"], {"roles": {}})
        s.update(books=r["n"], in_stock=r["stock"], researched=r["res"])
    return out


def _dup_map(conn):
    dups = {}
    for a, an, b, bn in probable_duplicates(conn):
        dups.setdefault(a, []).append({"id": b, "name": bn})
        dups.setdefault(b, []).append({"id": a, "name": an})
    return dups


def list_authors(conn=None):
    own = conn is None
    conn = conn or get_connection()
    try:
        stats, dups = _stats(conn), _dup_map(conn)
        return [_author_row(r, stats, dups) for r in conn.execute("SELECT * FROM authors ORDER BY author_id")]
    finally:
        if own:
            conn.close()


def author_detail(author_id, conn=None):
    own = conn is None
    conn = conn or get_connection()
    try:
        r = conn.execute("SELECT * FROM authors WHERE author_id = ?", (author_id,)).fetchone()
        if r is None:
            return None
        out = _author_row(r, _stats(conn, author_id), _dup_map(conn))
        out["books_list"] = [dict(b) for b in conn.execute(
            """SELECT b.book_id, b.title, b.publisher, b.genre, b.quantity, b.active, b.cover_url, b.cover_source,
                      b.research_status, ba.role, ba.source link_source, COALESCE(p.overall_status, 'pending') status
               FROM book_authors ba JOIN books b USING(book_id) LEFT JOIN book_profiles p USING(book_id)
               WHERE ba.author_id = ? ORDER BY ba.role, b.title""", (author_id,))]
        for f in _LIST_FIELDS:
            out[f] = _load(r[f])
        for f in _TEXT_FIELDS:
            out[f] = r[f]
        out["confidence"], out["researched_at"] = r["confidence"], r["researched_at"]  # «الأسلوب من البحث» (2026-10-01)
        return out
    finally:
        if own:
            conn.close()


def _clean_name(value, limit=120):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        raise ValueError("الاسم طويل")
    return text


def _taken(conn, names, except_id):
    """Another author already uses one of these spellings (as name or alias)."""
    keys = {key(n) for n in names if key(n)}
    for r in conn.execute("SELECT author_id, name_ar, name_latin, aliases FROM authors WHERE author_id <> ?", (except_id,)):
        for n in [r["name_ar"], r["name_latin"]] + _load(r["aliases"]):
            if n and key(n) in keys:
                return r["name_ar"]
    return None


def update_author(author_id, data, user_name=None):
    """Staff edit: Arabic name, Latin name, aliases, origin, verified flag. -> (before, after) or raises ValueError."""
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM authors WHERE author_id = ?", (author_id,)).fetchone()
        if r is None:
            raise LookupError
        before = {"name_ar": r["name_ar"], "name_latin": r["name_latin"], "aliases": _load(r["aliases"]),
                  "origin": r["origin"], "staff_verified": r["staff_verified"]}
        after = dict(before)
        if "name" in data:
            name = _clean_name(data["name"])
            if not key(name):
                raise ValueError("الاسم العربي مطلوب")
            after["name_ar"] = name
        if "latin" in data:
            after["name_latin"] = _clean_name(data["latin"]) or None
        if "aliases" in data:
            if not isinstance(data["aliases"], list) or len(data["aliases"]) > 20:
                raise ValueError("الأسماء البديلة غير صالحة")
            after["aliases"] = [_clean_name(a) for a in data["aliases"] if _clean_name(a)]
        if "origin" in data:
            after["origin"] = _clean_name(data["origin"], 80) or None
        if "verified" in data:
            after["staff_verified"] = 1 if data["verified"] else 0
        # the previous Arabic spelling stays an alias so the books' text keeps linking here
        if key(after["name_ar"]) != key(before["name_ar"]) and \
                key(before["name_ar"]) not in {key(a) for a in after["aliases"]}:
            after["aliases"].append(before["name_ar"])
        seen, aliases = {key(after["name_ar"])}, []
        for a in after["aliases"]:
            if key(a) and key(a) not in seen:
                seen.add(key(a)); aliases.append(a)
        after["aliases"] = aliases
        clash = _taken(conn, [after["name_ar"], after["name_latin"]] + aliases, author_id)
        if clash:
            raise ValueError(f"هذا الاسم مستخدم للمؤلف «{clash}» — استخدم الدمج بدلًا منه")
        now = _now()
        conn.execute("""UPDATE authors SET name_ar=?, name_latin=?, aliases=?, origin=?, staff_verified=?,
                        verified_by=CASE WHEN ?=1 AND staff_verified=0 THEN ? ELSE verified_by END,
                        verified_at=CASE WHEN ?=1 AND staff_verified=0 THEN ? ELSE verified_at END,
                        updated_at=? WHERE author_id=?""",
                     (after["name_ar"], after["name_latin"], json.dumps(aliases, ensure_ascii=False), after["origin"],
                      after["staff_verified"], after["staff_verified"], user_name, after["staff_verified"], now, now, author_id))
        conn.commit()
        return before, after
    finally:
        conn.close()


def merge_authors(keep_id, drop_id):
    """One person spelled twice: `drop` becomes aliases of `keep`, its links move, its row is removed."""
    if keep_id == drop_id:
        raise ValueError("اختر مؤلفين مختلفين")
    conn = get_connection()
    try:
        keep = conn.execute("SELECT * FROM authors WHERE author_id = ?", (keep_id,)).fetchone()
        drop = conn.execute("SELECT * FROM authors WHERE author_id = ?", (drop_id,)).fetchone()
        if keep is None or drop is None:
            raise LookupError
        names = _load(keep["aliases"]) + [drop["name_ar"], drop["name_latin"]] + _load(drop["aliases"])
        seen, aliases = {key(keep["name_ar"])}, []
        for n in names:
            if n and key(n) and key(n) not in seen:
                seen.add(key(n)); aliases.append(n)
        sets = {"aliases": json.dumps(aliases, ensure_ascii=False),
                "name_latin": keep["name_latin"] or drop["name_latin"]}
        for f in _TEXT_FIELDS:
            sets[f] = keep[f] if keep[f] else drop[f]
        for f in _LIST_FIELDS:
            merged = _load(keep[f]) + [x for x in _load(drop[f]) if x not in _load(keep[f])]
            sets[f] = json.dumps(merged, ensure_ascii=False)
        sets["updated_at"] = now = _now()
        conn.execute(f"UPDATE authors SET {', '.join(k + '=?' for k in sets)} WHERE author_id=?",
                     list(sets.values()) + [keep_id])
        moved = conn.execute("SELECT COUNT(*) FROM book_authors WHERE author_id=?", (drop_id,)).fetchone()[0]
        conn.execute("""INSERT OR IGNORE INTO book_authors (book_id, author_id, role, position, source)
                        SELECT book_id, ?, role, position, source FROM book_authors WHERE author_id=?""", (keep_id, drop_id))
        conn.execute("DELETE FROM book_authors WHERE author_id=?", (drop_id,))
        # the dropped record's prizes move too (same prize/result/year already on `keep` = a duplicate) — 2026-10-09
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='author_awards'").fetchone():
            conn.execute("""DELETE FROM author_awards WHERE author_id=? AND EXISTS (SELECT 1 FROM author_awards k
                            WHERE k.author_id=? AND k.award_key=author_awards.award_key AND k.result=author_awards.result
                            AND IFNULL(k.year,0)=IFNULL(author_awards.year,0)
                            AND IFNULL(k.award_name,'')=IFNULL(author_awards.award_name,''))""", (drop_id, keep_id))
            conn.execute("UPDATE author_awards SET author_id=? WHERE author_id=?", (keep_id, drop_id))
        conn.execute("DELETE FROM authors WHERE author_id=?", (drop_id,))
        # parsed leftovers nobody links to any more (e.g. a spelling fixed in the books) are removed;
        # researched or staff-touched records are always kept
        conn.execute("""DELETE FROM authors WHERE research_status = 'pending' AND staff_verified = 0
                        AND aliases = '[]' AND known_for = '[]'
                        AND author_id NOT IN (SELECT author_id FROM book_authors)""")
        _catalog_styles(conn, now)
        conn.commit()
        return {"kept": keep["name_ar"], "removed": drop["name_ar"], "links_moved": moved}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def find(conn, name):
    """author_id for a name or alias (normalized), or None."""
    return _index(conn).get(key(name))


def unify_display(conn, keep_id, drop_id):
    """Before merging one person spelled twice: replace the variant spelling in the books' displayed
    author/translator text with the kept canonical name (Ibrahim's choice 2026-09-30), so visitors
    see one name. Only the linked books, only exact occurrences. -> [{book_id, author, translator}]"""
    keep = conn.execute("SELECT name_ar FROM authors WHERE author_id = ?", (keep_id,)).fetchone()["name_ar"]
    drop = conn.execute("SELECT name_ar, name_latin, aliases FROM authors WHERE author_id = ?", (drop_id,)).fetchone()
    variants = sorted({n for n in [drop["name_ar"], drop["name_latin"]] + _load(drop["aliases"])
                       if n and key(n) != key(keep)}, key=len, reverse=True)
    changed = []
    for b in conn.execute("""SELECT DISTINCT b.book_id, b.author, b.translator FROM books b
                             JOIN book_authors ba USING(book_id) WHERE ba.author_id = ?""", (drop_id,)).fetchall():
        author, translator = b["author"], b["translator"]
        for v in variants:
            author = author.replace(v, keep) if author else author
            translator = translator.replace(v, keep) if translator else translator
        if (author, translator) != (b["author"], b["translator"]):
            conn.execute("UPDATE books SET author = ?, translator = ? WHERE book_id = ?", (author, translator, b["book_id"]))
            changed.append({"book_id": b["book_id"], "author": [b["author"], author], "translator": [b["translator"], translator]})
    return changed


# ---------------------------------------------------------------------------
# Chat use (approved by Ibrahim 2026-09-30): identity by stored names/aliases, works through
# book_authors, and the stored style for «كتب تشبه فلان».
# ---------------------------------------------------------------------------
_LENGTH3 = {"very_short": "short", "short": "short", "medium": "medium", "long": "long", "very_long": "long"}


_SPELL_CACHE = {"at": 0.0, "rows": None}


def _soft_ops(a, b):
    """Like `_variant_ops`, but only vowel letters added/dropped and transliteration swaps are allowed
    (1 each); any other change makes it another word (9). «سيمون»/«سايمون» = 1, «العلي»/«الولي» = 9."""
    prev = [0]
    for cb in b:
        prev.append(prev[-1] + (1 if cb in _VOWELS else 9))
    for ca in a:
        cur = [prev[0] + (1 if ca in _VOWELS else 9)]
        for j, cb in enumerate(b, 1):
            sub = 0 if ca == cb else (1 if any(ca in g and cb in g for g in _TRANSLIT) else 9)
            cur.append(min(prev[j] + (1 if ca in _VOWELS else 9), cur[j - 1] + (1 if cb in _VOWELS else 9), prev[j - 1] + sub))
        prev = cur
    return prev[-1]


def _spelling_match(k, conn):
    """«سيمون سينك» for the record «سايمون سينك»: the same full name in another Arabic spelling of a foreign
    name (a vowel letter more or less, or a transliteration letter: ب/ف، ج/غ …). Every word must match,
    with the same number of words, at most two small changes in all, and exactly one person may fit;
    otherwise None (then the near-name question «تقصد …؟» still decides). (Ibrahim 2026-10-09)"""
    words = k.split()
    if len(words) < 2 or not re.search(r"[؀-ۿ]", k):
        return None
    import time
    if _SPELL_CACHE["rows"] is None or time.time() - _SPELL_CACHE["at"] > 60:
        rows = []
        for r in conn.execute("SELECT author_id, name_ar, aliases FROM authors"):
            for n in [r["name_ar"]] + _load(r["aliases"]):
                nk = key(n) if n else ""
                if nk and re.search(r"[؀-ۿ]", nk):
                    rows.append((r["author_id"], nk.split()))
        _SPELL_CACHE.update(at=time.time(), rows=rows)
    hits = set()
    for aid, ws in _SPELL_CACHE["rows"]:
        if len(ws) != len(words):
            continue
        total = 0
        for w, x in zip(words, ws):
            if w == x:
                continue
            if frozenset((w, x)) in _DISTINCT_NAMES or min(len(w), len(x)) < 3:
                total = 99
                break
            ops = _soft_ops(w, x)
            if ops > (1 if min(len(w), len(x)) < 5 else 2):
                total = 99
                break
            total += ops
        if total <= 2:
            hits.add(aid)
    return hits.pop() if len(hits) == 1 else None


def lookup(name, conn=None, spelling=True):
    """The author record whose name, Latin name or alias equals `name` (normalized) -> dict or None.
    With `spelling` (default), a full name written in another Arabic spelling also finds its record
    (`_spelling_match`)."""
    k = key(name)
    if not k:
        return None
    own = conn is None
    conn = conn or get_connection()
    try:
        try:
            aid = _index(conn).get(k)
            if aid is None and spelling:
                aid = _spelling_match(k, conn)
        except Exception:  # table missing on a database that was never migrated
            return None
        if aid is None:
            return None
        r = dict(conn.execute("SELECT * FROM authors WHERE author_id = ?", (aid,)).fetchone())
        for f in ("aliases", "known_for", "formats", "subgenres", "moods", "themes"):
            r[f] = _load(r[f])
        return r
    finally:
        if own:
            conn.close()


def names_of(record):
    """All spellings of an author record (canonical, Latin, aliases)."""
    return [n for n in [record["name_ar"], record.get("name_latin")] + list(record.get("aliases") or []) if n]


def style_for(record):
    """-> (style in the shape used by the chat similarity search, source) or (None, None).
    Researched fields first; otherwise the style computed from our researched cards."""
    if not record:
        return None, None
    if record.get("research_status") in ("researched", "thin") and (record["subgenres"] or record["moods"] or record["themes"]):
        return {"formats": record["formats"], "subgenres": record["subgenres"], "moods": record["moods"],
                "pace": record.get("pace"), "length": record.get("typical_length"), "themes": record["themes"]}, "research"
    try:
        cs = json.loads(record.get("catalog_style") or "null")
    except (TypeError, ValueError):
        cs = None
    if cs and (cs.get("subgenres") or cs.get("moods") or cs.get("themes")):
        return {"formats": cs.get("formats") or [], "subgenres": cs.get("subgenres") or [], "moods": cs.get("moods") or [],
                "pace": cs.get("pace"), "length": _LENGTH3.get(cs.get("typical_length")), "themes": cs.get("themes") or []}, "catalog"
    return None, None


def book_ids(author_id, roles=("author",), conn=None):
    own = conn is None
    conn = conn or get_connection()
    try:
        return {r[0] for r in conn.execute(
            f"SELECT book_id FROM book_authors WHERE author_id = ? AND role IN ({','.join('?' * len(roles))})",
            (author_id, *roles))}
    except Exception:
        return set()
    finally:
        if own:
            conn.close()
