# Enrichment guide — researching a book card

How a new book (for example one that arrived from Rewaa with a `pending` card) gets its card.
Research is done from Abjjad, Goodreads and other public pages (owner-reported permission for
Abjjad/Goodreads; see APP_STATE). One JSON record per book, then `scripts/import_enrichment.py`.

## Rules
- Never invent facts. A value that was not seen on a real page is omitted (bibliographic field → `null`).
- No review text or quotes are stored. Every `summary` is the researcher's own words, ≤ 20 Arabic words.
- Confidence: ≥ 0.85 only with 5+ agreeing reviews (0.90 for a publisher/official fact); 0.65–0.84 for a few
  sources; below 0.65 is dropped. Conflicting evidence: `conflict: true`, confidence lowered by 0.15.
- For translated books, `language_difficulty` may only come from Arabic-edition evidence.
- Enums: `docs/book-profile-schema.md` (§2 core attributes, §2.1 age and content warnings).

## Language of the work (required since 2026-09-27)
Every record states three things, read from the edition page (publisher, Abjjad or Goodreads edition data):

| Field | Meaning | Values |
|---|---|---|
| `edition_language` | language of the copy on the shelf | ISO code (`ar`, `en`, `fr`, …) |
| `original_language` | language the work was first written in | ISO code; `null` if not established |
| `translator_ar` | translator of the Arabic edition | name as printed, or `null` |

This gives the card one of: **عمل عربي أصلي** (`ar`/`ar`), **مترجم عن …** (`ar` edition of a non-Arabic
work, with the translator when known), or **كتاب بلغة أخرى** (edition not in Arabic, e.g. an English copy).
Accepted codes: ar en fr es de ru it pt ja zh ko tr fa ur hi sv no da nl pl cs hu el grc he la bs
(anything else is stored as `other`). Full language names (e.g. "English") are converted by the importer.

## Summary and cover (required since 2026-09-27)
Every record also carries, when they can be found:

| Field | Meaning |
|---|---|
| `summary_ar` | **نبذة** — 2–3 sentences (at most 80 words) on what the book is about, written in the researcher's **own words** from the description on Goodreads or Abjjad. Never copied or quoted (the site descriptions are publisher text under copyright; no quotation marks allowed), no spoilers, no reviewer opinions. |
| `summary_source_url` | the Goodreads/Abjjad page the summary is based on |
| `cover_image_url` | direct link to the cover image of this edition |

Cover order: Open Library by ISBN, then Goodreads, then Abjjad are tried automatically by
`./fetch_covers.command`; the researcher only searches the web for books that script could not cover
(list: `reports/covers_missing.csv`) and puts the found image link in `cover_image_url`. The image must be
the cover of the same book (same title on the cover). Books with no cover anywhere are uploaded by staff
in the admin page (book card → الغلاف).

For a book already in the catalog (for example one that came from Rewaa), write
`<records_dir>/existing/<book_id>.json` with `book_id`, `summary_ar`, `summary_source_url`,
`cover_image_url` (and optionally age/content `rows`).

## Record format (`<records_dir>/new/<key>.json`)
```json
{"key": "1-1", "category": "ya", "title_ar": "…", "author_ar": "…",
 "original_title": "…", "original_language": "en", "edition_language": "ar", "translator_ar": "…",
 "publisher_ar": null, "publication_year": null, "page_count": null, "isbn": null,
 "catalog_genre": "رواية", "summary_ar": "…", "summary_source_url": "https://…", "cover_image_url": "https://…",
 "pages": {"abjjad": "https://…", "goodreads": "https://…"},
 "rows": [{"name": "moods", "value": "suspenseful", "confidence": 0.85, "review_count": 6,
           "conflict": false, "source_site": "goodreads", "edition_scope": "original_or_any",
           "summary": "…", "urls": ["https://…"]}]}
```
`rows` names: format, perceived_length, language_difficulty, pace, moods, emotional_weight, use_cases,
age_suitability, sensitive_content (multi-valued attributes: one row per value).

## Import
Work on a copy of the database, check the report, then replace the live file (and back up first):

    backend/venv/bin/python scripts/backup_db.py --reason "before enrichment import"
    backend/venv/bin/python scripts/import_enrichment.py /tmp/work.db data/seed_books.json <records_dir>            # dry run
    backend/venv/bin/python scripts/import_enrichment.py /tmp/work.db data/seed_books.json <records_dir> --apply
    backend/venv/bin/python scripts/catalog_snapshot.py export
    ./fetch_covers.command --urls <records_dir>/covers_todo.json     # covers found during research
    ./fetch_covers.command                                             # Open Library / Goodreads / Abjjad for the rest

Options: `--targets '{"category": n}'` caps new books per category; `--backfill-language` fills the language
fields of books already in the catalog from matching records; `--no-new` imports nothing new.
Records with fewer than 3 reviewed attribute rows are rejected; duplicates (same normalized title, or the
same translated work under another Arabic title) are skipped.

## Books already in the catalog from Rewaa (card v2, since 2026-09-28)
One record per book: `<records_dir>/<book_id>.json` in the card-v2 shape (see `research/pilot_2026-09-28/`
and `docs/book-profile-schema.md` §11–§12): `match.status` (matched / needs_confirmation / not_found),
bibliographic fields, `summary_ar`, `hook_ar`, `subjects`, `themes`, `setting`, series, and `rows` with
`edition_scope` = work / edition / reader_signal (content warnings may add `severity` 1–3, `frequency`,
`note_ar`). Reviews in any language count; `language_difficulty` and `style_complexity` of a translated
book need `edition` scope (Arabic-edition evidence).

    python3 scripts/import_rewaa_cards.py /tmp/work.db <records_dir>            # dry run + import_report.json
    backend/venv/bin/python scripts/backup_db.py --reason "before card import"
    python3 scripts/import_rewaa_cards.py data/library.db <records_dir> --apply
    ./fetch_covers.command --urls <records_dir>/covers_todo.json

Only `matched` records change a book (inventory fields never); the others only set `research_status`
so staff can see which books need confirmation.
