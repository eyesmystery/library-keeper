"""Where a book is in the library (approved by Ibrahim 2026-09-27).

The Rewaa export has no location column, and a location must never be invented. A book's location is:
  1. the shelf/location a staff member typed on the book (books.shelf_location), else
  2. the ACTIVE library section that holds the book's category (library_sections), else
  3. nothing: the visitor is told to ask a staff member.
Sections are managed by the manager in the admin page (الإعدادات → أقسام المكتبة).
"""
import json
import re

from models import get_connection

_cache = {"sections": None}


def invalidate():
    _cache["sections"] = None


def all_sections():
    if _cache["sections"] is None:
        conn = get_connection()
        rows = [dict(r) for r in conn.execute("SELECT * FROM library_sections ORDER BY sort, section_id")]
        conn.close()
        for r in rows:
            try:
                r["genres"] = [g for g in json.loads(r["genres"] or "[]") if isinstance(g, str)]
            except ValueError:
                r["genres"] = []
            r["active"] = bool(r["active"])
        _cache["sections"] = rows
    return _cache["sections"]


def section_for_genre(genre):
    for s in all_sections():
        if s["active"] and genre and genre in s["genres"]:
            return s
    return None


def location(book):
    """-> (text, kind) with kind 'shelf' (typed by staff), 'section' or None."""
    shelf = (book.get("shelf_location") or "").strip()
    if shelf:
        return shelf, "shelf"
    s = section_for_genre(book.get("genre"))
    if s:
        return s["name"], "section"
    return None, None


def save_sections(items, known_genres):
    """Replace the section list. Raises ValueError (Arabic message) when invalid."""
    if not isinstance(items, list) or len(items) > 40:
        raise ValueError("قائمة الأقسام غير صحيحة")
    clean, used = [], {}
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise ValueError("قائمة الأقسام غير صحيحة")
        name = re.sub(r"\s+", " ", str(it.get("name") or "")).strip()
        if not 1 <= len(name) <= 60:
            raise ValueError("اسم القسم مطلوب (حتى ٦٠ حرفًا)")
        genres = [g for g in (it.get("genres") or []) if isinstance(g, str)]
        for g in genres:
            if g not in known_genres:
                raise ValueError(f"تصنيف غير معروف: {g}")
            if g in used:
                raise ValueError(f"التصنيف «{g}» موجود في قسمين: {used[g]} و{name}")
            used[g] = name
        clean.append((name, json.dumps(genres, ensure_ascii=False), 1 if it.get("active") else 0, i))
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM library_sections")
        conn.executemany("INSERT INTO library_sections (name, genres, active, sort) VALUES (?, ?, ?, ?)", clean)
    conn.close()
    invalidate()
    return all_sections()
