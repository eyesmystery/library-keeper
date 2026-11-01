"""Bring the book edits made at the library into this computer's catalog (Ibrahim 2026-10-29: «وتعديلات الكتب اللي سويناها
في المكتبة؟»). Reads section 5 «تعديلات نفّذها الموظفون في المكتبة» of a pulled inbox file (data/inbox_received/<name>.md),
takes every «تعديل بيانات كتاب» field change (كان → صار), and per book and field keeps the first «كان» and the last «صار».

A change is applied only when this catalog still holds the library's «كان» (or the value is already the «صار»); otherwise it
is listed as a conflict and left alone. Stock fields (price, quantity, shown to visitors) are the library's own and are
skipped. Covers uploaded there and «تمت مراجعة الكتاب» marks are not in the file: they are listed only.

  backend/venv/bin/python scripts/apply_library_edits.py data/inbox_received/<name>.md            dry run (report)
  backend/venv/bin/python scripts/apply_library_edits.py data/inbox_received/<name>.md --apply    backup + write + audit
Every written value records its source: audit_log «library.edit_applied» with the inbox file, item number, who and when;
a summary written this way gets summary_source «library:<user> <date> (<file>)».
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import inbox_export as IE  # noqa: E402
import models  # noqa: E402

LABEL_FIELD = {v: k for k, v in IE.FIELD_AR.items()}
LANG_CODE = {v: k for k, v in IE.LANG_AR.items()}
SKIP = {"price", "quantity", "active", "name_ar", "name_latin", "aliases", "origin", "staff_verified"}
INTS = {"page_count", "publication_year", "original_year", "age_min", "age_max"}
BOOK_FIELDS = {"title", "author", "publisher", "genre", "page_count", "publication_year", "isbn", "translator", "language",
               "original_language", "shelf_location", "summary", "age_min", "age_max", "original_year",
               "original_year_source", "original_year_ref", "original_year_checked_at", "goodreads_url"}
HEAD = re.compile(r"^### 5\.(\d+) (?:★ )?(.+?) — .*\(book_id (\d+)\)\s*$")
BY = re.compile(r"^- بواسطة: (.+?) · (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*$")
CHANGE = re.compile(r"^- (.+?): كان (.*?) · صار (.*)$")


def value(field, text):
    text = text.strip()
    if text == "(فارغ)":
        return None
    if field in ("language", "original_language"):
        return LANG_CODE.get(text, text)
    if field in INTS:
        try:
            return int(text)
        except ValueError:
            return text
    return text


def parse(path):
    lines = open(path, encoding="utf-8").read().split("\n")
    i0 = next(i for i, l in enumerate(lines) if l.startswith("## 5."))
    i1 = next(i for i, l in enumerate(lines) if l.startswith("## 6."))
    items, cur = [], None
    for l in lines[i0:i1]:
        m = HEAD.match(l)
        if m:
            cur = {"n": int(m.group(1)), "action": m.group(2).strip(), "book_id": int(m.group(3)), "by": None, "at": None, "changes": []}
            items.append(cur)
            continue
        if l.startswith("### "):   # an item about no book (an event, «اختياراتنا»): its lines belong to no book
            cur = None
            continue
        if not cur:
            continue
        m = BY.match(l)
        if m:
            cur["by"], cur["at"] = m.group(1), m.group(2)
            continue
        m = CHANGE.match(l)
        if m and cur["action"] == "تعديل بيانات كتاب":
            label = m.group(1).strip()
            field = LABEL_FIELD.get(label, label)
            cur["changes"].append((field, value(field, m.group(2)), value(field, m.group(3))))
    return items


def same(a, b):
    na = None if a in (None, "") else str(a).strip()
    nb = None if b in (None, "") else str(b).strip()
    return na == nb


def plan(items, conn):
    per = {}   # (book_id, field) -> {first_was, last_now, refs}
    for it in items:
        for field, was, now in it["changes"]:
            if field in SKIP or field not in BOOK_FIELDS:
                continue
            k = (it["book_id"], field)
            if k not in per:
                per[k] = {"was": was, "now": now, "refs": []}
            per[k]["now"] = now
            per[k]["refs"].append({"n": it["n"], "by": it["by"], "at": it["at"]})
    apply_, conflicts, already = [], [], []
    for (bid, field), p in sorted(per.items()):
        row = conn.execute(f"SELECT {field} FROM books WHERE book_id = ?", (bid,)).fetchone()
        if row is None:
            conflicts.append((bid, field, "الكتاب غير موجود هنا", p))
            continue
        here = row[0]
        if same(here, p["now"]):
            already.append((bid, field, p))
        elif same(here, p["was"]):
            apply_.append((bid, field, here, p))
        else:
            conflicts.append((bid, field, here, p))
    covers = [(it["book_id"], it["n"], it["by"], it["at"]) for it in items if it["action"] in ("رفع غلاف", "جلب غلاف", "حذف غلاف")]
    return apply_, conflicts, already, covers


REVIEW_ACTIONS = {"تمت مراجعة الكتاب": "book.reviewed", "إلغاء مراجعة الكتاب": "book.unreviewed"}


def sync_format(conn, book_id, genre):
    """AGY-20261029-E2: a genre brought from the library moves the card's format with it, as the admin save does
    (app.patch_book: book_profiles.format and the unrejected format evidence follow the genre one to one)."""
    import app as A
    fmt = A.GENRE_FORMAT.get(genre or "", "other")
    conn.execute("INSERT OR IGNORE INTO book_profiles (book_id, overall_status) VALUES (?, 'pending')", (book_id,))
    conn.execute("UPDATE book_profiles SET format = ? WHERE book_id = ?", (fmt, book_id))
    conn.execute("""UPDATE attribute_evidence SET attribute_value = ?
                    WHERE book_id = ? AND attribute_name = 'format' AND COALESCE(rejected, 0) = 0
                          AND attribute_value <> ?""", (fmt, book_id, fmt))
    return fmt


def reviews_to_mirror(items, conn):
    """AGY-20261029-E2: «تمت مراجعة الكتاب» / «إلغاء المراجعة» done by the library's staff (inbox §5) -> the audit rows
    that put the same mark here (the book leaves «تحتاج مراجعة»). Only the mark: a staff review confirms no evidence
    (that is the developer's review). An item already mirrored (same book, action, who and when) is not repeated."""
    out = []
    for it in items:
        act = REVIEW_ACTIONS.get(it["action"])
        if not act:
            continue
        ref = f"{it['by']} {it['at']}"
        seen = conn.execute("SELECT 1 FROM audit_log WHERE action = ? AND target = ? AND details LIKE ?",
                            (act, str(it["book_id"]), f'%"library_ref": "{ref}"%')).fetchone()
        if not seen:
            out.append((it["book_id"], act, it["by"], it["at"], it["n"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inbox_md")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    name = os.path.basename(a.inbox_md)
    items = parse(a.inbox_md)
    conn = sqlite3.connect(models.DB_PATH)
    apply_, conflicts, already, covers = plan(items, conn)
    titles = {r[0]: r[1] for r in conn.execute("SELECT book_id, title FROM books")}
    print(f"{name}: {len(items)} items in §5, {sum(len(i['changes']) for i in items)} field changes")
    reviews = reviews_to_mirror(items, conn)
    print(f"to apply: {len(apply_)} · already the same here: {len(already)} · conflicts: {len(conflicts)} · covers (not in the file): {len(covers)} · reviews to mirror: {len(reviews)}")
    for bid, act, by, at, n in reviews:
        print(f"  REVIEW {bid} «{titles.get(bid, '?')}» {act} by {by} {at} (§5.{n})")
    for bid, field, here, p in apply_:
        print(f"  APPLY {bid} «{titles.get(bid, '?')}» {field}: {str(here)[:60]!r} → {str(p['now'])[:80]!r}  (§5.{','.join(str(r['n']) for r in p['refs'])} {p['refs'][-1]['by']})")
    for bid, field, here, p in conflicts:
        print(f"  CONFLICT {bid} «{titles.get(bid, '?')}» {field}: here {str(here)[:60]!r} · library was {str(p['was'])[:60]!r} → {str(p['now'])[:60]!r}")
    for bid, n, by, at in covers:
        print(f"  COVER {bid} «{titles.get(bid, '?')}» (§5.{n} {by} {at}) — the image stays at the library")
    if not a.apply:
        print("(dry run: nothing written)")
        return
    if apply_ or reviews:
        import shutil
        bdir = os.path.join(ROOT, "backups")
        os.makedirs(bdir, exist_ok=True)
        bk = os.path.join(bdir, f"library-before-library-edits-{time.strftime('%Y%m%d-%H%M%S')}.db")
        b = sqlite3.connect(bk)
        conn.backup(b)
        b.close()
        print("backup", bk)
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        with conn:
            for bid, field, here, p in apply_:
                last = p["refs"][-1]
                conn.execute(f"UPDATE books SET {field} = ? WHERE book_id = ?", (p["now"], bid))
                if field == "summary":
                    conn.execute("UPDATE books SET summary_source = ?, summary_updated_at = ? WHERE book_id = ?",
                                 (f"library:{last['by']} {last['at']} ({name})" if p["now"] else None, now, bid))
                if field == "genre":
                    sync_format(conn, bid, p["now"])
                if field == "goodreads_url":
                    conn.execute("UPDATE books SET goodreads_url_source = ?, goodreads_rating = NULL, goodreads_ratings_count = NULL, "
                                 "goodreads_rating_source = NULL, goodreads_rating_at = NULL WHERE book_id = ?",   # AUDIT 2026-10-31 P2
                                 (f"library:{last['by']} {last['at']} ({name})" if p["now"] else None, bid))
                conn.execute("INSERT INTO audit_log (at, username, action, target, details) VALUES (?, ?, ?, ?, ?)",
                             (now, "MGR (Claude)", "library.edit_applied", str(bid),
                              json.dumps({field: [here, p["now"]], "source": f"inbox {name} §5." + ",".join(str(r["n"]) for r in p["refs"]),
                                          "by_at_library": last["by"], "at_library": last["at"]}, ensure_ascii=False)))
            for bid, act, by, at, n in reviews:
                conn.execute("INSERT INTO audit_log (at, username, action, target, details) VALUES (?, ?, ?, ?, ?)",
                             (now, "MGR (Claude)", act, str(bid),
                              json.dumps({"by": f"{by} (المكتبة)", "library_ref": f"{by} {at}",
                                          "source": f"inbox {name} §5.{n}"}, ensure_ascii=False)))
        print(f"applied {len(apply_)} field changes · {len(reviews)} reviews mirrored")
        if any(f in ("author", "translator") for _, f, _, _ in apply_):
            import authors
            authors.sync()
            print("authors relinked")
        import library_sheet
        c2 = models.get_connection()
        print("shelf names normalized:", library_sheet.normalize_saved_locations(c2))
        c2.close()
    conn.close()


if __name__ == "__main__":
    main()
