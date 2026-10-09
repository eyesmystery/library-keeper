"""«تنزيل البلاغات» (Ibrahim, 2026-10-09): the staff inbox in one Markdown file, to send to Ibrahim or to
work through with Claude: visitor reports about books, visitor suggestions, questions the chat could
not answer, the staff's own requested edits, and (2026-10-09) the edits staff made in the data, from
audit_log with before -> after. Read only: nothing is marked handled by the export.
"""
import json
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


# «تعديلات الموظفين في المكتبة» (Ibrahim, 2026-10-09: «التعديلات اللي تصير في المكتبة ابغاها تنحفظ في log وتنظم
# لملف التغييرات»): what staff changed in the data, read from audit_log (who, when, before -> after).
EDIT_DAYS = 30
FIELD_AR = {
    "language": "لغة الطبعة", "original_language": "لغة الأصل", "translator": "المترجم", "price": "السعر",
    "quantity": "الكمية", "active": "ظاهر للزوار", "shelf_location": "مكان الرف", "age_min": "أقل عمر",
    "age_max": "أعلى عمر", "summary": "النبذة", "name_ar": "الاسم", "name_latin": "الاسم اللاتيني",
    "aliases": "أسماء أخرى", "origin": "البلد", "staff_verified": "تأكيد الموظف",
}
SKIP_FIELDS = {"summary_source", "summary_updated_at", "age_source"}
ACTION_AR = {
    "book.update": "تعديل بيانات كتاب", "book.confirmation": "رد على «تأكيد هوية الكتاب»",
    "book.confirmation.undo": "تراجع عن رد «تأكيد الهوية»", "book.delete": "حذف من الكتالوج",
    "book.restore": "استرجاع منتج محذوف", "book.honor.add": "إضافة جائزة / عمل مقتبس",
    "book.honor.delete": "حذف جائزة / عمل مقتبس", "cover.upload": "رفع غلاف", "cover.delete": "حذف غلاف",
    "cover.fetch": "جلب غلاف", "author.update": "تعديل مؤلف", "author.merge": "دمج مؤلفين",
    "author.award.add": "إضافة جائزة لمؤلف", "author.award.delete": "حذف جائزة مؤلف",
    "event.add": "إضافة فعالية", "event.update": "تعديل فعالية", "event.delete": "حذف فعالية",
    "sections.set": "تعديل أقسام المكتبة",
    "confirmations.corrections": "تطبيق تصحيحات الهوية",
}
ATTR_AR = {"format": "النوع", "age_suitability": "العمر المناسب", "moods": "المزاج", "pace": "الإيقاع",
           "language_difficulty": "صعوبة اللغة", "perceived_length": "الطول", "ending_tone": "النهاية",
           "sensitive_content": "محتوى حساس", "emotional_weight": "الثقل العاطفي", "reader_level": "مستوى القارئ"}
REASON_AR = {"not_a_book": "ليس كتابًا"}
CARD_AR = {"confirm": "تأكيد", "reject": "رفض", "restore": "استرجاع", "set": "تعيين", "add": "إضافة"}


LANG_AR = {"ar": "العربية", "en": "الإنجليزية", "fr": "الفرنسية", "es": "الإسبانية", "de": "الألمانية", "ru": "الروسية",
           "it": "الإيطالية", "pt": "البرتغالية", "ja": "اليابانية", "zh": "الصينية", "ko": "الكورية", "tr": "التركية",
           "fa": "الفارسية", "ur": "الأردية", "hi": "الهندية", "sv": "السويدية", "no": "النرويجية", "da": "الدنماركية",
           "nl": "الهولندية", "pl": "البولندية", "cs": "التشيكية", "hu": "المجرية", "el": "اليونانية",
           "grc": "اليونانية القديمة", "he": "العبرية", "la": "اللاتينية", "bs": "البوسنية", "other": "مترجم، اللغة غير معروفة"}


def _field(key, v):
    if key in ("language", "original_language") and v:
        return LANG_AR.get(v, v)
    if key in ("active", "staff_verified") and v in (0, 1):
        return "نعم" if v else "لا"
    return v


def _value(v):
    if v is None or v == "" or v == []:
        return "(فارغ)"
    if isinstance(v, list):
        return "، ".join(_md(x) for x in v)
    return _md(v)


def _staff_edits(status):
    """-> (rows, since_text, previous_export_at). Edits of the last EDIT_DAYS days, or all with status=all."""
    conn = get_connection()
    try:
        prev = conn.execute("SELECT MAX(at) FROM audit_log WHERE action='inbox.export'").fetchone()[0]
        acts = list(ACTION_AR) + ["card.%"]
        where = " OR ".join("action LIKE ?" if a.endswith("%") else "action = ?" for a in acts)
        args = list(acts)
        sql = f"SELECT * FROM audit_log WHERE ({where})"
        since = None
        if status != "all":
            since = (datetime.now(timezone.utc) - timedelta(days=EDIT_DAYS)).isoformat()
            sql += " AND at >= ?"
            args.append(since)
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY log_id", args)]
    finally:
        conn.close()
    return rows, since, prev


def _edit_lines(r, books):
    try:
        d = json.loads(r.get("details") or "null")
    except ValueError:
        d = r.get("details")
    act, target = r["action"], r.get("target")
    label = ACTION_AR.get(act) or ("مراجعة صفة قرائية (" + CARD_AR.get(act[5:], act[5:]) + ")" if act.startswith("card.") else act)
    book_acts = act.startswith(("book.", "cover.", "card."))
    head = label
    if book_acts and target and str(target).isdigit():
        b = books.get(int(target))
        head += " — " + (_book_line(b, (d or {}).get("title") if isinstance(d, dict) else None, target))
    elif act.startswith("author.") and target:
        head += f" — author_id {_md(target)}"
    elif act.startswith("event.") and isinstance(d, dict):
        head += f" — «{_md(d.get('title'))}» {_md(d.get('date'))}"
    out = [head, f"- بواسطة: {_md(r.get('username')) or '—'} · {_when(r.get('at'))}"]
    if act in ("book.update", "author.update") and isinstance(d, dict):
        for k, pair in d.items():
            if k in SKIP_FIELDS or not isinstance(pair, list) or len(pair) != 2:
                continue
            out.append(f"- {FIELD_AR.get(k, k)}: كان {_value(_field(k, pair[0]))} · صار {_value(_field(k, pair[1]))}")
    elif act == "book.confirmation" and isinstance(d, dict):
        out.append(f"- الاختيار: {_md(d.get('choice'))}" + (f" · تعليق: {_md(d.get('comment'))}" if d.get("comment") else ""))
    elif act.startswith("card.") and isinstance(d, dict):
        out.append(f"- الصفة: {ATTR_AR.get(d.get('attribute'), _md(d.get('attribute')))}" + (f" · القيمة: {_md(d.get('value'))}" if d.get("value") not in (None, "") else ""))
    elif act == "book.delete" and isinstance(d, dict):
        out.append(f"- السبب: {REASON_AR.get(d.get('reason'), _md(d.get('reason')))}"
                   + (f" · ملاحظة: {_md(d.get('note'))}" if d.get("note") else ""))
    elif act == "book.restore":
        pass
    elif isinstance(d, dict) and d:
        out.append("- التفاصيل: " + _md(json.dumps(d, ensure_ascii=False))[:400])
    elif d:
        out.append("- التفاصيل: " + _md(d)[:400])
    return out


# «ملفات رواء المستوردة» (Ibrahim 2026-10-09: «ابغى اعرف اذا تم تحميل ملف من رواء واضيف في المكتبة»)
CHANGE_AR = {"price": "السعر", "quantity": "الكمية", "shelf_location": "الرف", "barcode": "الباركود",
             "rewaa_sku": "رمز رواء", "publisher": "الدار"}
LIST_CAP = 150


def _imports(status):
    conn = get_connection()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(import_runs)")}
        sql, args = "SELECT * FROM import_runs", []
        if status != "all":
            sql += " WHERE applied_at >= ?"
            args.append((datetime.now(timezone.utc) - timedelta(days=EDIT_DAYS)).isoformat())
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY run_id", args)]
    finally:
        conn.close()
    return rows if cols else []


def _import_lines(run):
    out = [f"- الملف: {_md(run.get('file_name')) or '—'} · بواسطة: {_md(run.get('applied_by')) or '—'} · {_when(run.get('applied_at'))}",
           f"- الأسطر المقروءة: {run.get('rows_read') or 0} · كتب جديدة أُضيفت: {run.get('added') or 0} · كتب تغيّرت بياناتها: "
           f"{run.get('updated') or 0} · بلا تغيير: {run.get('unchanged') or 0} · صارت غير متوفرة (ليست في الملف): "
           f"{run.get('marked_out_of_stock') or 0} · أسطر مرفوضة: {run.get('invalid') or 0}", ""]
    try:
        d = json.loads(run.get("details") or "null")
    except ValueError:
        d = None
    if not isinstance(d, dict):
        return out + ["تفاصيل الكتب غير محفوظة لهذا الاستيراد (قبل الإصدار 0.9.8).", ""]

    def capped(items, fmt):
        lines = [fmt(x) for x in items[:LIST_CAP]]
        if len(items) > LIST_CAP:
            lines.append(f"- … و{len(items) - LIST_CAP} غيرها")
        return lines

    if d.get("new"):
        out += [f"**كتب جديدة أُضيفت للمكتبة ({len(d['new'])}):**", ""]
        out += capped(d["new"], lambda b: f"- «{_md(b.get('title'))}» — {_md(b.get('author')) or 'غير معروف'}"
                      + (f"، {_md(b.get('publisher'))}" if b.get("publisher") else "")
                      + f" · الكمية {b.get('quantity') if b.get('quantity') is not None else '—'}"
                      + f" · السعر {b.get('price') if b.get('price') is not None else '—'} (book_id {b.get('book_id')})")
        out.append("")
    ups = d.get("updated") or []
    if ups:
        per = {}
        for u in ups:
            for k in (u.get("changes") or {}):
                per[k] = per.get(k, 0) + 1
        out += [f"**تغيّرت بياناتها ({len(ups)}):** " + " · ".join(f"{CHANGE_AR.get(k, k)} {n}" for k, n in sorted(per.items(), key=lambda x: -x[1])), ""]
        other = [u for u in ups if set(u.get("changes") or {}) - {"quantity"}]
        out += capped(other, lambda u: f"- «{_md(u.get('title'))}» (book_id {u.get('book_id')}): "
                      + "، ".join(f"{CHANGE_AR.get(k, k)} {_value(v[0])} ← {_value(v[1])}" for k, v in (u.get("changes") or {}).items()))
        if len(other) < len(ups):
            out.append(f"- و{len(ups) - len(other)} كتابًا تغيّرت كميتها فقط (لم تُسرد)")
        out.append("")
    if d.get("zeroed"):
        out += [f"**صارت غير متوفرة لأنها ليست في ملف رواء ({len(d['zeroed'])}):**", ""]
        out += capped(d["zeroed"], lambda b: f"- «{_md(b.get('title'))}» (book_id {b.get('book_id')}، كانت الكمية {b.get('quantity')})")
        out.append("")
    if d.get("invalid"):
        out += [f"**أسطر مرفوضة ({len(d['invalid'])}):**", ""]
        out += capped(d["invalid"], lambda r: f"- سطر {r.get('row')}: «{_md(r.get('title'))}» — {_md('، '.join(r.get('problems') or []))}")
        out.append("")
    return out


def build(status="open", generated_by=None):
    status = status if status in ("open", "done", "all") else "open"
    st = None if status == "all" else status
    reports = _all(lambda o: (visitor_notes.list_notes("report", st or "all", 300, o), visitor_notes.total("report", st or "all")))
    suggestions = _all(lambda o: (visitor_notes.list_notes("suggestion", st or "all", 300, o), visitor_notes.total("suggestion", st or "all")))
    questions = _all(lambda o: open_questions.list_rows(st or "all", o))
    edits = _all(lambda o: staff_notes.list_rows(st or "all", o))
    changes, since, prev_export = _staff_edits(status)
    imports = _imports(status)
    change_ids = {int(c["target"]) for c in changes
                  if c["action"].startswith(("book.", "cover.", "card.")) and str(c.get("target") or "").isdigit()}
    books = _books({n.get("book_id") for n in reports + edits} | change_ids)
    now = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M")

    out = [f"# صندوق «ثلاث الذكي» — تصدير {now} (بتوقيت الرياض)", "",
           f"- البنود: {SCOPE_AR[status]}",
           f"- صدّره: {_md(generated_by) or '—'}",
           f"- بلاغات الكتب: {len(reports)} · اقتراحات الزوار: {len(suggestions)} · أسئلة بلا جواب: {len(questions)} · تعديلات مطلوبة: {len(edits)} · تعديلات نفّذها الموظفون: {len(changes)} · ملفات رواء المستوردة: {len(imports)}",
           "",
           "> **لـ Claude:** هذا الملف من لوحة إدارة «ثلاث الذكي»، مساعد يقترح كتب مكتبة ثلاث المتوفرة. "
           "كل بند عن كتاب فيه رقمه في قاعدة البيانات (book_id). البلاغات والتعديلات أخطاء في بيانات الكتاب "
           "(العنوان، المؤلف، الغلاف، النبذة، التصنيف، المكان) يلزم التحقق منها قبل تصحيحها. «أسئلة بلا جواب» "
           "عبارات قصيرة من طلبات الزوار ما لقى لها الشات جوابًا في بياناتنا، مرتبة حسب عدد المرات. التصدير "
           "لا يغيّر شيئًا ولا يعلّم أي بند كمعالَج. القسم 5 سجلّ ما غيّره الموظفون فعلًا في البيانات (قبل ← بعد)، والقسم 6 ملفات رواء المستوردة وما أضافته وغيّرته، "
           "وهذه التعديلات محفوظة في قاعدة المكتبة ويحتفظ بها التحديث القادم.", ""]

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
    out += [f"## 5. تعديلات نفّذها الموظفون في المكتبة ({len(changes)})", "",
            ("- المدة: كل السجل" if since is None else f"- المدة: آخر {EDIT_DAYS} يومًا (من {_when(since)})")
            + (f" · ★ = جديد بعد آخر تصدير ({_when(prev_export)})" if prev_export else ""), ""]
    if not changes:
        out += ["لا يوجد.", ""]
    for i, c in enumerate(changes, 1):
        lines = _edit_lines(c, books)
        star = " ★" if prev_export and str(c.get("at") or "") > str(prev_export) else ""
        out += [f"### 5.{i}{star} {lines[0]}"] + lines[1:] + [""]
    out += [f"## 6. ملفات رواء المستوردة ({len(imports)})", "",
            "- المدة: كل السجل" if since is None else f"- المدة: آخر {EDIT_DAYS} يومًا", ""]
    if not imports:
        out += ["لا يوجد.", ""]
    for i, run in enumerate(imports, 1):
        star = " ★" if prev_export and str(run.get("applied_at") or "") > str(prev_export) else ""
        out += [f"### 6.{i}{star} استيراد {_when(run.get('applied_at'))}"] + _import_lines(run)
    counts = {"reports": len(reports), "suggestions": len(suggestions), "questions": len(questions), "edits": len(edits),
              "staff_changes": len(changes), "imports": len(imports)}
    return "\n".join(out).rstrip() + "\n", counts
