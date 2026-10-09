"""Staff answers for books whose identity needs confirmation (Ibrahim, 2026-10-04).

A book with research_status = 'needs_confirmation' shows a «تأكيد هوية الكتاب» section in its admin
card. The staff member, holding the printed copy, picks one of the known options (the candidates the
research found, «البيانات الحالية صحيحة», «ولا واحد منها», «النسخة غير موجودة على الرف») and/or writes
a comment. The answer is only STORED here: it does not change the book. The manager conversation
reads the open answers (scripts/confirmations.py) and corrects the record from them, then marks them handled.

One active answer per book: a new answer replaces the earlier one (kept as history, `undone_at` set);
«تراجع» removes the active answer. Nothing is ever deleted from this table by the app.
"""
import json
from datetime import datetime, timezone

from models import get_connection

FIXED = [("current", "البيانات الحالية صحيحة"),
         ("none", "ولا واحد من هذه — الصحيح في التعليق"),
         ("no_copy", "النسخة غير موجودة على الرف")]
COMMENT_MAX = 1000


def ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_confirmations (
            confirmation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            choice TEXT,
            choice_label TEXT,
            comment TEXT,
            created_at TEXT NOT NULL,
            created_by TEXT,
            undone_at TEXT,
            undone_by TEXT,
            handled_at TEXT,
            handled_note TEXT
        )
        """
    )


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _candidates(book):
    try:
        v = json.loads(book.get("research_candidates") or "[]")
    except (TypeError, ValueError):
        return []
    return [c for c in v if isinstance(c, dict) and (c.get("title") or c.get("author"))] if isinstance(v, list) else []


def options(book):
    """The known choices for one book: its research candidates first, then the fixed ones."""
    out = []
    for i, c in enumerate(_candidates(book)):
        parts = [x for x in (c.get("author"), c.get("publisher"), str(c["year"]) if c.get("year") else None) if x]
        url = c.get("url") if isinstance(c.get("url"), str) and c["url"].startswith(("http://", "https://")) else None
        out.append({"key": "candidate:%d" % i, "label": "«%s»" % (c.get("title") or "بدون عنوان"),
                    "detail": " · ".join(parts), "url": url})
    return out + [{"key": k, "label": label, "detail": "", "url": None} for k, label in FIXED]


def _row(r):
    return {"id": r["confirmation_id"], "choice": r["choice"], "label": r["choice_label"], "comment": r["comment"],
            "at": r["created_at"], "by": r["created_by"], "handled_at": r["handled_at"]} if r else None


def active(book_id, conn=None):
    own = conn is None
    conn = conn or get_connection()
    try:
        ensure_table(conn)
        return _row(conn.execute("SELECT * FROM book_confirmations WHERE book_id = ? AND undone_at IS NULL "
                                 "ORDER BY confirmation_id DESC LIMIT 1", (int(book_id),)).fetchone())
    finally:
        if own:
            conn.close()


def answered_ids():
    """book ids with an active answer (for the catalog list)."""
    conn = get_connection()
    try:
        ensure_table(conn)
        return {r[0] for r in conn.execute("SELECT DISTINCT book_id FROM book_confirmations WHERE undone_at IS NULL")}
    finally:
        conn.close()


def for_card(book):
    """What the admin card needs; None when the book needs no confirmation and has no answer."""
    cur = active(book["book_id"])
    if book.get("research_status") != "needs_confirmation" and not cur:
        return None
    return {"needed": book.get("research_status") == "needs_confirmation", "note": book.get("research_note_ar") or book.get("research_note"),
            "options": options(book), "current": cur}


def save(book, choice, comment, user_name):
    """Store an answer (a choice, a comment, or both). ValueError when empty or the choice is unknown."""
    if choice is not None and not isinstance(choice, str):
        raise ValueError("اختيار غير معروف")
    if comment is not None and not isinstance(comment, str):
        raise ValueError("التعليق يجب أن يكون نصًا")
    choice = (choice or "").strip() or None
    comment = " ".join(str(comment or "").split())[:COMMENT_MAX] or None
    labels = {o["key"]: (o["label"] + (" — " + o["detail"] if o["detail"] else "")) for o in options(book)}
    if choice and choice not in labels:
        raise ValueError("اختيار غير معروف")
    if not choice and not comment:
        raise ValueError("اختر واحدًا من الخيارات أو اكتب تعليقًا")
    if choice == "none" and not comment:
        raise ValueError("اكتب في التعليق ما هو الصحيح (العنوان والمؤلف كما على الغلاف)")
    conn = get_connection()
    try:
        ensure_table(conn)
        now = _now()
        conn.execute("UPDATE book_confirmations SET undone_at = ?, undone_by = ? WHERE book_id = ? AND undone_at IS NULL",
                     (now, user_name, book["book_id"]))
        conn.execute("INSERT INTO book_confirmations (book_id, choice, choice_label, comment, created_at, created_by) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (book["book_id"], choice, labels.get(choice), comment, now, user_name))
        conn.commit()
    finally:
        conn.close()


def undo(book_id, user_name):
    """Withdraw the active answer. LookupError when there is none or the manager already handled it."""
    conn = get_connection()
    try:
        ensure_table(conn)
        r = conn.execute("SELECT confirmation_id, handled_at FROM book_confirmations WHERE book_id = ? AND undone_at IS NULL "
                         "ORDER BY confirmation_id DESC LIMIT 1", (int(book_id),)).fetchone()
        if r is None:
            raise LookupError("no answer")
        if r["handled_at"]:
            raise ValueError("اعتُمد هذا التأكيد وصُحّح الكتاب عليه؛ اكتب تأكيدًا جديدًا إن كان فيه خطأ")
        conn.execute("UPDATE book_confirmations SET undone_at = ?, undone_by = ? WHERE confirmation_id = ?",
                     (_now(), user_name, r["confirmation_id"]))
        conn.commit()
    finally:
        conn.close()


def open_answers(include_handled=False):
    """Active answers with the book's current fields, oldest first (read by the manager conversation)."""
    conn = get_connection()
    try:
        ensure_table(conn)
        rows = conn.execute(
            "SELECT c.*, b.title, b.author, b.publisher, b.translator, b.research_status, b.research_note, b.research_candidates "
            "FROM book_confirmations c JOIN books b USING (book_id) WHERE c.undone_at IS NULL "
            + ("" if include_handled else "AND c.handled_at IS NULL ") + "ORDER BY c.confirmation_id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            cands = _candidates(d)
            d.pop("research_candidates")
            if (d["choice"] or "").startswith("candidate:"):
                i = int(d["choice"].split(":")[1])
                d["chosen_candidate"] = cands[i] if i < len(cands) else None
            out.append(d)
        return out
    finally:
        conn.close()


def mark_handled(confirmation_id, note=""):
    conn = get_connection()
    try:
        ensure_table(conn)
        n = conn.execute("UPDATE book_confirmations SET handled_at = ?, handled_note = ? WHERE confirmation_id = ? "
                         "AND undone_at IS NULL", (_now(), note or None, int(confirmation_id))).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Hand-over to the manager conversation when the app runs on the library's own computer (2026-10-04):
# the manager copies/downloads the open answers from the admin page (export_package), gives them to
# Claude, and brings back a corrections file that the admin page previews and applies (plan / apply).
# ---------------------------------------------------------------------------
import os
import re
import sqlite3

import models

FORMAT_OUT, FORMAT_IN = "book-confirmations-1", "confirmation-corrections-1"
FIELDS = {"title": "العنوان", "author": "المؤلف", "publisher": "الناشر", "translator": "المترجم",
          "original_language": "لغة الأصل", "publication_year": "سنة النشر", "page_count": "عدد الصفحات",
          "isbn": "ISBN", "research_status": "حالة البحث"}
STATUSES = ("matched", "needs_confirmation", "not_found")
_BOOK_OUT = ("title", "author", "publisher", "translator", "original_language", "language", "publication_year",
             "page_count", "isbn", "barcode", "rewaa_sku", "genre", "research_status", "research_note")
MAX_ITEMS = 500
_MAX_ID = 2 ** 63 - 1  # SQLite's largest integer


def export_package(include_handled=False):
    conn = get_connection()
    try:
        ensure_table(conn)
        answers = []
        for a in open_answers(include_handled):
            b = conn.execute("SELECT * FROM books WHERE book_id = ?", (a["book_id"],)).fetchone()
            answers.append({"confirmation_id": a["confirmation_id"], "book_id": a["book_id"], "choice": a["choice"],
                            "choice_label": a["choice_label"], "comment": a["comment"], "answered_at": a["created_at"],
                            "answered_by": a["created_by"], "handled_at": a["handled_at"],
                            "chosen_candidate": a.get("chosen_candidate"),
                            "book": {k: b[k] for k in _BOOK_OUT}, "has_summary": bool(b["summary"])})
        waiting = conn.execute("SELECT COUNT(*) FROM books WHERE research_status = 'needs_confirmation'").fetchone()[0]
    finally:
        conn.close()
    return {"format": FORMAT_OUT, "exported_at": _now(), "count": len(answers), "books_needing_confirmation": waiting,
            "answers": answers}


def _clean(field, value):
    """-> normalized value; ValueError with an Arabic message."""
    if field in ("publication_year", "page_count"):
        if value in (None, ""):
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not (0 < value < 10000):
            raise ValueError(f"{FIELDS[field]}: رقم غير صالح")
        return value
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{FIELDS[field]}: قيمة غير نصية")
    value = " ".join((value or "").split())[:300] or None
    if field in ("title", "author") and not value:
        raise ValueError(f"{FIELDS[field]}: لا يُترك فارغًا")
    if field == "research_status" and value not in STATUSES:
        raise ValueError("حالة البحث غير معروفة")
    if field == "original_language" and value and not re.fullmatch(r"[a-z]{2,5}", value):
        raise ValueError("لغة الأصل: رمز غير صالح")
    return value


def plan_corrections(package, conn=None):
    """Check a corrections file against the live records. -> {"ok", "error", "items": [...]} (nothing is written)."""
    if not isinstance(package, dict) or package.get("format") != FORMAT_IN or not isinstance(package.get("items"), list):
        return {"ok": False, "error": "هذا ليس ملف تصحيحات من Claude (الصيغة غير معروفة)", "items": []}
    if not package["items"] or len(package["items"]) > MAX_ITEMS:
        return {"ok": False, "error": "الملف فارغ أو أكبر من المسموح", "items": []}
    own = conn is None
    conn = conn or get_connection()
    out, seen = [], set()
    try:
        ensure_table(conn)
        for it in package["items"]:
            row = {"book_id": None, "title": "", "ok": False, "problem": None, "changes": [], "set": {},
                   "confirmation_id": None, "clear_summary": False, "note": ""}
            out.append(row)
            if not isinstance(it, dict) or isinstance(it.get("book_id"), bool) or not isinstance(it.get("book_id"), int):
                row["problem"] = "بند بدون رقم كتاب"; continue
            if not (0 < it["book_id"] <= _MAX_ID):  # beyond SQLite's integers the query itself fails (audit 2026-10-07)
                row["problem"] = "رقم الكتاب غير صالح"; continue
            row["book_id"] = it["book_id"]
            row["note"] = " ".join(str(it.get("note") or "").split())[:300]
            row["clear_summary"] = it.get("clear_summary") is True
            b = conn.execute("SELECT * FROM books WHERE book_id = ?", (it["book_id"],)).fetchone()
            if b is None:
                row["problem"] = "الكتاب غير موجود"; continue
            row["title"] = b["title"]
            if it["book_id"] in seen:
                row["problem"] = "الكتاب مكرر في الملف"; continue
            seen.add(it["book_id"])
            # The file must name the value it saw for the title, the author and every field it changes; any
            # of them different now means someone edited the book after the export (audit 2026-10-05).
            exp = it.get("expect") if isinstance(it.get("expect"), dict) else {}
            asked = it.get("set") if isinstance(it.get("set"), dict) else {}
            need = {"title", "author"} | {k for k in asked if k in FIELDS}
            missing = sorted(k for k in need if k not in exp)
            if missing:
                row["problem"] = ("الملف لا يذكر القيمة الأصلية لـ: " + "، ".join(FIELDS[k] for k in missing)
                                  + "؛ صدّر الردود من جديد"); continue
            stale = sorted(k for k in exp if k in FIELDS and exp[k] != b[k])
            if stale:
                row["problem"] = ("تغيّر سجل الكتاب بعد تصدير الردود (" + "، ".join(FIELDS[k] for k in stale)
                                  + ")؛ صدّر الردود من جديد"); continue
            cid = it.get("confirmation_id")
            if cid is not None:
                c = conn.execute("SELECT book_id, undone_at, handled_at FROM book_confirmations WHERE confirmation_id = ?",
                                 (cid,)).fetchone() if isinstance(cid, int) and not isinstance(cid, bool) and 0 < cid <= _MAX_ID else None
                if c is None or c["book_id"] != it["book_id"]:
                    row["problem"] = "رد الموظف غير موجود لهذا الكتاب"; continue
                if c["undone_at"]:
                    row["problem"] = "الموظف تراجع عن هذا الرد أو غيّره"; continue
                if c["handled_at"]:
                    row["problem"] = "هذا الرد طُبّق من قبل"; continue
                row["confirmation_id"] = cid
            sets = it.get("set") if isinstance(it.get("set"), dict) else {}
            bad = [k for k in sets if k not in FIELDS]
            if bad:
                row["problem"] = "حقل غير مسموح: " + "، ".join(map(str, bad)); continue
            try:
                for k, v in sets.items():
                    v = _clean(k, v)
                    if v != b[k]:
                        row["set"][k] = v
                        row["changes"].append({"field": k, "label": FIELDS[k], "old": b[k], "new": v})
            except ValueError as e:
                row["problem"] = str(e); continue
            if row["clear_summary"] and b["summary"]:
                row["changes"].append({"field": "summary", "label": "النبذة", "old": "موجودة", "new": "تُمسح (تخص كتابًا آخر)"})
            if row["clear_summary"]:
                n = conn.execute(_OLD_WORK_ROWS.replace("{what}", "SELECT COUNT(*) FROM attribute_evidence"),
                                 (it["book_id"],)).fetchone()[0]
                if n:
                    row["changes"].append({"field": "evidence", "label": "صفات الكتاب", "old": f"{n} صفة مستخرجة",
                                           "new": "تُلغى (تخص كتابًا آخر)"})
            if not row["changes"] and row["confirmation_id"] is None:
                row["problem"] = "لا يوجد تغيير"; continue
            row["ok"] = True
    finally:
        if own:
            conn.close()
    bad = sum(1 for r in out if not r["ok"])
    return {"ok": bad == 0, "error": f"{bad} بند فيه مشكلة؛ لا يُطبَّق شيء حتى تُحل" if bad else None, "items": out}


# Attribute rows that described the work the record used to name: everything a staff member has not
# confirmed. Confirmed rows stay: the staff member confirmed them against the copy on the shelf.
_OLD_WORK_ROWS = "{what} WHERE book_id = ? AND COALESCE(rejected, 0) = 0 AND COALESCE(human_verified, 0) = 0"


def _drop_old_work(conn, book_id, user_name, now):
    """The record now names another work (`clear_summary`): its extracted attributes, card and computed
    relations belonged to the old one and must not be used for the corrected book (audit 2026-10-05)."""
    import card_review
    conn.execute(_OLD_WORK_ROWS.replace("{what}", "UPDATE attribute_evidence SET rejected = 1, rejected_at = ?, rejected_by = ?"),
                 (now, "تصحيح الهوية: " + str(user_name or ""), book_id))
    card_review.recompute_card(conn, book_id)
    conn.execute("""UPDATE book_profiles SET themes = NULL,
                    overall_status = CASE WHEN EXISTS (SELECT 1 FROM attribute_evidence e WHERE e.book_id = book_profiles.book_id
                        AND COALESCE(e.rejected, 0) = 0 AND e.source_site <> 'derived' AND e.attribute_value <> 'unknown')
                        THEN overall_status ELSE 'pending' END
                    WHERE book_id = ?""", (book_id,))
    conn.execute("DELETE FROM book_relations WHERE source = 'computed' AND (source_book_id = ? OR target_book_id = ?)",
                 (book_id, book_id))


def _backup():
    """A consistent copy of the live database in backups/ before corrections are written."""
    folder = os.path.join(os.path.dirname(models.DB_PATH), "..", "backups")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "library-%s-corrections.db" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    src, out = sqlite3.connect(models.DB_PATH), sqlite3.connect(dest)
    try:
        src.backup(out)
        if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup failed the integrity check")
    finally:
        out.close(); src.close()
    return os.path.basename(dest)


def apply_corrections(package, user_name):
    """Backup, then write every item in one transaction. ValueError (with the plan) when any item is refused."""
    plan = plan_corrections(package)
    if not plan["ok"]:
        raise ValueError(plan)
    backup = _backup()
    conn = get_connection()
    try:
        ensure_table(conn)
        conn.execute("BEGIN IMMEDIATE")  # nobody writes between the re-check below and the commit
        plan = plan_corrections(package, conn)  # re-checked on the connection that writes
        if not plan["ok"]:
            raise ValueError(plan)
        now, day = _now(), datetime.now().strftime("%Y-%m-%d")
        for r in plan["items"]:
            if r["set"]:
                conn.execute("UPDATE books SET %s WHERE book_id = ?" % ", ".join(f"{k} = ?" for k in r["set"]),
                             (*r["set"].values(), r["book_id"]))
            if r["clear_summary"]:
                conn.execute("UPDATE books SET summary = NULL, summary_source = NULL, summary_updated_at = NULL, hook = NULL, "
                             "subjects = NULL, setting = NULL, subgenres = NULL WHERE book_id = ?", (r["book_id"],))
                _drop_old_work(conn, r["book_id"], user_name, now)
            if r["changes"]:
                old = conn.execute("SELECT research_note FROM books WHERE book_id = ?", (r["book_id"],)).fetchone()[0]
                was = "; ".join(f"{c['field']} was «{c['old']}»" for c in r["changes"] if c["field"] in ("title", "author", "publisher"))
                text = f"{day}: corrected from the staff confirmation" + (f" ({was})" if was else "") + (f" — {r['note']}" if r["note"] else "")
                conn.execute("UPDATE books SET research_note = ? WHERE book_id = ?", ((old + " | " if old else "") + text, r["book_id"]))
            if r["confirmation_id"] is not None:
                conn.execute("UPDATE book_confirmations SET handled_at = ?, handled_note = ? WHERE confirmation_id = ?",
                             (now, (r["note"] or "applied from a corrections file") + f" (by {user_name})", r["confirmation_id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    import authors  # people records follow the corrected author / translator text
    authors.sync()
    if any(r["clear_summary"] for r in plan["items"]):
        try:
            import relations  # links between books are computed again without the old work's card
            relations.rebuild()
        except Exception as exc:  # the corrections are already saved; relations are rebuilt at the next start too
            print("relations rebuild failed after corrections:", exc, flush=True)
    return {"backup": backup, "books": sum(1 for r in plan["items"] if r["changes"]),
            "handled": sum(1 for r in plan["items"] if r["confirmation_id"] is not None), "items": plan["items"]}
