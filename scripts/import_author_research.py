"""Import an author research batch into `authors` (docs/author-profile-proposal.md).

  python3 scripts/import_author_research.py <db_path> <batch_dir> [--apply] [--fill-only]

--fill-only (2026-10-01): sourced values already stored (Wikidata/Abjjad) are kept; the batch only fills
empty fields, adds aliases, and never lowers the research status.

<batch_dir>/records.json: [{author_id, name_ar, name_latin, origin, aliases, known_for, formats, subgenres,
moods, pace, typical_length, themes, audience, style_note_ar, confidence, research_status}].
Each record must still match its author (name or alias) — a record whose author was merged or renamed
is skipped and reported. Vocabularies are checked (formats/moods/pace as on the book card). Aliases
already used by another author are dropped and reported. Staff-verified names/origins are not
overwritten. `catalog_style` and `books` are never touched. Without --apply: dry run on a copy.
The report is written to <batch_dir>/import_report.json.
"""
import json, os, sqlite3, sys, tempfile
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))
import models  # noqa: E402
import authors  # noqa: E402
import llm  # noqa: E402
from chat import FORMATS  # noqa: E402

db, batch, apply = sys.argv[1], sys.argv[2], "--apply" in sys.argv[3:]
fill_only = "--fill-only" in sys.argv[3:]
RANK = {"pending": 0, "needs_sources": 1, "thin": 2, "researched": 3}
records = json.load(open(os.path.join(batch, "records.json"), encoding="utf-8"))
if not apply:
    tmp = os.path.join(tempfile.mkdtemp(), "copy.db")
    src, dst = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(tmp)
    src.backup(dst); src.close(); dst.close()
    db = tmp
models.DB_PATH = db
models.init_db()
conn = models.get_connection()
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
STATUSES = ("researched", "thin", "needs_sources")
report = {"applied": apply, "imported": [], "skipped": [], "issues": []}
try:
    for r in records:
        aid = r.get("author_id")
        if aid is None:
            # an author we have no book by (well-known authors list, approved 2026-09-30): reuse the record
            # if any stored spelling matches, otherwise create it; created=well_known for the report
            aid = authors.find(conn, r["name_ar"]) or next((authors.find(conn, a) for a in r.get("aliases") or [] if authors.find(conn, a)), None)
            if aid is None:
                aid = conn.execute("""INSERT INTO authors (name_ar, aliases, research_status, research_note, created_at, updated_at)
                                      VALUES (?, '[]', 'pending', 'well-known author, no book in our catalog when added', ?, ?)""",
                                   (r["name_ar"], now, now)).lastrowid
                report.setdefault("created", []).append({"author_id": aid, "name": r["name_ar"]})
            r = dict(r, author_id=aid, name_ar=conn.execute("SELECT name_ar FROM authors WHERE author_id=?", (aid,)).fetchone()[0])
        row = conn.execute("SELECT * FROM authors WHERE author_id = ?", (aid,)).fetchone()
        if row is None or aid not in (authors.find(conn, r["name_ar"]), authors.find(conn, r.get("name_latin") or "")):
            report["skipped"].append({"author_id": aid, "name": r["name_ar"], "why": "author record changed (merged/renamed/removed)"})
            continue
        if r["research_status"] not in STATUSES:
            report["skipped"].append({"author_id": aid, "why": "bad status"}); continue
        bad = ([f for f in r["formats"] if f not in FORMATS] + [m for m in r["moods"] if m not in llm.MOODS]
               + ([r["pace"]] if r["pace"] and r["pace"] not in llm.PACES else [])
               + ([r["typical_length"]] if r["typical_length"] not in (None, "short", "medium", "long") else [])
               + ([r["audience"]] if r["audience"] not in (None, "adult", "young_adult", "children", "mixed") else []))
        if bad:
            report["skipped"].append({"author_id": aid, "why": f"values outside the vocabulary: {bad}"}); continue
        aliases = authors._load(row["aliases"])
        for a in r["aliases"] + ([r["name_latin"]] if r["name_latin"] and row["name_latin"] and
                                 authors.key(r["name_latin"]) != authors.key(row["name_latin"]) else []):
            if authors.key(a) in {authors.key(x) for x in aliases + [row["name_ar"], row["name_latin"] or ""]}:
                continue
            clash = authors._taken(conn, [a], aid)
            if clash:
                report["issues"].append(f"{r['name_ar']}: alias «{a}» already belongs to «{clash}» — dropped")
                continue
            aliases.append(a)
        staff = bool(row["staff_verified"])
        # a record stored under a Latin-script name gets its Arabic name; the Latin one stays name_latin
        name_ar, name_latin = row["name_ar"], row["name_latin"]
        if not staff and authors._is_latin(name_ar) and not authors._is_latin(r["name_ar"]):
            clash = authors._taken(conn, [r["name_ar"]], aid)
            if clash:
                report["issues"].append(f"{r['name_ar']}: Arabic name already belongs to «{clash}» — kept «{name_ar}»")
            else:
                name_latin, name_ar = name_latin or name_ar, r["name_ar"]
                aliases = [a for a in aliases if authors.key(a) != authors.key(name_ar)]
        if fill_only:
            L = lambda f: authors._load(row[f])
            keep = lambda f, v: row[f] if row[f] not in (None, "", "[]") else v
            r = dict(r, origin=keep("origin", r["origin"]), known_for=L("known_for") or r["known_for"], formats=L("formats") or r["formats"],
                     subgenres=L("subgenres") or r["subgenres"], moods=L("moods") or r["moods"], pace=keep("pace", r["pace"]),
                     typical_length=keep("typical_length", r["typical_length"]), themes=L("themes") or r["themes"],
                     audience=keep("audience", r["audience"]), style_note_ar=keep("style_note_ar", r["style_note_ar"]),
                     research_status=max(r["research_status"], row["research_status"], key=lambda x: RANK.get(x, 0)))
        note = ("general knowledge, no sources read in this batch" if r["research_status"] != "needs_sources"
                else "nothing reliable known yet; only the catalog-computed style applies")
        conn.execute("""UPDATE authors SET name_ar=?, name_latin=?, origin=?, aliases=?, known_for=?, formats=?, subgenres=?, moods=?,
                        pace=?, typical_length=?, themes=?, audience=?, style_note_ar=?, confidence=?, research_status=?,
                        research_note=?, researched_at=?, updated_at=? WHERE author_id=?""",
                     (name_ar, name_latin if (staff or not r["name_latin"]) else (name_latin or r["name_latin"]),
                      row["origin"] if (staff and row["origin"]) else (r["origin"] or row["origin"]),
                      json.dumps(aliases, ensure_ascii=False), json.dumps(r["known_for"], ensure_ascii=False),
                      json.dumps(r["formats"]), json.dumps(r["subgenres"], ensure_ascii=False), json.dumps(r["moods"]),
                      r["pace"], r["typical_length"], json.dumps(r["themes"], ensure_ascii=False), r["audience"],
                      r["style_note_ar"], (row["confidence"] or min(r["confidence"] or 0, 0.75) or None) if fill_only else (min(r["confidence"] or 0, 0.75) or None),
                      r["research_status"], (((row["research_note"] or "") + " | general knowledge for the gaps") if fill_only and row["research_note"] else note), now, now, aid))
        report["imported"].append({"author_id": aid, "name": r["name_ar"], "status": r["research_status"]})
    conn.commit()
finally:
    conn.close()
report["summary"] = {"created": len(report.get("created", [])), "imported": len(report["imported"]), "skipped": len(report["skipped"]), "issues": len(report["issues"]),
                     "by_status": {s: sum(1 for x in report["imported"] if x["status"] == s) for s in STATUSES}}
with open(os.path.join(batch, "import_report.json"), "w", encoding="utf-8") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=1)
print(json.dumps(report["summary"], ensure_ascii=False))
for x in report["skipped"] + report["issues"]:
    print(" ", x)
