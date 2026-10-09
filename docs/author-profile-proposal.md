# Author Profiles — Approved 2026-09-30 (step 1 implemented)

Status: proposed and approved by Ibrahim 2026-09-30 (§5 answers below). Step 1 implemented the same day: tables, string splitting, links, catalog-computed styles (`backend/authors.py`, `scripts/build_authors.py`, sync on every server start and after `import_rewaa_cards.py --apply`). Not yet: author research batches, the well-known-authors list, chat use. Adds two tables; changes no existing column. Written in English per the repository documentation rule; the Arabic summary was given to Ibrahim in the conversation.

## 1. Why

Today the author is only the text in `books.author`. Checked on the live DB (2026-09-30):

- 4,558 of 5,358 books have «غير معروف» (not yet researched); the researched books carry 520 distinct author strings, 104 of them with more than one book.
- The same person appears under several spellings: «ماريو بارغاس يوسا» / «ماريو فارغاس يوسا»، «جيلبرت سينويه» / «جيلبير سينويه»، «جوزيه رودريغيز دوس سانتوس» / «جوزيه ر. دوس سانتوس»; Márquez is stored only as «Gabriel García Márquez».
- One string often holds several people and roles: «آلان مور (رسوم ديف غيبونز)»، «سارة المصري (تحرير)»، co-authors separated by «،» or «و».
- Nothing is stored about an author. Identity confirmation (full name, aliases) and «كتب تشبه فلان» depend on the model's general knowledge in every conversation, and its description changes from run to run (observed 2026-09-30: satire in one run, horror/fantasy in another).

## 2. Tables

### 2.1 `authors`

| Field | Meaning / rule |
|---|---|
| `author_id` | key |
| `name_ar` | canonical Arabic display name as usually published in Arabic |
| `name_latin` | original-script or Latin name when the author is not Arabic |
| `aliases` | JSON list of other spellings/transliterations (Arabic and Latin), used for matching only |
| `origin` | country/region of the author when reliably known (enables «كاتب سعودي»), else null |
| `known_for` | up to 5 best-known titles (internal: identity confirmation and similarity; never offered as books) |
| `formats`, `subgenres`, `moods`, `pace`, `typical_length`, `themes` | the author's usual writing, same vocabularies as the book card (`llm.MOODS`, `llm.PACES`, card formats; subgenres as shelf labels) |
| `audience` | adult / young_adult / children / mixed |
| `style_note_ar` | one short internal sentence («رعب وغموض بلغة سهلة وسخرية، قصص قصيرة سريعة») |
| `catalog_style` | JSON computed from our researched cards of their books (books, formats, subgenres, moods, pace, typical_length, themes); recomputed on every sync. The researched style fields above are kept separate and are never overwritten by it |
| `research_status`, `researched_at`, `confidence`, `source_urls` | as for books; general knowledge capped at 0.75 like book rows |
| `staff_verified`, `verified_by`, `verified_at` | optional review, never required |

### 2.2 `book_authors`

`book_id`, `author_id`, `role` (author / translator / illustrator / editor), `position`. A book can have several people; `books.author` stays as the display string from research and is never overwritten by this table.

## 3. How it is filled

1. **Split existing strings (script, no research):** parse `books.author` (and `books.translator`) into people and roles: «(رسوم …)» → illustrator, «(تحرير)» → editor, «،» and a joining «و» between full names → co-authors. Exact normalized matches merge automatically; probable duplicates (same family name, near-identical first name) go to a merge list resolved during the author research batch. Nothing is guessed silently.
2. **Style from our own catalog (computed):** for an author with researched books in stock, formats/subgenres/moods/pace/length are aggregated from those cards (`style_basis = catalog`). Deterministic, recomputed after every import.
3. **Author research batches (100 authors per batch, done directly by Claude, no sub-agents):** aliases, origin, known works, and the style fields from Goodreads/Abjjad author pages and general knowledge, in the same way as book batches (backup → import script → log).
4. **Authors visitors ask for who are not in the catalog** (e.g. أحمد خالد توفيق): option A — a fixed list of well-known authors Arabic readers ask for (≈150) researched once; option B — also record, anonymously and name-only, author names the chat could not match, for the next batch. B stores visitor-derived text and needs explicit approval under the privacy rule.
5. **Every new book batch** links its books to author records (existing or new).

## 4. How the app uses it

- **Identity:** «ماركيز» / «أسامة» / «أحمد توفيق» are matched against `name_ar` + `aliases` first; the confirmation question proposes the stored canonical name; listing authors by first name reads the table. The model's aliases remain only a fallback for authors not yet in the table.
- **Works by an author:** through `book_authors` (all spellings, co-authored books; translators/illustrators only when the visitor asks for them, e.g. «ترجمات صالح علماني»).
- **Similar to an author:** the stored style replaces the model's per-conversation description; the model's description is used only when the author has no record, and the debug output says which was used.
- **Visitor wording:** style fields are used to search and to state the likeness («قريب من أجواء الرعب والغموض اللي يُعرف فيها»); no biography, awards or other author facts are presented.

## 5. Decisions (answered by Ibrahim 2026-09-30)

Answers: 1 approved; 2 our authors + the well-known-authors list (option A; option B not approved); 3 all four roles; 4 a general likeness sentence only (no biography/awards).

Original questions:

1. Approve the two tables (schema change).
2. Scope of research: authors of our books only, or also the well-known-authors list (option A), and whether to record unmatched author names (option B).
3. Roles: include translators and illustrators, or authors only.
4. Whether the likeness sentence may name the author's known style to the visitor, or keep it search-only.

## 6. Effort and order

Split script + tables + tests first (one session), then catalog-computed styles, then research batches alongside the book batches. The chat changes come last and keep the current behaviour as fallback.
