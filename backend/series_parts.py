"""The full list of a series' parts (backend/series_parts.json), so that Manar can say how many parts a series has and
name the parts we do not stock, instead of «ما عندي تفاصيل» (library open questions 15 «اسم الريح» and 17
«كينغسبريدج»; Ibrahim 2026-10-17: «تمام اتفق» / «اتفق»). Titles come only from the file, never from the model."""
import json
import os

import chat_rules

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "series_parts.json")
_cache = {"mtime": None, "data": []}


def _load():
    try:
        m = os.path.getmtime(PATH)
        if _cache["mtime"] != m:
            with open(PATH, encoding="utf-8") as f:
                _cache.update(mtime=m, data=[s for s in (json.load(f).get("series") or []) if s.get("parts")])
    except (OSError, ValueError):
        _cache.update(mtime=None, data=[])
    return _cache["data"]


def for_book(book):
    """The series entry of this book (by its series_name, or its title being a part's Arabic title) -> dict or None."""
    if not book:
        return None
    keys = {chat_rules.title_key(book.get("series_name") or ""), chat_rules.title_key(book.get("title") or "")} - {""}
    for s in _load():
        names = {chat_rules.title_key(n) for n in s.get("names") or []}
        parts = {chat_rules.title_key(p.get("title_ar") or "") for p in s["parts"]} - {""}
        if keys & names or (chat_rules.title_key(book.get("title") or "") in parts):
            return s
    return None


def _ours(part, series_books):
    """Our book for a part: the same Arabic title, else the same series position."""
    tk = chat_rules.title_key(part.get("title_ar") or "")
    for b in series_books:
        if tk and chat_rules.title_key(b.get("title") or "") == tk:
            return b
    for b in series_books:
        if part.get("n") and b.get("series_position") == part["n"]:
            return b
    return None


def describe(entry, series_books, is_available, language="ar"):
    """-> one line listing every part with whether we have it in stock, for the model's guidance."""
    out = []
    for p in entry["parts"]:
        b = _ours(p, series_books)
        if language == "en":
            name = p.get("title_en") or p.get("title_ar")
            label = p.get("label_en") or f"part {p['n']}"
            st = "in stock" if b and is_available(b) else ("not in stock now" if b else "not in our catalog")
        else:
            name = p.get("title_ar") or p.get("title_en")
            label = p.get("label_ar") or f"الجزء {p['n']}"
            st = "متوفر عندنا" if b and is_available(b) else ("غير متوفر الآن" if b else "غير موجود عندنا")
        out.append(f"{label}: «{name}» ({p.get('year')}، {st})" if language != "en" else f"{label}: «{name}» ({p.get('year')}, {st})")
    note = entry.get("note_en" if language == "en" else "note_ar") or ""
    return ("; " if language == "en" else "؛ ").join(out) + ((" — " + note) if note else "")


def count(entry):
    return len(entry["parts"])
