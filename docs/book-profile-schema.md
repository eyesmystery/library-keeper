# Book Profile Schema — Smart Catalog Design (Approved, Implemented)

Status: approved design on 2026-09-24; implemented in `data/library.db`. Current prototype catalog (checked 2026-09-28): 500 books, 500 cards (400 `enriched`, 100 `needs_review`), 5,257 evidence rows. The prototype books will be removed before the real Rewaa catalog (§11.1). §§1–10 describe card version 1 as implemented; §12 is the approved card version 2 for the real catalog (not implemented yet).

Language note: this document is written in English per the repository documentation rule. Attribute display values include Arabic labels because the visitor UI is Arabic.

---

## 1. Purpose

One enriched **book card** per book, holding the ten approved core reading attributes plus evidence, confidence, source, and approval status for every attribute value. The card is written by the enrichment agent (offline, before opening hours) and read by the recommendation engine during visitor sessions. Visitors never see raw attributes as guarantees; they see reasons built from approved values only.

## 2. The ten core attributes

| # | Attribute (key) | Type | Values (UI label in Arabic) | Notes |
|---|---|---|---|---|
| 1 | `format` | enum | novel (رواية), short_stories (قصص قصيرة), biography (سيرة), history (تاريخ), thought (فكر), psychology (علم نفس), self_dev (تطوير ذاتي), science (علوم), poetry (شعر), play (مسرحية), travel (أدب رحلات), religious (ديني), children (أطفال), young_adult (يافعين), business (أعمال), comics (قصص مصورة), essays (مقالات ونصوص), criticism (نقد أدبي), arts (فنون وسينما) — these four added 2026-10-03, sociology (علم اجتماع) — added 2026-10-04, other (آخر), unknown (غير معروف) | One value. Maps to the existing `genre` column; `format` is the normalized supertype. |
| 2 | `genres` | multi-enum | same list as `format` plus subgenres later | 1–3 values. First value must equal `format`. |
| 3 | `page_count` | int | — | Already in `books` table. Edition-bound fact, not derived from reviews. |
| 4 | `perceived_length` | enum | very_short (قصير جدًا), short (قصير), medium (متوسط), long (طويل), very_long (طويل جدًا), unknown (غير معروف) | Baseline derived from `page_count` (see §6.1); overridden only by repeated review evidence ("feels much longer than its pages"). |
| 5 | `language_difficulty` | enum | very_easy (سهلة جدًا), easy (سهلة), medium (متوسطة), advanced (متقدمة), unknown (غير معروف) | **Arabic-edition-bound.** For translated books this MUST come from reviews of the Arabic edition (Abjjad). Never copied from the original work's reviews. |
| 6 | `pace` | enum | fast (سريع), medium (متوسط), slow (بطيء), contemplative (تأملي), mixed (متفاوت), unknown (غير معروف) | Story attribute; for translated books may come from reviews of the original work (Goodreads) or the Arabic edition. |
| 7 | `moods` | multi-enum | comforting (مريح), warm (دافئ), funny (مرح), suspenseful (مشوق), mysterious (غامض), inspiring (ملهم), contemplative (تأملي), romantic (رومانسي), tense (متوتر), dark (مظلم), sad (حزين), uplifting (مبتهج), unknown (غير معروف) | 0–3 values plus `unknown` when no evidence. Most important attribute for mood-based requests. |
| 8 | `emotional_weight` | enum | light (خفيف), medium (متوسط), heavy (ثقيل), intense (شديد), unknown (غير معروف) | Kept separate from mood: a sad book can be light; a thriller can be heavy. |
| 9 | `reader_level` | enum | beginner (مبتدئ), regular (معتاد), advanced (متقدم), multiple (مناسب لمستويات متعددة), unknown (غير معروف) | **Derived attribute** (see §6.2), not asked of reviewers directly. |
| 10 | `use_cases` | multi-enum | habit_start (بداية عادة القراءة), light_reading (قراءة خفيفة), coffee (مع القهوة), travel (أثناء السفر), gift (هدية), book_club (نادي قراءة), discussion (للنقاش), practical_learning (تعلّم عملي), quick_read (قراءة سريعة), slump_recovery (الخروج من فتور القراءة), unknown (غير معروف) | 0–3 values. Drives the quick-start choices. |

Deferred conditional attributes (NOT in this phase, listed to freeze the schema shape): `themes`, `opening_grip`, `style_complexity`, `ending_tone`, `series_dependency`. **Update 2026-09-28:** approved for the real-catalog phase, collected in the same research pass as the core attributes — see §11.3. (`age_suitability` and `sensitive_content` were approved on 2026-09-25; see §2.1.) Each will reuse the same evidence record structure when approved, so no migration will be needed for them.

### 2.1 Age and content attributes (approved 2026-09-25, all books)

**Update 2026-10-04 (Ibrahim: «اختيار الفئة العمرية يكون فقط اذا في محتوى جنسي يكون لا يناسب الاطفال فقط»):** age categories are no longer used. `age_suitability` rows stay in the database but the app does not read or show them (hidden in the admin card too). A child or teenager is offered books from the children's / young-adult shelves (card `format` children / young_adult; a teenager also gets children's books), minus the blocking content warnings below. The only age statement given to the model is «لا يناسب الأطفال» on a book whose card lists `sexual_content`. A request that names neither a genre nor a child gets no children's books.

| Attribute | Type | Allowed values | Notes |
|---|---|---|---|
| `age_suitability` | enum | children (≤12, للأطفال), young_adult (13–17, لليافعين), adult (للكبار), all_ages (لكل الأعمار) | One value. Evidence: publisher age ratings, Goodreads/Abjjad shelves and descriptions, reviewer reports. Stored in `book_profiles.age_suitability`. |
| `sensitive_content` | multi-enum | violence, sexual_content, strong_language, horror, substance_use, self_harm, religious_controversy, political, or `none` | Zero or more; `none` only when no warning survives. Stored as a JSON list in `book_profiles.sensitive_content`. |

Recommendation rules (implemented in `backend/app.py` `age_allowed`):
- When the visitor is choosing for a child (`children`) or a teen (`young_adult`) — detected from words like طفل/بنتي عمرها 8/مراهق/ثانوي or from the AI `audience` field — a book is eligible only if its card shows a suitable age at Medium tier or better (children: children/all_ages; teens: young_adult/all_ages/children and not adult) and no blocking content flag at Medium or better. Unknown age never passes for these readers.
- Blocking flags: children — every flag; teens — sexual_content, strong_language, substance_use, self_harm, religious_controversy.
- Other visitors: books tagged only `children` are left out (they stay on the children's shelf); content the visitor asks to avoid (AI `avoid_content`) excludes flagged books.
- In chat, age and content warnings are passed to the model as card facts (confirmed/impression), and only age-suitable books can be shown as cards.

### 2.2 Language of the work (approved 2026-09-27, bibliographic — on `books`, not the card)

| Field | Meaning |
|---|---|
| `books.language` | language of this copy/edition (existing column, default `ar`) |
| `books.original_language` | language the work was written in; `NULL` = not established (never guessed) |
| `books.translator` | translator of the Arabic edition, when known |

Shown everywhere as one label (`origin_label()` in `backend/app.py`): «عمل عربي أصلي», «مترجم عن <اللغة>، ترجمة <المترجم>», or «كتاب باللغة <اللغة> (غير مترجم للعربية)»; nothing is shown when unknown. Filled by research records (`docs/enrichment-guide.md`) or by staff in the admin card; passed to the chat model as `origin` in the book facts.

## 3. Storage design

Two tables (implemented). Since this section was written, columns were added: `books.original_language/translator/summary/summary_source/cover_source/barcode/rewaa_sku/...`, `book_profiles.age_suitability/sensitive_content/themes/ending_tone`, and `attribute_evidence.rejected/rejected_at/rejected_by` (soft removal by staff).

### 3.1 `book_profiles` — one row per book (the card)

| Column | Type | Meaning |
|---|---|---|
| `book_id` | INTEGER UNIQUE → books | One card per book. |
| `format` | TEXT | Attribute 1. |
| `genres` | TEXT (JSON array) | Attribute 2. |
| `perceived_length` | TEXT | Attribute 4. |
| `language_difficulty` | TEXT | Attribute 5. |
| `pace` | TEXT | Attribute 6. |
| `moods` | TEXT (JSON array) | Attribute 7. |
| `emotional_weight` | TEXT | Attribute 8. |
| `reader_level` | TEXT | Attribute 9. |
| `use_cases` | TEXT (JSON array) | Attribute 10. |
| `overall_status` | TEXT | `pending` / `enriched` / `needs_review` / `approved`. |
| `last_enriched_at` | TEXT (ISO 8601) | Last agent run. |

`page_count` stays in `books`; it is an inventory/bibliographic fact.

### 3.2 `attribute_evidence` — one row per attribute finding (PRD §16.1 `AttributeEvidence`)

| Column | Type | Meaning |
|---|---|---|
| `evidence_id` | INTEGER PK | — |
| `book_id` | INTEGER → books | — |
| `attribute_name` | TEXT | One of the ten keys (or a deferred key later). |
| `attribute_value` | TEXT | The concluded value. |
| `confidence` | REAL | 0.00–1.00, per §4. |
| `evidence_summary` | TEXT | One short sentence describing the pattern found (e.g. "multiple reviews describe a fast-moving plot"). **Never contains quoted review text.** |
| `source_urls` | TEXT (JSON array) | Links actually read for this conclusion. |
| `source_site` | TEXT | `goodreads` / `abjjad` / `web` (a single non-review web source such as an article; capped at single-source confidence) / `staff` (only for real staff input) / `derived`. |
| `edition_scope` | TEXT | `arabic_edition` / `original_or_any` — enforces the §5 rule for `language_difficulty`. |
| `review_count` | INTEGER | How many independent reviews supported the value. |
| `conflict_seen` | INTEGER (bool) | Whether sources disagreed. |
| `analyzed_at` | TEXT (ISO 8601) | When the agent concluded. |
| `human_verified` | INTEGER (bool) | Set by staff review. |
| `verified_by` | TEXT | Staff identifier. |
| `verified_at` | TEXT (ISO 8601) | When verified. |

### 3.3 What is never stored

- Full review texts or any quoted review fragments.
- Reviewer names or profiles.
- Ratings copied per review (only aggregate counts inside evidence rows).
- Any visitor personal data.

## 4. Confidence model (PRD §14.6)

| Tier | Range | Handling |
|---|---|---|
| High | 0.85–1.00 | Usable in matching and in visitor-facing reasons. |
| Medium | 0.65–0.84 | Usable for ranking only; never phrased as a guarantee to the visitor; queued for staff review. |
| Low | < 0.65 | Treated as `unknown`; queued for staff review; excluded from reasons. |

Starting scores (before conflict adjustments):

| Evidence situation | Starting confidence |
|---|---|
| Publisher/official description supports the value | 0.90 |
| Agreement of 5+ independent reviews | 0.85 |
| Agreement of 2–4 independent reviews | 0.75 |
| Single clear review | 0.65 |
| Model general knowledge alone | 0.50 (always `unknown` tier unless human-verified) |

Adjustments:

- Conflicting sources: −0.15 and `conflict_seen = 1`; conflicting rows always go to staff review regardless of final score.
- Reviews are short, vague, or off-topic (filtered out): they do not count toward agreement.
- Same-site duplicate reviews of the same edition count once toward independence.
- A value with zero usable evidence stays `unknown` with confidence recorded as 0.

## 5. Source rules

| Attribute | Goodreads (original work reviews) | Abjjad (Arabic edition reviews) | Derived |
|---|---|---|---|
| `format`, `genres` | ✔ | ✔ | — |
| `page_count` | — | — | from inventory data |
| `perceived_length` | ✔ (signal only) | ✔ (signal only) | baseline from page_count |
| `language_difficulty` | ✖ for translated works | ✔ required for translated works | — |
| `pace` | ✔ | ✔ | — |
| `moods` | ✔ | ✔ | — |
| `emotional_weight` | ✔ | ✔ | — |
| `reader_level` | — | — | derived (§6.2) |
| `use_cases` | ✔ | ✔ | — |

For originally-Arabic works, both sites qualify for every attribute. Every evidence row records `source_site` and `edition_scope`, so any rule violation is detectable by query.

## 6. Derivation rules

### 6.1 `perceived_length` baseline

| page_count | Baseline |
|---|---|
| ≤ 150 | `very_short` |
| 151–300 | `short` |
| 301–500 | `medium` |
| 501–800 | `long` |
| > 800 | `very_long` |

Review override: only when ≥3 independent reviews say the book feels longer/shorter than its page count; move one step and record evidence.

### 6.2 `reader_level` derivation (Phase 1)

Score from available signals:

| Signal | Beginner points | Advanced points |
|---|---|---|
| `language_difficulty` | very_easy/easy: +2 | advanced: +2 |
| `perceived_length` | very_short/short: +1 | long/very_long: +1 |
| `pace` | fast/mixed: +1 | contemplative/slow: +1 |
| `emotional_weight` | light: +1 | heavy/intense: +1 |

- Beginner ≥ advanced and beginner ≥ 3 → `beginner`.
- Advanced > beginner and advanced ≥ 3 → `advanced`.
- Both < 3 → `multiple`.
- Insufficient signals (language_difficulty unknown) → `unknown`.

Weights are starting values; tune after the first real batch, log every change.

## 7. Recommendation usage rules (Phase 1)

1. Hard filters (never broken): active + in stock; explicit format when the visitor named one.
   - Implemented 2026-09-27 (`constrain()` in `backend/app.py`, both the AI and keyword paths): in stock + active; suitable age; content the visitor wants to avoid; moods/weights the visitor excluded (Medium tier or better on the card); the genre(s) the visitor named (a children's/young-adult request also accepts the أطفال/يافعين shelf); "short" when asked (a book with known pages > 250, or a long/very long card when pages are unknown, is excluded). Filler books are drawn only from books that pass these filters; if none pass, the visitor is told which wishes could not be met.
2. Explicit constraints the visitor stated (length cap, language difficulty request): match only High-tier values, or Medium-tier that is human-verified.
3. Soft preferences (mood, pace, weight, use case): rank by tier-weighted score; High = 1.0, human-verified Medium = 0.8, unknown = 0.
4. Reasons quote only values from the matched card with High tier (or verified Medium), phrased from the approved Arabic labels.
5. Unknown never blocks a book; it only removes it from reasons.

## 8. Card lifecycle

| Status | Meaning |
|---|---|
| `pending` | No attributes yet. |
| `enriched` | Agent ran; ≥1 attribute has a High-tier value; no conflicts pending. |
| `needs_review` | Any conflict_seen, or Medium-tier value that staff should confirm, or language_difficulty missing for a translated work. |
| `approved` | Staff pressed approve; card is fully trusted for the season. |

Re-enrichment triggers: new stock entry, staff request, new source discovered, conflict reported. Never during a visitor session.

## 9. Admin review queue

Ordering: conflicts first, then missing language_difficulty on translated works, then Medium-tier pending confirmation, then newest. Implemented as an optional staff review in the admin (`backend/card_review.py`: confirm / remove / restore / set / add per attribute, soft removals). Review is optional because ~5,000 books are expected; cards work from evidence confidence alone.

## 10. Implementation items (status 2026-09-28)

1. `attribute_evidence` table and `book_profiles` columns — done (migration runs on server start).
2. Batch order for the prototype books — done (100 on 2026-09-24, then later waves; 500 books total).
3. Enrichment runner — research records imported per `docs/enrichment-guide.md`.
4. Staff accounts for `verified_by` — admin accounts exist; staff verification writes the account name.

Open for the real catalog: see §11 and §12.

## 11. Real-catalog phase (approved by Ibrahim 2026-09-28)

Applies when the ~5,000 real books are loaded from Rewaa. Not implemented yet.

### 11.1 Start from an empty catalog

Before the first real Rewaa import, all current prototype books (and their cards, evidence rows and covers) are removed, so test data never mixes with real books. A full database backup is taken first; the wipe itself is run only after Ibrahim confirms it at that moment (approval gate: destructive data change).

### 11.2 What Rewaa provides vs. what we fetch

| From Rewaa (kept) | Ignored Rewaa fields |
|---|---|
| title (المنتج الأساسي), publisher (e.g. دار الساقي), Rewaa SKU (الرمز التعريفي — internal code, not an ISBN), barcode (often empty), available quantity, retail price | cost, wholesale price, tax type, brand, product description (empty), last purchase price, supplier, product category |

Everything else is fetched by research: author, translator, original title/language, publication year, page count, ISBN, cover, back-cover summary, and all reading attributes.

Matching rule (no ISBN in most rows): a source page is accepted only when **title and publisher both agree**. Several plausible matches or no clear match → the book is marked «يحتاج تأكيد» (needs confirmation) instead of guessing. Any ISBN found is saved to `books` so later runs match more precisely. Staff filling the barcode in Rewaa would further improve matching.

### 11.3 Deferred attributes — now collected in the same pass

| Attribute | Main source | Notes |
|---|---|---|
| `series_dependency` | Goodreads / Open Library series field (e.g. "#2") | factual; High confidence when the series field exists |
| `themes` | publisher description, reader shelves on Goodreads/Abjjad, reviews | multi-value |
| `style_complexity` | reviews of the **Arabic edition** | edition-bound, same rule as `language_difficulty` |
| `opening_grip` | reviews ("hooked from page one", "slow start") | fiction only |
| `ending_tone` | reviews | fiction only; stores tone only (happy / sad / open / bittersweet), never plot details |

Books with few reviews (common for Arabic non-fiction) will have many `unknown` values; that is acceptable — unknown never excludes a book, it only removes it from reasons.

### 11.4 Reviews in any language

Reviews in **any language** count as evidence (translated books often have far more reviews in other editions), for story-level attributes: format/genres, pace, moods, emotional_weight, use_cases, perceived_length signal, themes, opening_grip, ending_tone, age/content. **Exception unchanged:** `language_difficulty` and `style_complexity` describe the wording of the Arabic edition, so for translated works they still come only from Arabic-edition evidence (`edition_scope = arabic_edition`). The `evidence_summary` stays in the researcher's own Arabic words; no review text is stored in any language.

### 11.5 Summary (نبذة) and cover

- Every card gets a نبذة based on the book's description on Goodreads (normally the back-cover text), falling back to Abjjad. Per the 2026-09-27 rule it is written in the researcher's own words (2–3 sentences, ≤ 80 words), faithful to that description, no spoilers, with `summary_source_url` pointing to the page used.
- Cover: low resolution is enough — just enough to recognize the cover. Stored at about 300 px wide, compressed (WebP, roughly 20–30 KB each; ~100–150 MB for 5,000 books). Source order unchanged: Open Library → Goodreads/Abjjad → web search → staff upload.

## 12. Card version 2 (approved by Ibrahim 2026-09-28; storage + import implemented 2026-09-28)

Implemented: columns in `books` (hook, subjects, setting, subgenres, series_name/position, learning_outcomes, research_status/note/at) and `book_profiles` (style_complexity, opening_grip, narrative_structure, practicality, prior_knowledge, series_dependency; themes and ending_tone already existed), `attribute_evidence.severity/frequency/note_ar`, the `grief` warning, chat facts (hook, subjects, themes, setting, series) and `scripts/import_rewaa_cards.py`. Also implemented 2026-09-28: admin display of the v2 fields; severity-based age rules (§12.4, rules below); book relations (§12.5, `backend/relations.py`, rules below).

Severity rules as implemented (an unknown severity stays unknown — never assumed strong or mild; the flag alone then decides as before severities existed): children — every flag in the children's blocking list blocks at any severity, `grief` only at 3; young adults — their blocking flags (sexual_content, strong_language, substance_use, self_harm, religious_controversy) block from 2 up, a mild (1) one does not; violence / horror / grief block young adults only at 3.

Relations as implemented: same_author, series_prev/next, similar, lighter/shorter/easier alternative. Card-based relations need a researched card, the same format and the same edition language; alternatives must share a theme or two moods; a volume that needs earlier parts is never an alternative; at most 4 similar / 2 per alternative type / 5 same-author per book. Rebuilt on every server start and after `scripts/import_rewaa_cards.py --apply`. In chat, related books of the first three facts are added (up to 2 each) only when in stock and suitable for the reader, with their reason. Not built: younger_alternative, continues_topic, other_language_edition.

Built from an external review of the app, filtered for fit with this plan and for effort at ~5,000 books. Version 1 attributes (pace, moods, emotional_weight, …) stay as they are; version 2 adds to them.

### 12.1 Scope of every value

`attribute_evidence.edition_scope` is widened to three values: `work` (true of the book in any edition: genre, themes, series, plot-level traits), `edition` (true of the copy on the shelf: ISBN, translator, pages, cover, Arabic language difficulty, style complexity), `reader_signal` (impressions from readers: pace, mood, opening grip). Edition facts from a foreign edition are never copied onto the Arabic copy.

### 12.2 Fields added to every book (core)

| Field | Scope | Source / rule |
|---|---|---|
| `isbn13` | edition | Open Library, Google Books, publisher page; saved when found (ISBN-10, dimensions, binding and edition number are not collected) |
| `edition_year` | edition | same sources |
| `original_title`, `original_language`, `translator` | work / edition | §2.2 rules unchanged; never guessed |
| `series_name`, `series_position` | work | Goodreads / Open Library series field |
| `summary_ar` (نبذة) | work | own words, 2–3 sentences, no spoilers (§11.5) |
| `hook_ar` | work | one short sentence on why the book is engaging, written with the summary, no spoilers |
| `subgenres` | work | stored under `genres` after the main format |
| `subjects` | work | what the book is about (e.g. تاريخ الأندلس) |
| `themes` | work | human/intellectual themes (e.g. الفقد، الهوية، الانتماء), 3–6 values |
| `setting` | work | place and era, only when it matters to the book |

### 12.3 Type-specific extensions

Filled only for the matching format; left `unknown` otherwise.

| Format | Fields |
|---|---|
| Fiction (novel, short stories, play, young adult) | `opening_grip`, `ending_tone` (tone only, no plot), `narrative_structure` (linear / interwoven / multiple voices / letters) |
| Knowledge books (history, thought, science, psychology, self-development, business, religious) | `practicality` (theoretical / practical / exercises / personal experience), `prior_knowledge` (none / some / specialist), `learning_outcomes` (what the reader comes away understanding, own words) |
| Biography, poetry, children | core fields only for now |

`style_complexity` (direct / literary / philosophical / experimental) applies to all formats and stays Arabic-edition-only for translated works (§11.4).

### 12.4 Content warnings with severity

`sensitive_content` values gain a severity (1 mild, 2 clear, 3 strong), an optional frequency (passing / recurring — only when sources say so), and one short spoiler-free Arabic note. `grief` is added to the warning list. Age rules in §2.1 block at severity ≥ 1 for children and use the same blocking flags for teens.

### 12.5 Relations between books (later step, after the catalog is filled)

Stored as `source_book_id, target_book_id, relation_type, reason_ar, confidence, source`. Types: `same_author`, `series_prev`, `series_next`, `similar`, `lighter_alternative`, `shorter_alternative`, `easier_alternative`, `younger_alternative`, `continues_topic`, `other_language_edition`. Author/series relations come from bibliographic facts; the alternatives are computed from card values inside the in-stock catalog, with the reason taken from the values that differ. Only in-stock targets are offered to visitors.

### 12.6 Sources added

- **Google Books** (volumes search): description, pages, ISBN, language, cover; accepted only under the title + publisher matching rule (§11.2).
- **Publisher page**: used when found; not a required step.
- **Open Library covers**: requests are paced and every cover is stored locally once; no repeated fetches.
- Descriptions are never stored as text; only the source URL is kept.

### 12.7 Not adopted (and why)

| Suggestion | Reason |
|---|---|
| suspense / humor / romance levels, cognitive load, reading-time estimate | already covered by `moods`, `reader_level`/`language_difficulty`, `perceived_length` |
| dialogue vs description, plot vs character, point of view | reviews rarely state them reliably; would be mostly `unknown` at 5,000 books |
| `best_for` / `not_ideal_for` as stored text | the chat model derives them from card values at answer time, so no unsupported claims are stored |
| ISBN-10, dimensions, binding, edition number, abridged flag | no value for the conversation |
| WorldCat | requires a paid subscription |
| storing the full publisher description | copyright rule (§11.5) |
| post-reading visitor signals | visitors have no accounts, so there is no way to reach them after reading; the existing anonymous rating stays |

### 12.8 Pilot before the full run

Research the first **50 real books** from the Rewaa file with the full version 2 card, then measure fill rate per field and per format. Fields that come back mostly `unknown` are dropped or deferred before the remaining ~5,000 books are researched.

### 12.9 Staff confirmation of unidentified books (approved 2026-09-28; built last, after the app)

- During research, a book that cannot be identified with confidence keeps up to 3 `candidates` in its record (title, author, publisher, year, cover link, page link); the importer stores them in `books.research_candidates` (JSON) next to `research_status` / `research_note`.
- Planned admin screen «كتب تحتاج تأكيد»: with several candidates the staff member picks the matching cover or chooses «ولا واحد منها» and types the author from the book; with one candidate: «نعم هو» / «لا»; plus «مو كتاب» for non-book products.
- After confirmation the cover is fetched automatically by the app; the full card is researched in the next research batch (same method as the pilot), not generated from the in-app model's own knowledge. Model-with-web-search inside the app is a possible later option.

