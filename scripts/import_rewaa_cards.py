"""Import researched card-v2 records for books that are ALREADY in the catalog (from Rewaa).

Usage (from the project folder; work on a COPY of the database, check, then replace):
  python3 scripts/import_rewaa_cards.py <db_path> <records_dir> [--apply] [--include-probable]

Records: <records_dir>/<book_id>.json, card v2 shape (docs/book-profile-schema.md §12,
research/pilot_2026-09-28/_save.py). Only records whose match.status is "matched" are imported
(with --include-probable also needs_confirmation records that name an author and a format).
For the other records only books.research_status / research_note are written so staff can see
which books need confirmation.

What changes for an imported book (never price, stock, SKU, barcode or shelf):
  - bibliographic facts: author (only when still «غير معروف»), title (the researched display
    title, e.g. a typo fixed), original_language, translator, language (edition), year, pages,
    ISBN (checksum-valid only, when empty), genre (Arabic label from the format)
  - summary (own words) + source, hook, subjects, subgenres, setting, series, learning outcomes
  - the card: previous evidence for the book is replaced by the record's rows; book_profiles is
    recomputed (v1 attributes + v2 attributes + themes)
  - cover_image_url is NOT downloaded here: <records_dir>/covers_todo.json is written for
    ./fetch_covers.command --urls <records_dir>/covers_todo.json
Without --apply nothing is written; the report is printed and saved as <records_dir>/import_report.json.
"""
import glob, json, os, re, sqlite3, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import rewaa_import  # noqa: E402  (is_isbn)
import covers as covers_mod  # noqa: E402  (book-page pattern for covers_todo)

DB, HERE = sys.argv[1], sys.argv[2]
APPLY = "--apply" in sys.argv
PROBABLE = "--include-probable" in sys.argv
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")

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
                          "religious_controversy", "political", "grief", "none"],
    # card v2 (§12.3)
    "style_complexity": ["direct", "literary", "philosophical", "experimental"],
    "opening_grip": ["fast", "slow"],
    "ending_tone": ["happy", "uplifting", "bittersweet", "sad", "open", "shocking"],
    "narrative_structure": ["linear", "interwoven", "multiple_voices", "letters", "essays"],
    "practicality": ["theoretical", "practical", "exercises", "personal_experience"],
    "prior_knowledge": ["none", "some", "specialist"],
    "series_dependency": ["standalone", "standalone_start", "requires_previous"],
}
SINGLE = ["format", "perceived_length", "language_difficulty", "pace", "emotional_weight", "age_suitability",
          "style_complexity", "opening_grip", "ending_tone", "narrative_structure", "practicality",
          "prior_knowledge", "series_dependency"]
MULTI_CAP = {"moods": 4, "use_cases": 4, "sensitive_content": 9}
GENRE_AR = {"novel": "رواية", "short_stories": "قصص قصيرة", "children": "أطفال", "young_adult": "يافعين",
            "self_dev": "تطوير ذاتي", "psychology": "علم نفس", "history": "تاريخ", "biography": "سيرة ذاتية",
            "religious": "دين", "thought": "فكر", "science": "علوم", "business": "أعمال", "poetry": "شعر",
            "play": "مسرحية", "travel": "أدب رحلات",
            "comics": "قصص مصورة", "essays": "مقالات ونصوص", "criticism": "نقد أدبي", "arts": "فنون وسينما",
            "sociology": "علم اجتماع"}
SITES = {"goodreads", "abjjad", "web", "derived", "staff"}
LANGS = {"ar", "en", "fr", "es", "de", "ru", "it", "pt", "ja", "zh", "ko", "tr", "fa", "ur", "hi", "sv", "no", "da",
         "nl", "pl", "cs", "hu", "el", "grc", "he", "la", "bs"}


def lang(v):
    v = str(v or "").strip().lower()
    return None if not v else (v if v in LANGS else "other")


def clean_row(r, translated, issues):
    name, value = r.get("name"), r.get("value")
    if name not in ALLOWED or value not in ALLOWED[name]:
        issues.append(f"bad value {name}={value}")
        return None
    site = r.get("source_site")
    if site not in SITES:
        issues.append(f"bad site {site}")
        return None
    try:
        conf = float(r.get("confidence"))
    except (TypeError, ValueError):
        return None
    rc = int(r.get("review_count") or 0)
    conflict = bool(r.get("conflict"))
    if site == "derived":
        conf = min(conf, 0.75)
    elif conf >= 0.85 and rc < 5 and conf != 0.90:
        conf = 0.84  # High tier needs 5+ agreeing reviews (0.90 = publisher/official fact)
    conf = max(0.0, min(conf, 0.95 if not conflict else 0.80))
    if conf < 0.65:
        return None
    scope = r.get("edition_scope")
    # card v2 scope (work / edition / reader_signal) -> stored scope used by the app
    stored_scope = "arabic_edition" if scope == "edition" and r.get("_edition_ar") else "original_or_any"
    if name in ("language_difficulty", "style_complexity") and translated and scope != "edition":
        issues.append(f"dropped {name}: not Arabic-edition evidence")
        return None
    summary = re.sub(r"\s+", " ", str(r.get("summary") or "")).strip()[:160] or None
    urls = [u for u in (r.get("urls") or []) if isinstance(u, str) and u.startswith(("http://", "https://"))][:4]
    sev = r.get("severity") if isinstance(r.get("severity"), int) and 1 <= r.get("severity") <= 3 else None
    freq = r.get("frequency") if r.get("frequency") in ("passing", "recurring") else None
    note = re.sub(r"\s+", " ", str(r.get("note_ar") or "")).strip()[:120] or None
    return {"name": name, "value": value, "confidence": round(conf, 2), "review_count": rc, "conflict": conflict,
            "site": site, "scope": None if site == "derived" else stored_scope, "summary": summary, "urls": urls,
            "severity": sev, "frequency": freq, "note": note}


def derive_level(lang_d, length, pace, weight):
    if lang_d in (None, "unknown"):
        return "unknown"
    b = a = 0
    b += 2 if lang_d in ("very_easy", "easy") else 0
    a += 2 if lang_d == "advanced" else 0
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


def length_from_pages(p):
    if not p:
        return None
    return "very_short" if p <= 150 else "short" if p <= 300 else "medium" if p <= 500 else "long" if p <= 800 else "very_long"


def card_from(rows):
    usable = [r for r in rows if not r["conflict"]]
    card = {}
    for attr in SINGLE:
        c = sorted((r for r in usable if r["name"] == attr), key=lambda r: -r["confidence"])
        card[attr] = c[0]["value"] if c else None
    for attr, cap in MULTI_CAP.items():
        vals = []
        for r in sorted((r for r in usable if r["name"] == attr), key=lambda r: -r["confidence"]):
            if r["value"] not in vals:
                vals.append(r["value"])
        if attr == "sensitive_content" and len(vals) > 1:
            vals = [v for v in vals if v != "none"]
        card[attr] = vals[:cap]
    card["reader_level"] = derive_level(card["language_difficulty"], card["perceived_length"], card["pace"], card["emotional_weight"])
    return card


def text(v, n):
    v = re.sub(r"\s+", " ", str(v or "")).strip()
    return v[:n] if v else None


def strlist(v, n=6):
    return [text(x, 60) for x in (v or []) if isinstance(x, str) and x.strip()][:n]


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import models  # noqa: E402
models.DB_PATH = DB
models.init_db()  # same migration as the server (adds card-v2 columns)
books = {r["book_id"]: dict(r) for r in conn.execute("SELECT * FROM books")}

report = {"imported": [], "flagged": [], "skipped": [], "issues": {}}
plans = []
covers = {}
for f in sorted(glob.glob(os.path.join(HERE, "*.json"))):
    if os.path.basename(f).startswith(("_", "import_report", "covers_")):
        continue
    d = json.load(open(f, encoding="utf-8"))
    bid = d.get("book_id")
    if bid not in books:
        report["skipped"].append({"book_id": bid, "why": "not in catalog"})
        continue
    status = (d.get("match") or {}).get("status")
    if d.get("product_type"):
        status = "not_a_book" if status == "matched" or d.get("product_type") in ("activity_kit", "activity_book") else status
    importable = status == "matched" or (PROBABLE and status == "needs_confirmation" and d.get("author") and d.get("format"))
    note = text((d.get("match") or {}).get("note"), 300)
    if d.get("product_type") and not importable:
        status = "not_a_book"
    if not importable:
        cands = [{k: (text(c.get(k), 200) if isinstance(c.get(k), str) else c.get(k)) for k in
                  ("title", "author", "publisher", "year", "cover_image_url", "url")}
                 for c in (d.get("candidates") or []) if isinstance(c, dict) and c.get("title")][:3]
        plans.append(("flag", bid, status, note, json.dumps(cands, ensure_ascii=False) if cands else None))
        report["flagged"].append({"book_id": bid, "title": books[bid]["title"], "status": status})
        continue
    edition = lang(d.get("edition_language")) or books[bid].get("language") or "ar"
    original = lang(d.get("original_language"))
    translated = bool(original and original != edition)
    issues = []
    rows = []
    for r in d.get("rows", []):
        x = clean_row(dict(r, _edition_ar=(edition == "ar")), translated, issues)
        if x:
            rows.append(x)
    fmt = next((r["value"] for r in rows if r["name"] == "format"), None) or (d.get("format") if d.get("format") in ALLOWED["format"] else None)
    pages = d.get("page_count") if isinstance(d.get("page_count"), int) and 10 <= d["page_count"] <= 5000 else None
    if pages and not any(r["name"] == "perceived_length" for r in rows):
        rows.append({"name": "perceived_length", "value": length_from_pages(pages), "confidence": 0.75, "review_count": 0,
                     "conflict": False, "site": "derived", "scope": None, "summary": f"{pages} صفحة", "urls": [],
                     "severity": None, "frequency": None, "note": None})
    b = books[bid]
    upd = {}
    if d.get("author") and (not b.get("author") or b["author"] == "غير معروف"):
        upd["author"] = text(d["author"], 120)
    if d.get("title_display") and d["title_display"] != b["title"]:
        upd["title"] = text(d["title_display"], 200)
    if original:
        upd["original_language"] = original
    if d.get("translator"):
        upd["translator"] = text(d["translator"], 120)
    upd["language"] = edition
    # books.publication_year = year of THIS edition; the original year of a translated work is not it
    year = d.get("edition_year") if isinstance(d.get("edition_year"), int) else (
        d.get("publication_year") if isinstance(d.get("publication_year"), int) and not translated else None)
    if year and not b.get("publication_year"):
        upd["publication_year"] = year
    if pages and not b.get("page_count"):
        upd["page_count"] = pages
    isbn = rewaa_import.norm_code(d.get("isbn13"))
    if isbn and rewaa_import.is_isbn(isbn) and not b.get("isbn"):
        upd["isbn"] = isbn
    if fmt and GENRE_AR.get(fmt):
        upd["genre"] = GENRE_AR[fmt]
    s = text(d.get("summary_ar"), 700)
    if s and len(s.split()) <= 80 and '"' not in s:
        upd.update(summary=s, summary_source=text(d.get("summary_source_url"), 300), summary_updated_at=NOW)
    for k, n in (("hook_ar", "hook"),):
        if d.get(k):
            upd[n] = text(d[k], 240)
    for k in ("subjects", "subgenres"):
        if strlist(d.get(k)):
            upd[k] = json.dumps(strlist(d.get(k)), ensure_ascii=False)
    if d.get("setting"):
        upd["setting"] = text(d["setting"], 160)
    if d.get("series_name"):
        upd["series_name"] = text(d["series_name"], 120)
        if isinstance(d.get("series_position"), int):
            upd["series_position"] = d["series_position"]
    if d.get("learning_outcomes"):
        upd["learning_outcomes"] = text(d["learning_outcomes"], 300)
    upd.update(research_status=status, research_note=note, researched_at=d.get("researched_at") or NOW)
    page = next((u for u in (d.get("pages") or {}).values()
                 if isinstance(u, str) and covers_mod.is_book_page(u)), None)
    cover_src = d.get("cover_image_url") if str(d.get("cover_image_url") or "").startswith("http") else page
    if cover_src and not b.get("cover_url"):
        covers[str(bid)] = cover_src  # an image URL, or the book page (fetch_covers uses its og:image)
    card = card_from(rows)
    themes = strlist(d.get("themes"))
    plans.append(("import", bid, upd, rows, (card, themes, fmt)))
    report["imported"].append({"book_id": bid, "title": upd.get("title", b["title"]), "rows": len(rows),
                               "fields": sorted(k for k in upd if k not in ("research_status", "research_note", "researched_at"))})
    if issues:
        report["issues"][str(bid)] = issues

if APPLY:
    try:
        conn.execute("BEGIN")
        for p in plans:
            if p[0] == "flag":
                _, bid, status, note, cands = p
                conn.execute("UPDATE books SET research_status=?, research_note=?, research_candidates=?, researched_at=? WHERE book_id=?",
                             (status, note, cands, NOW, bid))
                continue
            _, bid, upd, rows, (card, themes, fmt) = p
            conn.execute(f"UPDATE books SET {', '.join(k + '=?' for k in upd)} WHERE book_id=?", (*upd.values(), bid))
            conn.execute("DELETE FROM attribute_evidence WHERE book_id=?", (bid,))
            for r in rows:
                conn.execute("""INSERT INTO attribute_evidence (book_id, attribute_name, attribute_value, confidence,
                                evidence_summary, source_urls, source_site, edition_scope, review_count, conflict_seen,
                                analyzed_at, human_verified, severity, frequency, note_ar)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)""",
                             (bid, r["name"], r["value"], r["confidence"], r["summary"], json.dumps(r["urls"]), r["site"],
                              r["scope"], r["review_count"], int(r["conflict"]), NOW, r["severity"], r["frequency"], r["note"]))
            high = any(r["confidence"] >= 0.85 and not r["conflict"] and r["site"] != "derived" for r in rows)
            status = "enriched" if high and not any(r["conflict"] for r in rows) else "needs_review"
            genres = [fmt] if fmt else []
            conn.execute("INSERT OR IGNORE INTO book_profiles (book_id, overall_status) VALUES (?, 'pending')", (bid,))
            conn.execute("""UPDATE book_profiles SET format=?, genres=?, perceived_length=?, language_difficulty=?, pace=?,
                            moods=?, emotional_weight=?, reader_level=?, use_cases=?, age_suitability=?, sensitive_content=?,
                            themes=?, ending_tone=?, style_complexity=?, opening_grip=?, narrative_structure=?, practicality=?,
                            prior_knowledge=?, series_dependency=?, overall_status=?, last_enriched_at=? WHERE book_id=?""",
                         (fmt, json.dumps(genres), card["perceived_length"], card["language_difficulty"], card["pace"],
                          json.dumps(card["moods"]), card["emotional_weight"], card["reader_level"], json.dumps(card["use_cases"]),
                          card["age_suitability"], json.dumps(card["sensitive_content"]), json.dumps(themes, ensure_ascii=False),
                          card["ending_tone"], card["style_complexity"], card["opening_grip"], card["narrative_structure"],
                          card["practicality"], card["prior_knowledge"], card["series_dependency"], status, NOW, bid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    import relations  # noqa: E402  (after the cards changed, relations are recomputed)
    report["relations"] = relations.rebuild()
    import authors  # noqa: E402  (new/changed authors are linked to their author records)
    report["authors"] = authors.sync()
    with open(os.path.join(HERE, "covers_todo.json"), "w", encoding="utf-8") as fh:
        json.dump(covers, fh, ensure_ascii=False, indent=1)
conn.close()
report["summary"] = {"imported": len(report["imported"]), "flagged": len(report["flagged"]),
                     "skipped": len(report["skipped"]), "covers_todo": len(covers), "applied": APPLY}
with open(os.path.join(HERE, "import_report.json"), "w", encoding="utf-8") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=1)
print(json.dumps(report["summary"], ensure_ascii=False))
for k, v in report["issues"].items():
    print(k, v)
