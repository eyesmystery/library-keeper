"""«تنزيل البلاغات» (Ibrahim, 2026-10-09): the staff inbox in one Markdown file, to send to Ibrahim or to
work through with Claude: visitor reports about books, visitor suggestions, questions the chat could
not answer, and the staff's own requested edits. Read only: nothing is marked handled by the export.
"""
from datetime import datetime, timedelta, timezone

import open_questions
import staff_notes
import visitor_notes
from models import get_connection

RIYADH = timezone(timedelta(hours=3))
STATUS_AR = {"open": "مفتوح", "done": "تمت معالجته"}
SCOPE_AR = {"open": "المفتوحة فقط", "done": "التي تمت معالجتها فقط", "all": "كل البنود (المفتوحة والمعالجة)"}


def _when(value):
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(RIYADH).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value or "")


def _md(text):
    """One line of user text, safe inside a Markdown list item."""
    return " ".join(str(text or "").split()).replace("|", "¦")


def _all(fetch):
    rows, offset = [], 0
    while True:
        page, total = fetch(offset)
        rows += page
        offset += len(page)
        if not page or offset >= total:
            return rows


def _books(ids):
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    conn = get_connection()
    try:
        q = f"SELECT book_id, title, author, publisher, isbn, genre, quantity FROM books WHERE book_id IN ({','.join('?' * len(ids))})"
        return {r["book_id"]: dict(r) for r in conn.execute(q, ids)}
    finally:
        conn.close()


def _book_line(b, fallback_title, book_id):
    if not b:
        return f"«{_md(fallback_title) or 'كتاب محذوف'}» (book_id {book_id or '—'}، لم يعد في الكتالوج)"
    extra = "، ".join(x for x in (_md(b.get("author")), _md(b.get("publisher")), f"ISBN {b['isbn']}" if b.get("isbn") else "",
                                  _md(b.get("genre"))) if x)
    return f"«{_md(b['title'])}» — {extra} (book_id {b['book_id']})"


def build(status="open", generated_by=None):
    status = status if status in ("open", "done", "all") else "open"
    st = None if status == "all" else status
    reports = _all(lambda o: (visitor_notes.list_notes("report", st or "all", 300, o), visitor_notes.total("report", st or "all")))
    suggestions = _all(lambda o: (visitor_notes.list_notes("suggestion", st or "all", 300, o), visitor_notes.total("suggestion", st or "all")))
    questions = _all(lambda o: open_questions.list_rows(st or "all", o))
    edits = _all(lambda o: staff_notes.list_rows(st or "all", o))
    books = _books({n.get("book_id") for n in reports + edits})
    now = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M")

    out = [f"# صندوق «ثلاث الذكي» — تصدير {now} (بتوقيت الرياض)", "",
           f"- البنود: {SCOPE_AR[status]}",
           f"- صدّره: {_md(generated_by) or '—'}",
           f"- بلاغات الكتب: {len(reports)} · اقتراحات الزوار: {len(suggestions)} · أسئلة بلا جواب: {len(questions)} · تعديلات الموظفين: {len(edits)}",
           "",
           "> **لـ Claude:** هذا الملف من لوحة إدارة «ثلاث الذكي»، مساعد يقترح كتب مكتبة ثلاث المتوفرة. "
           "كل بند عن كتاب فيه رقمه في قاعدة البيانات (book_id). البلاغات والتعديلات أخطاء في بيانات الكتاب "
           "(العنوان، المؤلف، الغلاف، النبذة، التصنيف، المكان) يلزم التحقق منها قبل تصحيحها. «أسئلة بلا جواب» "
           "عبارات قصيرة من طلبات الزوار ما لقى لها الشات جوابًا في بياناتنا، مرتبة حسب عدد المرات. التصدير "
           "لا يغيّر شيئًا ولا يعلّم أي بند كمعالَج.", ""]

    out += [f"## 1. بلاغات الكتب من الزوار ({len(reports)})", ""]
    if not reports:
        out += ["لا يوجد.", ""]
    for i, n in enumerate(reports, 1):
        out += [f"### 1.{i} {_book_line(books.get(n.get('book_id')), n.get('book_title'), n.get('book_id'))}",
                f"- الخطأ في: {_md(n.get('reason_label') or n.get('reason') or '—')}"]
        if n.get("note"):
            out.append(f"- كلام الزائر: {_md(n['note'])}")
        out.append(f"- التاريخ: {_when(n.get('created_at'))} · الحالة: {STATUS_AR.get(n.get('status'), n.get('status'))}"
                   + (f" (عالجه {_md(n.get('handled_by'))} {_when(n.get('handled_at'))})" if n.get("status") == "done" else "")
                   + (" · من الواجهة الإنجليزية" if n.get("language") == "en" else ""))
        out.append("")

    out += [f"## 2. اقتراحات الزوار ({len(suggestions)})", ""]
    if not suggestions:
        out += ["لا يوجد.", ""]
    for i, n in enumerate(suggestions, 1):
        out.append(f"{i}. {_md(n.get('note'))} — {_when(n.get('created_at'))} · {STATUS_AR.get(n.get('status'), n.get('status'))}")
    out.append("")

    out += [f"## 3. أسئلة بلا جواب ({len(questions)})", "",
            "| # | العبارة | النوع | عن كتاب | عدد المرات | آخر مرة | الحالة |", "|---|---|---|---|---|---|---|"]
    for i, q in enumerate(questions, 1):
        out.append(f"| {i} | {_md(q.get('phrase'))} | {_md(q.get('kind_label'))} | {_md(q.get('book_title')) or '—'} | "
                   f"{q.get('times')} | {_when(q.get('last_at'))} | {STATUS_AR.get(q.get('status'), q.get('status'))} |")
    if not questions:
        out.append("| — | لا يوجد | | | | | |")
    out.append("")

    out += [f"## 4. تعديلات مطلوبة من الموظفين ({len(edits)})", ""]
    if not edits:
        out += ["لا يوجد.", ""]
    for i, n in enumerate(edits, 1):
        out += [f"### 4.{i} {_book_line(books.get(n.get('book_id')), n.get('book_title'), n.get('book_id'))}",
                f"- المطلوب: {_md(n.get('note'))}",
                f"- كتبه: {_md(n.get('created_by')) or '—'} · {_when(n.get('created_at'))} · الحالة: {STATUS_AR.get(n.get('status'), n.get('status'))}"
                + (f" (نفّذه {_md(n.get('handled_by'))} {_when(n.get('handled_at'))})" if n.get("status") == "done" else ""),
                ""]
    counts = {"reports": len(reports), "suggestions": len(suggestions), "questions": len(questions), "edits": len(edits)}
    return "\n".join(out).rstrip() + "\n", counts
