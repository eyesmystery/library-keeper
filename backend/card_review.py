"""Optional staff review of reading attributes (approved 2026-09-25).

Review is OPTIONAL: with ~5,000 books nobody can read everything, so cards
keep working from review-derived confidence alone. Staff can, per book:
  - confirm a value   -> human_verified = 1 (treated as confirmed everywhere)
  - remove a value    -> soft delete (rejected = 1); history kept (PRD §26)
  - restore a value   -> undo a removal
  - set a value       -> single-valued attributes: previous values are marked
                         removed and a verified staff row is added
  - add a value       -> multi-valued attributes (moods, use cases, genres)
After every action the book card (book_profiles) is recomputed from the
remaining evidence, and reader_level is re-derived unless staff set it.
"""
import json
from datetime import datetime, timezone

from models import get_connection

SINGLE = ["format", "perceived_length", "language_difficulty", "pace", "emotional_weight", "reader_level",
          "age_suitability"]
# Card version 2 single-valued attributes (approved 2026-09-28, schema §12.3)
V2_SINGLE = ["style_complexity", "opening_grip", "ending_tone", "narrative_structure", "practicality",
             "prior_knowledge", "series_dependency"]
SINGLE = SINGLE + V2_SINGLE
MULTI = ["genres", "moods", "use_cases", "sensitive_content"]
FORMATS = ["novel", "short_stories", "biography", "history", "thought", "psychology", "self_dev",
           "science", "poetry", "play", "travel", "religious", "children", "young_adult", "business",
           "comics", "essays", "criticism", "arts", "sociology", "other"]
ALLOWED = {
    "format": FORMATS, "genres": FORMATS,
    "perceived_length": ["very_short", "short", "medium", "long", "very_long"],
    "language_difficulty": ["very_easy", "easy", "medium", "advanced"],
    "pace": ["fast", "medium", "slow", "contemplative", "mixed"],
    "emotional_weight": ["light", "medium", "heavy", "intense"],
    "reader_level": ["beginner", "regular", "advanced", "multiple"],
    "moods": ["comforting", "warm", "funny", "suspenseful", "mysterious", "inspiring", "contemplative",
              "romantic", "tense", "dark", "sad", "uplifting"],
    "use_cases": ["habit_start", "light_reading", "coffee", "travel", "gift", "book_club", "discussion",
                  "practical_learning", "quick_read", "slump_recovery"],
    "age_suitability": ["children", "young_adult", "adult", "all_ages"],
    "sensitive_content": ["violence", "sexual_content", "strong_language", "horror", "substance_use",
                          "self_harm", "religious_controversy", "political", "grief", "none"],
    "style_complexity": ["direct", "literary", "philosophical", "experimental"],
    "opening_grip": ["fast", "slow"],
    "ending_tone": ["happy", "uplifting", "bittersweet", "sad", "open", "shocking"],
    "narrative_structure": ["linear", "interwoven", "multiple_voices", "letters", "essays"],
    "practicality": ["theoretical", "practical", "exercises", "personal_experience"],
    "prior_knowledge": ["none", "some", "specialist"],
    "series_dependency": ["standalone", "standalone_start", "requires_previous"],
}
ACTIONS = ("confirm", "reject", "restore", "set", "add")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _derive_reader_level(lang, length, pace, weight):
    """Schema §6.2 (same rule as the 2026-09-24 cleanup)."""
    if lang in (None, "unknown"):
        return "unknown"
    b = a = 0
    if lang in ("very_easy", "easy"):
        b += 2
    if lang == "advanced":
        a += 2
    if length in ("very_short", "short"):
        b += 1
    if length in ("long", "very_long"):
        a += 1
    if pace in ("fast", "mixed"):
        b += 1
    if pace in ("contemplative", "slow"):
        a += 1
    if weight == "light":
        b += 1
    if weight in ("heavy", "intense"):
        a += 1
    if b >= a and b >= 3:
        return "beginner"
    if a > b and a >= 3:
        return "advanced"
    return "multiple"


def _rank(row):
    # verified first (latest verification wins), then confidence, then newest
    return (1 if row["human_verified"] else 0, row["verified_at"] or "", row["confidence"] or 0, row["evidence_id"])


def recompute_card(conn, book_id):
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM attribute_evidence WHERE book_id = ? AND COALESCE(rejected, 0) = 0", (book_id,))]
    by_attr = {}
    for r in rows:
        if r["attribute_value"] != "unknown":
            by_attr.setdefault(r["attribute_name"], []).append(r)
    card = {}
    for attr in SINGLE:
        cands = sorted(by_attr.get(attr, []), key=_rank, reverse=True)
        card[attr] = cands[0]["attribute_value"] if cands else "unknown"
    for attr in MULTI:
        seen, values = set(), []
        for r in sorted(by_attr.get(attr, []), key=_rank, reverse=True):
            if r["attribute_value"] not in seen:
                seen.add(r["attribute_value"])
                values.append(r["attribute_value"])
        card[attr] = values if attr == "sensitive_content" else values[:3]
    # "none" only stands when no real warning survives
    if len(card["sensitive_content"]) > 1:
        card["sensitive_content"] = [v for v in card["sensitive_content"] if v != "none"]
    # reader_level: staff decision wins; otherwise re-derive from the card
    staff_level = any(r["human_verified"] for r in by_attr.get("reader_level", []) if r["source_site"] != "derived")
    if not staff_level:
        derived = _derive_reader_level(card["language_difficulty"], card["perceived_length"],
                                       card["pace"], card["emotional_weight"])
        card["reader_level"] = derived
        conn.execute("""UPDATE attribute_evidence SET attribute_value = ?
                        WHERE book_id = ? AND attribute_name = 'reader_level' AND source_site = 'derived'
                              AND COALESCE(rejected, 0) = 0""", (derived, book_id))
    reviewed = [r for r in rows if r["source_site"] != "derived" and r["attribute_value"] != "unknown"]
    if not reviewed:
        status = None  # keep whatever it was (e.g. pending)
    elif all(r["human_verified"] for r in reviewed):
        status = "approved"
    else:
        status = "needs_review"
    conn.execute("INSERT OR IGNORE INTO book_profiles (book_id, overall_status) VALUES (?, 'pending')", (book_id,))
    conn.execute(
        """UPDATE book_profiles SET format = ?, genres = ?, perceived_length = ?, language_difficulty = ?,
                  pace = ?, moods = ?, emotional_weight = ?, reader_level = ?, use_cases = ?,
                  age_suitability = ?, sensitive_content = ?,
                  overall_status = COALESCE(?, overall_status)
           WHERE book_id = ?""",
        (card["format"] if card["format"] != "unknown" else None, json.dumps(card["genres"]),
         card["perceived_length"], card["language_difficulty"], card["pace"],
         json.dumps(card["moods"], ensure_ascii=False), card["emotional_weight"], card["reader_level"],
         json.dumps(card["use_cases"]),
         card["age_suitability"] if card["age_suitability"] != "unknown" else None,
         json.dumps(card["sensitive_content"]), status, book_id))
    conn.execute(f"UPDATE book_profiles SET {', '.join(a + ' = ?' for a in V2_SINGLE)} WHERE book_id = ?",
                 (*[card[a] if card[a] != "unknown" else None for a in V2_SINGLE], book_id))


def _insert_staff_row(conn, book_id, attr, value, reviewer, now):
    conn.execute(
        """INSERT INTO attribute_evidence (book_id, attribute_name, attribute_value, confidence,
                   evidence_summary, source_urls, source_site, edition_scope, review_count, conflict_seen,
                   analyzed_at, human_verified, verified_by, verified_at, rejected)
           VALUES (?, ?, ?, 1.0, 'حدّدها موظف المكتبة', '[]', 'staff', NULL, 0, 0, ?, 1, ?, ?, 0)""",
        (book_id, attr, value, now, reviewer, now))


def apply_action(book_id, attr, action, evidence_id=None, value=None, reviewer=None):
    if attr not in ALLOWED:
        raise ValueError("unknown attribute")
    if action not in ACTIONS:
        raise ValueError("unknown action")
    reviewer = (str(reviewer or "").strip() or "موظف")[:60]
    now = _now()
    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM books WHERE book_id = ?", (book_id,)).fetchone() is None:
            raise LookupError("book not found")
        if action in ("confirm", "reject", "restore"):
            row = conn.execute("SELECT * FROM attribute_evidence WHERE evidence_id = ? AND book_id = ? AND attribute_name = ?",
                               (evidence_id, book_id, attr)).fetchone()
            if row is None:
                raise ValueError("evidence not found for this book/attribute")
            if row["source_site"] == "derived":
                raise ValueError("derived values are recomputed automatically; use 'set' instead")
            if action == "confirm":
                if row["attribute_value"] == "unknown":
                    raise ValueError("cannot confirm an unknown value")
                conn.execute("""UPDATE attribute_evidence SET human_verified = 1, verified_by = ?, verified_at = ?,
                                rejected = 0 WHERE evidence_id = ?""", (reviewer, now, evidence_id))
            elif action == "reject":
                conn.execute("""UPDATE attribute_evidence SET rejected = 1, rejected_at = ?, rejected_by = ?,
                                human_verified = 0 WHERE evidence_id = ?""", (now, reviewer, evidence_id))
            else:
                conn.execute("UPDATE attribute_evidence SET rejected = 0, rejected_at = NULL, rejected_by = NULL WHERE evidence_id = ?",
                             (evidence_id,))
        else:
            if value not in ALLOWED[attr]:
                raise ValueError("value not allowed for this attribute")
            if action == "set":
                if attr not in SINGLE:
                    raise ValueError("use 'add' for multi-valued attributes")
                conn.execute("""UPDATE attribute_evidence SET rejected = 1, rejected_at = ?, rejected_by = ?
                                WHERE book_id = ? AND attribute_name = ? AND COALESCE(rejected, 0) = 0
                                      AND NOT (source_site = 'derived' AND attribute_name = 'reader_level')""",
                             (now, reviewer, book_id, attr))
                if attr == "reader_level":  # staff decision replaces the derived value
                    conn.execute("""UPDATE attribute_evidence SET rejected = 1, rejected_at = ?, rejected_by = ?
                                    WHERE book_id = ? AND attribute_name = 'reader_level' AND source_site = 'derived'""",
                                 (now, reviewer, book_id))
                _insert_staff_row(conn, book_id, attr, value, reviewer, now)
            else:  # add
                if attr not in MULTI:
                    raise ValueError("use 'set' for single-valued attributes")
                existing = conn.execute("""SELECT evidence_id FROM attribute_evidence WHERE book_id = ? AND attribute_name = ?
                                           AND attribute_value = ? ORDER BY COALESCE(rejected, 0), confidence DESC LIMIT 1""",
                                        (book_id, attr, value)).fetchone()
                if existing:
                    conn.execute("""UPDATE attribute_evidence SET human_verified = 1, verified_by = ?, verified_at = ?,
                                    rejected = 0, rejected_at = NULL, rejected_by = NULL WHERE evidence_id = ?""",
                                 (reviewer, now, existing["evidence_id"]))
                else:
                    _insert_staff_row(conn, book_id, attr, value, reviewer, now)
        recompute_card(conn, book_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
