"""Import researched book cards (one JSON file per book) into the catalog database.

Usage (from the project folder; work on a COPY of the database, check, then replace):
  backend/venv/bin/python scripts/import_enrichment.py <db_path> <seed_json_path> <records_dir> [--apply]
      [--targets '{"kids": 40, ...}'] [--backfill-language] [--no-new]

- Record format and research rules: docs/enrichment-guide.md.
- Without --apply nothing is written; a report is printed and saved as report.json in <records_dir>.
- New books (title not already in the catalog, and not the same translated work under another
  Arabic title) are inserted with their evidence, card, derived reader level and language fields.
- --backfill-language: for books ALREADY in the catalog whose original language is unknown, fill
  original_language / translator / language from a matching record (same normalized title).
- Existing books' reading attributes are never changed here.
- Summary (نبذة) and cover (2026-09-27): `summary_ar` (own words) is stored for new books, and for
  existing books (records in <records_dir>/existing/<book_id>.json) that have no summary yet.
  `cover_image_url` is not downloaded here (the work database is a copy); the importer writes
  <records_dir>/covers_todo.json, and after the copy replaces the live database:
      ./fetch_covers.command --urls <records_dir>/covers_todo.json
"""
import json, glob, os, re, sys, sqlite3, hashlib, collections
from datetime import datetime, timezone

DB, SEED, HERE = sys.argv[1], sys.argv[2], sys.argv[3]
APPLY = "--apply" in sys.argv
BACKFILL = "--backfill-language" in sys.argv
NO_NEW = "--no-new" in sys.argv  # only backfill/update existing books; insert nothing
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")
TARGETS = None  # optional per-category caps: --targets '{"category": n}'
if "--targets" in sys.argv:
    TARGETS = json.loads(sys.argv[sys.argv.index("--targets") + 1])
LANG_CODES = {"arabic": "ar", "english": "en", "french": "fr", "spanish": "es", "german": "de", "russian": "ru",
              "italian": "it", "portuguese": "pt", "japanese": "ja", "chinese": "zh", "korean": "ko",
              "turkish": "tr", "persian": "fa", "farsi": "fa", "urdu": "ur", "hindi": "hi", "swedish": "sv",
              "norwegian": "no", "danish": "da", "dutch": "nl", "polish": "pl", "czech": "cs",
              "hungarian": "hu", "greek": "el", "ancient greek": "grc", "hebrew": "he", "latin": "la",
              "bosnian": "bs", "العربية": "ar", "عربي": "ar", "الإنجليزية": "en", "الفرنسية": "fr"}
KNOWN_LANGS = {"ar", "en", "fr", "es", "de", "ru", "it", "pt", "ja", "zh", "ko", "tr", "fa", "ur", "hi", "sv",
               "no", "da", "nl", "pl", "cs", "hu", "el", "grc", "he", "la", "bs"}


def lang_code(v):
    """'English' / 'en' / 'EN' -> 'en'; unknown or empty -> None (never guessed)."""
    v = str(v or "").strip().lower()
    if not v:
        return None
    v = LANG_CODES.get(v, v)
    return v if v in KNOWN_LANGS else "other"

ALLOWED = {
    "format": ["novel", "short_stories", "biography", "history", "thought", "psychology", "self_dev", "science",
               "poetry", "play", "travel", "religious", "children", "young_adult", "business", "comics", "essays", "criticism", "arts", "sociology", "other"],
    "perceived_length": ["very_short", "short", "medium", "long", "very_long"],
    "language_difficulty": ["very_easy", "easy", "medium", "advanced"],
    "pace": ["fast", "medium", "slow", "contemplative", "mixed"],
    "moods": ["comforting", "warm", "funny", "suspenseful", "mysterious", "inspiring", "contemplative",
              "romantic", "tense", "dark", "sad", "uplifting"],
    "emotional_weight": ["light", "medium", "heavy", "intense"],
    "use_cases": ["habit_start", "light_reading", "coffee", "travel", "gift", "book_club", "discussion",
                  "practical_learning", "quick_read", "slump_recovery"],
    "age_suitability": ["children", "young_adult", "adult", "all_ages"],
    "sensitive_content": ["violence", "sexual_content", "strong_language", "horror", "substance_use", "self_harm",
                          "religious_controversy", "political", "none"],
}
SINGLE = ["format", "perceived_length", "language_difficulty", "pace", "emotional_weight", "age_suitability"]
MULTI_CAP = {"moods": 4, "use_cases": 4}
GENRES = ["رواية", "قصص قصيرة", "أطفال", "يافعين", "تطوير ذاتي", "علم نفس", "تاريخ", "سيرة ذاتية", "سيرة نبوية",
          "دين", "فكر", "علوم", "أعمال", "شعر", "أدب", "مسرحية", "أدب رحلات"]
SHELF = {"رواية": "A", "قصص قصيرة": "A", "تطوير ذاتي": "B", "علم نفس": "B", "أعمال": "B", "مسرحية": "C",
         "فكر": "D", "تاريخ": "D", "سيرة ذاتية": "D", "سيرة نبوية": "D", "دين": "D", "أدب رحلات": "D",
         "علوم": "E", "شعر": "F", "أدب": "F", "أطفال": "G", "يافعين": "H"}
SITES = {"goodreads", "abjjad", "web", "derived"}


def norm(s):
    s = re.sub(r"[ً-ٰٟـ]", "", str(s or ""))
    s = re.sub("[أإآ]", "ا", s).replace("ة", "ه").replace("ى", "ي")
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"^ال", "", s.strip())
    return re.sub(r"[^؀-ۿa-zA-Z0-9]", "", s).lower()


def h(s):
    return int(hashlib.sha1(s.encode()).hexdigest(), 16)


issues = collections.Counter()


def clean_row(r, translated):
    name, value = r.get("name"), r.get("value")
    if name not in ALLOWED or value not in ALLOWED[name]:
        issues["bad enum"] += 1
        return None
    site = r.get("source_site")
    if site not in SITES:
        issues["bad site"] += 1
        return None
    try:
        conf = float(r.get("confidence"))
    except (TypeError, ValueError):
        issues["bad confidence"] += 1
        return None
    rc = r.get("review_count")
    rc = int(rc) if isinstance(rc, (int, float)) else 0
    scope = r.get("edition_scope") if r.get("edition_scope") in ("arabic_edition", "original_or_any") else "original_or_any"
    conflict = bool(r.get("conflict"))
    if site == "derived":
        conf = min(conf, 0.75)
    elif conf >= 0.85 and rc < 5 and conf != 0.90:
        conf = 0.84  # High tier needs 5+ agreeing reviews (0.90 = publisher/official fact)
        issues["capped to 0.84 (<5 reviews)"] += 1
    conf = max(0.0, min(conf, 0.95 if not conflict else 0.80))
    if conf < 0.65:
        issues["dropped <0.65"] += 1
        return None
    if name == "language_difficulty" and translated and scope != "arabic_edition":
        issues["dropped non-Arabic-edition language (translated)"] += 1
        return None
    summary = str(r.get("summary") or "").strip()
    if len(summary) > 160:
        summary = summary[:157] + "…"
    urls = [u for u in (r.get("urls") or []) if isinstance(u, str) and u.startswith(("http://", "https://"))][:4]
    return {"name": name, "value": value, "confidence": round(conf, 2), "review_count": rc, "conflict": conflict,
            "site": site, "scope": None if site == "derived" else scope, "summary": summary or None, "urls": urls}


def derive_level(lang, length, pace, weight):
    if lang in (None, "unknown"):
        return "unknown"
    b = a = 0
    b += 2 if lang in ("very_easy", "easy") else 0
    a += 2 if lang == "advanced" else 0
    b += 1 if length in ("very_short", "short") else 0
    a += 1 if length in ("long", "very_long") else 0
    b += 1 if pace in ("fast", "mixed") else 0
    a += 1 if pace in ("contemplative", "slow") else 0
    b += 1 if weight == "light" else 0
    a += 1 if weight in ("heavy", "intense") else 0
    if b >= a and b >= 3:
        return "beginner"
    if a > b and a >= 3:
        return "advanced"
    return "multiple"


def card_from(rows):
    usable = [r for r in rows if not r["conflict"]]
    card = {}
    for attr in SINGLE:
        c = sorted((r for r in usable if r["name"] == attr), key=lambda r: -r["confidence"])
        card[attr] = c[0]["value"] if c else None
    for attr in ("moods", "use_cases", "sensitive_content"):
        vals = []
        for r in sorted((r for r in usable if r["name"] == attr), key=lambda r: -r["confidence"]):
            if r["value"] not in vals:
                vals.append(r["value"])
        if attr == "sensitive_content" and len(vals) > 1:
            vals = [v for v in vals if v != "none"]
        card[attr] = vals[:MULTI_CAP.get(attr, 9)]
    card["reader_level"] = derive_level(card["language_difficulty"], card["perceived_length"], card["pace"],
                                        card["emotional_weight"])
    return card


def summary_of(d):
    """Own-words summary (نبذة): 2–3 sentences, at most 80 words; the source page is kept."""
    text = re.sub(r"\s+", " ", str(d.get("summary_ar") or "")).strip()
    if not text or len(text.split()) > 80 or '"' in text or "«" in text:
        return None, None
    src = str(d.get("summary_source_url") or "").strip()[:300] or None
    return text, src


def cover_of(d):
    url = str(d.get("cover_image_url") or "").strip()
    return url if re.match(r"^https?://", url) else None


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
_cols = {r["name"] for r in conn.execute("PRAGMA table_info(books)")}
for _c in ("original_language", "translator", "cover_source", "cover_updated_at", "summary", "summary_source",
           "summary_updated_at"):  # same migration the server runs (models._migrate_schema)
    if _c not in _cols:
        conn.execute(f"ALTER TABLE books ADD COLUMN {_c} TEXT")
existing_books = {r["book_id"]: dict(r) for r in conn.execute("SELECT * FROM books")}
seen = {norm(b["title"]) for b in existing_books.values()}
report = {"issues": {}, "existing": {}, "new": {}, "dropped": []}

# ---------- existing books: age + content only
ex_rows = {}
ex_summaries = {}
covers_todo = {}
for f in sorted(glob.glob(os.path.join(HERE, "existing", "*.json"))):
    d = json.load(open(f))
    bid = int(d["book_id"])
    if bid not in existing_books:
        continue
    rows = [x for x in (clean_row(r, False) for r in d.get("rows", [])) if x and x["name"] in ("age_suitability", "sensitive_content")]
    if rows:
        ex_rows[bid] = rows
    summ, src = summary_of(d)
    if summ and not existing_books[bid].get("summary"):
        ex_summaries[bid] = (summ, src)
    if cover_of(d):
        covers_todo[str(bid)] = cover_of(d)
report["existing"] = {"books_with_rows": len(ex_rows), "rows": sum(len(v) for v in ex_rows.values()),
                      "summaries": len(ex_summaries), "cover_urls": len(covers_todo)}

# ---------- new books
cands = collections.defaultdict(list)


def okey(d):
    o = re.sub(r"[^a-z0-9]", "", str(d.get("original_title") or "").lower())
    return o if len(o) >= 4 and (d.get("original_language") or "ar") != "ar" else None


orig_seen = set()
for f in glob.glob(os.path.join(HERE, "new", "*.json")):
    try:
        d0 = json.load(open(f))
    except ValueError:
        continue
    if norm(d0.get("title_ar")) in seen and okey(d0):
        orig_seen.add(okey(d0))
for f in sorted(glob.glob(os.path.join(HERE, "new", "*.json")), key=lambda p: [int(x) for x in os.path.basename(p)[:-5].split("-")]):
    try:
        d = json.load(open(f))
    except ValueError:
        issues["unreadable file"] += 1
        continue
    title, author = str(d.get("title_ar") or "").strip(), str(d.get("author_ar") or "").strip()
    k = norm(title)
    if not title or not author:
        issues["missing title/author"] += 1
        continue
    if k in seen or (okey(d) and okey(d) in orig_seen):
        report["dropped"].append({"key": d.get("key"), "title": title, "why": "duplicate"})
        continue
    translated = (lang_code(d.get("original_language")) or "ar") != "ar"
    rows = [x for x in (clean_row(r, translated) for r in d.get("rows", [])) if x]
    reviewed = [r for r in rows if r["site"] != "derived"]
    if len(reviewed) < 3:
        report["dropped"].append({"key": d.get("key"), "title": title, "why": f"only {len(reviewed)} reviewed rows"})
        continue
    genre = d.get("catalog_genre") if d.get("catalog_genre") in GENRES else None
    if genre is None:
        issues["missing catalog genre"] += 1
        continue
    seen.add(k)
    if okey(d):
        orig_seen.add(okey(d))
    pc = d.get("page_count")
    pc = int(pc) if isinstance(pc, (int, float)) and 4 <= pc <= 3000 else None
    yr = d.get("publication_year")
    yr = int(yr) if isinstance(yr, (int, float)) and 800 <= yr <= 2026 else None
    isbn = re.sub(r"[^0-9Xx]", "", str(d.get("isbn") or ""))
    translator = str(d.get("translator_ar") or "").strip()[:120] or None
    cands[d.get("category")].append({"file": os.path.basename(f), "spare": bool(d.get("spare")), "title": title[:200],
                                     "edition_language": lang_code(d.get("edition_language")) or "ar",
                                     "original_language": lang_code(d.get("original_language")),
                                     "translator": translator,
                                     "author": author[:120], "publisher": (d.get("publisher_ar") or None),
                                     "year": yr, "pages": pc, "isbn": isbn if len(isbn) in (10, 13) else "",
                                     "genre": genre, "rows": rows, "translated": translated,
                                     "summary": summary_of(d), "cover": cover_of(d)})

# optional per-category caps (non-spare first); without --targets every valid record is chosen
chosen, left = [], []
for cat, items in cands.items():
    items.sort(key=lambda x: x["spare"])
    t = 0 if NO_NEW else (len(items) if TARGETS is None else TARGETS.get(cat, 0))
    chosen += items[:t]
    left += items[t:]
report["new"] = {"available": sum(len(v) for v in cands.values()), "chosen": len(chosen),
                 "per_category": {c: sum(1 for x in chosen if x in v) for c, v in cands.items()},
                 "unused_spares": len(left)}
report["issues"] = dict(issues)

# language backfill for books already in the catalog (records matched by normalized title)
backfill = {}
if BACKFILL:
    by_title = {norm(b["title"]): b for b in existing_books.values() if not b.get("original_language")}
    for f in glob.glob(os.path.join(HERE, "new", "*.json")) + glob.glob(os.path.join(HERE, "*.json")):
        try:
            d = json.load(open(f))
        except (ValueError, OSError):
            continue
        if not isinstance(d, dict):
            continue
        b = by_title.get(norm(d.get("title_ar")))
        orig = lang_code(d.get("original_language"))
        if b and orig and b["book_id"] not in {v["book_id"] for v in backfill.values()}:
            backfill[norm(b["title"])] = {"book_id": b["book_id"], "original_language": orig,
                                          "translator": (str(d.get("translator_ar") or "").strip()[:120] or None),
                                          "language": lang_code(d.get("edition_language")) or "ar"}
    report["backfill_language"] = {"books": len(backfill),
                                   "translated": sum(1 for v in backfill.values() if v["original_language"] != "ar"),
                                   "arabic_original": sum(1 for v in backfill.values() if v["original_language"] == "ar")}

if APPLY:
    shelf_next = collections.Counter()
    for b in existing_books.values():
        m = re.match(r"([A-Z])(\d+)", b["shelf_location"] or "")
        if m:
            shelf_next[m.group(1)] = max(shelf_next[m.group(1)], int(m.group(2)))
    new_seed = []

    def ins_ev(bid, r, verified=0):
        conn.execute("""INSERT INTO attribute_evidence (book_id, attribute_name, attribute_value, confidence,
                        evidence_summary, source_urls, source_site, edition_scope, review_count, conflict_seen,
                        analyzed_at, human_verified, rejected) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                     (bid, r["name"], r["value"], r["confidence"], r["summary"], json.dumps(r["urls"]),
                      r["site"], r["scope"], r["review_count"], int(r["conflict"]), NOW, verified))

    for info in backfill.values():
        conn.execute("UPDATE books SET original_language = ?, translator = ?, language = ? "
                     "WHERE book_id = ? AND original_language IS NULL",
                     (info["original_language"], info["translator"], info["language"], info["book_id"]))
    for bid, (summ, src) in ex_summaries.items():
        conn.execute("UPDATE books SET summary = ?, summary_source = ?, summary_updated_at = ? "
                     "WHERE book_id = ? AND (summary IS NULL OR summary = '')", (summ, src, NOW, bid))
    for bid, rows in ex_rows.items():
        conn.execute("DELETE FROM attribute_evidence WHERE book_id=? AND attribute_name IN ('age_suitability','sensitive_content') AND source_site!='staff'", (bid,))
        for r in rows:
            ins_ev(bid, r)
        card = card_from(rows)
        conn.execute("UPDATE book_profiles SET age_suitability=?, sensitive_content=? WHERE book_id=?",
                      (card["age_suitability"], json.dumps(card["sensitive_content"]), bid))
    for x in chosen:
        letter = SHELF[x["genre"]]
        shelf_next[letter] += 1
        pages = x["pages"]
        if x["genre"] == "أطفال":
            price = 25 + 5 * (h(x["title"]) % 5)
        else:
            base = 30 + (pages or 250) // 10
            price = min(120, max(30, 5 * round(base / 5)))
        qty = 1 + h(x["title"] + "q") % 4
        book = {"title": x["title"], "author": x["author"], "publisher": x["publisher"], "publication_year": x["year"],
                "page_count": pages, "isbn": x["isbn"], "cover_url": "", "genre": x["genre"], "price": float(price),
                "quantity": qty, "shelf_location": None}  # never invented: staff or library sections (2026-09-27)
        book.update(language=x["edition_language"], original_language=x["original_language"], translator=x["translator"])
        cur = conn.execute("""INSERT INTO books (title, author, publisher, publication_year, page_count, isbn, cover_url,
                              language, genre, price, quantity, shelf_location, active, source, original_language, translator)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,'seed',?,?)""",
                           (book["title"], book["author"], book["publisher"], book["publication_year"], book["page_count"],
                            book["isbn"], "", book["language"], book["genre"], book["price"], book["quantity"],
                            book["shelf_location"], book["original_language"], book["translator"]))
        bid = cur.lastrowid
        summ, src = x["summary"]
        if summ:
            conn.execute("UPDATE books SET summary = ?, summary_source = ?, summary_updated_at = ? WHERE book_id = ?",
                         (summ, src, NOW, bid))
            book["summary"] = summ
        if x["cover"]:
            covers_todo[str(bid)] = x["cover"]
        for r in x["rows"]:
            ins_ev(bid, r)
        card = card_from(x["rows"])
        lvl = card["reader_level"]
        if lvl != "unknown":
            ins_ev(bid, {"name": "reader_level", "value": lvl, "confidence": 0.75, "summary": "محسوب من سهولة اللغة والطول والإيقاع والثقل",
                         "urls": [], "site": "derived", "scope": None, "review_count": 0, "conflict": False})
        genres = [card["format"]] if card["format"] else []
        conn.execute("""INSERT INTO book_profiles (book_id, format, genres, perceived_length, language_difficulty, pace,
                        moods, emotional_weight, reader_level, use_cases, age_suitability, sensitive_content,
                        overall_status, last_enriched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'enriched',?)""",
                     (bid, card["format"], json.dumps(genres), card["perceived_length"] or "unknown",
                      card["language_difficulty"] or "unknown", card["pace"] or "unknown",
                      json.dumps(card["moods"], ensure_ascii=False), card["emotional_weight"] or "unknown", lvl,
                      json.dumps(card["use_cases"]), card["age_suitability"], json.dumps(card["sensitive_content"]), NOW))
        new_seed.append(book)
    conn.commit()
    seed = json.load(open(SEED, encoding="utf-8"))
    seed += new_seed
    if BACKFILL:
        by_title = {norm(s_["title"]): s_ for s_ in seed}
        for k, info in backfill.items():
            if k in by_title:
                by_title[k].update(info)
    with open(SEED, "w", encoding="utf-8") as fh:  # same layout as the original: one book per line
        fh.write("[\n  " + ",\n  ".join(json.dumps(b, ensure_ascii=False) for b in seed) + "\n]\n")
    report["applied"] = {"new_books": len(new_seed), "seed_total": len(seed),
                         "summaries_new": sum(1 for b in new_seed if b.get("summary")),
                         "summaries_existing": len(ex_summaries), "cover_urls": len(covers_todo)}
    if covers_todo:
        json.dump(covers_todo, open(os.path.join(HERE, "covers_todo.json"), "w"), indent=1)
conn.close()
json.dump(report, open(os.path.join(HERE, "report.json"), "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: v for k, v in report.items() if k != "dropped"}, ensure_ascii=False, indent=1))
print("dropped:", len(report["dropped"]))
