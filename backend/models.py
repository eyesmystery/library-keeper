import json
import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

FEEDBACK_RATINGS = ("perfect", "close", "no")

ATTRIBUTE_NAMES = (
    "format",
    "genres",
    "perceived_length",
    "language_difficulty",
    "pace",
    "moods",
    "emotional_weight",
    "reader_level",
    "use_cases",
)

SOURCE_SITES = ("goodreads", "abjjad", "web", "staff", "derived")
EDITION_SCOPES = ("arabic_edition", "original_or_any")
PROFILE_STATUSES = ("pending", "enriched", "needs_review", "approved")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "library.db")
SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "seed_books.json")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


SUGGESTED_SECTIONS = [
    ("قسم الروايات والقصص", ["رواية", "قصص قصيرة"]),
    ("قسم الأطفال", ["أطفال"]),
    ("قسم اليافعين", ["يافعين"]),
    ("قسم تطوير الذات وعلم النفس والأعمال", ["تطوير ذاتي", "علم نفس", "أعمال"]),
    ("قسم الأدب والشعر والمسرح", ["أدب", "شعر", "مسرحية", "أدب رحلات"]),
    ("قسم التاريخ والسِّير", ["تاريخ", "سيرة ذاتية", "سيرة نبوية"]),
    ("قسم الفكر والدين", ["فكر", "دين"]),
    ("قسم العلوم", ["علوم"]),
]


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add schema columns/tables that older databases may lack.

    Only structural changes happen here — never data values.
    """
    # New evidence table (schema contract: docs/book-profile-schema.md §3.2)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attribute_evidence (
            evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            attribute_name TEXT NOT NULL,
            attribute_value TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0,
            evidence_summary TEXT,
            source_urls TEXT,
            source_site TEXT,
            edition_scope TEXT,
            review_count INTEGER DEFAULT 0,
            conflict_seen INTEGER DEFAULT 0,
            analyzed_at TEXT,
            human_verified INTEGER DEFAULT 0,
            verified_by TEXT,
            verified_at TEXT,
            FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE
        )
        """
    )
    # Columns added to book_profiles by the approved schema (idempotent adds)
    profile_additions = {
        "format": "TEXT",
        "genres": "TEXT",
        "emotional_weight": "TEXT",
    }
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(book_profiles)")
    }
    for column, column_type in profile_additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE book_profiles ADD COLUMN {column} {column_type}")

    # Optional staff review (2026-09-25): soft-delete keeps the history (PRD §26)
    ev_cols = {row["name"] for row in conn.execute("PRAGMA table_info(attribute_evidence)")}
    for column, column_type in {"rejected": "INTEGER DEFAULT 0", "rejected_at": "TEXT", "rejected_by": "TEXT"}.items():
        if column not in ev_cols:
            conn.execute(f"ALTER TABLE attribute_evidence ADD COLUMN {column} {column_type}")

    # Rewaa inventory sync (approved 2026-09-25): identifiers + provenance on books
    book_additions = {
        "barcode": "TEXT",          # normalized digits (ISBN/EAN) from Rewaa
        "rewaa_sku": "TEXT",        # Rewaa product SKU
        "source": "TEXT",           # 'seed' (prototype data) or 'rewaa'
        "last_synced_at": "TEXT",   # ISO time of the last import that touched the row
        # Language of the work (approved 2026-09-27). `language` (existing) = language of THIS
        # copy/edition; `original_language` = language the work was written in (NULL = unknown);
        # together: Arabic original / translated into Arabic / a book in another language.
        "original_language": "TEXT",
        "translator": "TEXT",
        # Cover image (approved 2026-09-27): `cover_url` (existing) points at the local copy
        # served from /covers/; source = openlibrary | goodreads | abjjad | web | staff.
        "cover_source": "TEXT",
        "cover_updated_at": "TEXT",
        # Short summary (نبذة) written in our own words from the book's description on
        # Goodreads/Abjjad (never copied text); source = the page it was based on.
        "summary": "TEXT",
        "summary_source": "TEXT",
        "summary_updated_at": "TEXT",
    }
    existing_books = {row["name"] for row in conn.execute("PRAGMA table_info(books)")}
    for column, column_type in book_additions.items():
        if column not in existing_books:
            conn.execute(f"ALTER TABLE books ADD COLUMN {column} {column_type}")
    # Library sections by category (approved 2026-09-27; see backend/sections.py). Rewaa has no
    # location column, so a book's location is: the shelf a staff member typed, else the active
    # section of its category, else nothing (the visitor is told to ask a staff member).
    new_sections = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_sections'").fetchone() is None
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS library_sections (
            section_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            genres TEXT NOT NULL DEFAULT '[]',
            active INTEGER NOT NULL DEFAULT 0,
            sort INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    if new_sections:  # suggested once, switched off until the manager reviews and turns them on
        for i, (name, genres) in enumerate(SUGGESTED_SECTIONS):
            conn.execute("INSERT INTO library_sections (name, genres, active, sort) VALUES (?, ?, 0, ?)",
                         (name, json.dumps(genres, ensure_ascii=False), i))
    # Visitor choices and reading trends (approved 2026-09-27; see backend/insights.py).
    # Anonymous: no conversation text, no visitor id. Choosing a book never changes stock.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_choices (
            choice_id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            language TEXT,
            match TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_daily (
            day TEXT NOT NULL,
            book_id INTEGER NOT NULL,
            shown INTEGER NOT NULL DEFAULT 0,
            chosen INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, book_id)
        )
        """
    )
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='feedback'").fetchone():
        fb_cols = {row["name"] for row in conn.execute("PRAGMA table_info(feedback)")}
        if "choice_id" not in fb_cols:
            conn.execute("ALTER TABLE feedback ADD COLUMN choice_id INTEGER")
    # Staff accounts + audit log (approved 2026-09-27; see backend/auth.py)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS staff_users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('manager', 'staff')),
            password_hash TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_login_at TEXT,
            password_changed_at TEXT,
            disabled_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            target TEXT,
            details TEXT
        )
        """
    )
    # Model-call budget (approved 2026-09-27): one row per day
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_daily (
            day TEXT PRIMARY KEY,
            model_calls INTEGER NOT NULL DEFAULT 0,
            tokens INTEGER NOT NULL DEFAULT 0,
            fallback_turns INTEGER NOT NULL DEFAULT 0,
            capped_turns INTEGER NOT NULL DEFAULT 0,
            rate_limited INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_books_barcode ON books(barcode)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_books_rewaa_sku ON books(rewaa_sku)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            file_name TEXT,
            applied_at TEXT NOT NULL,
            rows_read INTEGER DEFAULT 0,
            added INTEGER DEFAULT 0,
            updated INTEGER DEFAULT 0,
            unchanged INTEGER DEFAULT 0,
            missing INTEGER DEFAULT 0,
            marked_out_of_stock INTEGER DEFAULT 0,
            invalid INTEGER DEFAULT 0,
            mapping TEXT
        )
        """
    )


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS books (
            book_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            publisher TEXT,
            publication_year INTEGER,
            page_count INTEGER,
            isbn TEXT,
            cover_url TEXT,
            language TEXT DEFAULT 'ar',
            genre TEXT,
            price REAL DEFAULT 0.0,
            quantity INTEGER DEFAULT 0,
            shelf_location TEXT,
            active INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_profiles (
            profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL UNIQUE,
            language_difficulty TEXT,
            pace TEXT,
            perceived_length TEXT,
            ending_tone TEXT,
            reader_level TEXT,
            age_suitability TEXT,
            moods TEXT,
            themes TEXT,
            sensitive_content TEXT,
            use_cases TEXT,
            overall_status TEXT,
            last_enriched_at TEXT,
            format TEXT,
            genres TEXT,
            emotional_weight TEXT,
            FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE
        )
        """
    )
    _migrate_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER,
            rating TEXT NOT NULL CHECK (rating IN ('perfect', 'close', 'no')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            choice_id INTEGER,
            FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE SET NULL
        )
        """
    )
    conn.commit()
    conn.close()


def seed_books() -> None:
    if not os.path.exists(SEED_PATH):
        return
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        books = json.load(f)
    conn = get_connection()
    existing = conn.execute("SELECT COUNT(*) AS c FROM books").fetchone()["c"]
    if existing > 0:
        conn.close()
        return
    for book in books:
        conn.execute(
            """
            INSERT INTO books
            (title, author, publisher, publication_year, page_count, isbn, cover_url, language, genre, price, quantity,
             shelf_location, active, original_language, translator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book.get("title"),
                book.get("author"),
                book.get("publisher"),
                book.get("publication_year"),
                book.get("page_count"),
                book.get("isbn"),
                book.get("cover_url"),
                book.get("language", "ar"),
                book.get("genre"),
                book.get("price", 0.0),
                book.get("quantity", 0),
                book.get("shelf_location"),
                book.get("active", 1),
                book.get("original_language"),
                book.get("translator"),
            ),
        )
    conn.commit()
    conn.close()


def _load_json_list(value):
    """Parse a JSON array stored in a TEXT column; return [] on anything else."""
    if not value:
        return []
    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def get_profile_cards() -> dict:
    """Return usable reading-profile cards keyed by book_id.

    Reads structured conclusions only (never review text). Evidence rows
    supply per-(attribute, value) confidence so callers can weight matches
    and quote reasons honestly per docs/book-profile-schema.md section 7.
    """
    conn = get_connection()
    try:
        profile_rows = conn.execute("SELECT * FROM book_profiles").fetchall()
        try:
            evidence_rows = conn.execute(
                """SELECT book_id, attribute_name, attribute_value, confidence,
                          human_verified, conflict_seen, COALESCE(rejected, 0) AS rejected, source_site
                   FROM attribute_evidence"""
            ).fetchall()
        except sqlite3.OperationalError:  # database not yet migrated (no `rejected` column)
            evidence_rows = conn.execute(
                """SELECT book_id, attribute_name, attribute_value, confidence,
                          human_verified, conflict_seen, 0 AS rejected, source_site
                   FROM attribute_evidence"""
            ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return {}
    cards = {}
    for row in profile_rows:
        cards[row["book_id"]] = {
            "moods": _load_json_list(row["moods"]),
            "pace": row["pace"],
            "emotional_weight": row["emotional_weight"],
            "language_difficulty": row["language_difficulty"],
            "reader_level": row["reader_level"],
            "perceived_length": row["perceived_length"],
            "use_cases": _load_json_list(row["use_cases"]),
            "age_suitability": row["age_suitability"],
            "sensitive_content": _load_json_list(row["sensitive_content"]),
            "confidence": {},
        }
    for ev in evidence_rows:
        card = cards.get(ev["book_id"])
        if card is None:
            continue
        if ev["rejected"]:
            continue  # removed by staff review
        # Schema §4: conflicting evidence always goes to staff review and is
        # not used for matching or reasons until a human verifies it.
        if ev["conflict_seen"] and not ev["human_verified"]:
            continue
        key = (ev["attribute_name"], ev["attribute_value"])
        conf = float(ev["confidence"] or 0.0)
        verified = bool(ev["human_verified"])
        if ev["source_site"] == "derived" and not verified:
            # Derived values (reader level from other attributes, length from an
            # approximate page count) are ranking hints, never quoted as confirmed.
            conf = min(conf, 0.75)
        prev = card["confidence"].get(key)
        if prev is None or conf > prev[0] or (conf == prev[0] and verified and not prev[1]):
            card["confidence"][key] = (conf, verified)
    conn.close()
    return cards


def list_books(only_active: bool = True) -> List[sqlite3.Row]:
    conn = get_connection()
    query = "SELECT * FROM books"
    if only_active:
        query += " WHERE active = 1 AND quantity > 0"
    query += " ORDER BY title"
    rows = conn.execute(query).fetchall()
    conn.close()
    return rows


def get_book(book_id: int) -> Optional[sqlite3.Row]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM books WHERE book_id = ?", (book_id,)).fetchone()
    conn.close()
    return row


def update_book(book_id: int, fields: dict) -> None:
    allowed = {"price", "quantity", "shelf_location", "active", "cover_url", "language", "original_language",
               "translator", "cover_source", "cover_updated_at", "summary", "summary_source", "summary_updated_at"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    conn = get_connection()
    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    conn.execute(f"UPDATE books SET {set_clause} WHERE book_id = ?", (*updates.values(), book_id))
    conn.commit()
    conn.close()


def add_feedback(book_id: Optional[int], rating: str) -> None:
    """Store an anonymous rating: no session id, query text, or personal data."""
    if rating not in FEEDBACK_RATINGS:
        raise ValueError("invalid rating")
    conn = get_connection()
    conn.execute("INSERT INTO feedback (book_id, rating) VALUES (?, ?)", (book_id, rating))
    conn.commit()
    conn.close()
