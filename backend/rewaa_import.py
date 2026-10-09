"""Import / sync the catalog from a Rewaa (رواء) product export (approved 2026-09-25).

Flow (admin page → "استيراد من رواء"):
  1. upload a CSV or Excel (.xlsx) export  -> parse, suggest a column mapping
  2. choose which columns map to our fields (others, e.g. tax type or product
     category, are simply ignored)          -> dry-run plan (nothing written)
  3. apply                                   -> one transaction + an import_runs record

Matching order for each row: barcode/ISBN -> Rewaa SKU -> same title (+ author).
Existing books: only inventory fields change (price, quantity, shelf) and missing
identifiers are filled in; titles, authors and reading attributes are never
overwritten. New books are added with a `pending` card (no reading attributes
until review-based enrichment runs).

No third-party dependency: .xlsx is read with zipfile + xml.etree.
"""
import json
import csv
import io
from decimal import Decimal, InvalidOperation
import re
import secrets
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import deleted_books
from models import get_connection

MAX_ROWS = 20000
UPLOAD_TTL = 30 * 60
_uploads = {}  # upload_id -> {"at", "file_name", "headers", "rows"}

# Our target fields. Everything else in the export is ignored.
FIELDS = {
    "title": {"label": "اسم الكتاب", "required": True},
    "barcode": {"label": "الباركود / ISBN", "required": False},
    "sku": {"label": "رمز المنتج (SKU)", "required": False},
    "author": {"label": "المؤلف", "required": False},
    "publisher": {"label": "دار النشر", "required": False},
    "price": {"label": "سعر البيع", "required": False},
    "quantity": {"label": "الكمية المتوفرة", "required": False},
    "shelf": {"label": "الرف / الموقع", "required": False},
}
# Header hints (normalized, substring match) for the automatic suggestion.
HINTS = {
    "title": ["اسم المنتج", "اسم الكتاب", "المنتج", "الاسم", "product name", "name", "title"],
    "barcode": ["باركود", "الباركود", "barcode", "isbn", "ean", "gtin"],
    "sku": ["sku", "رمز المنتج", "كود المنتج", "رمز", "product code", "code"],
    "author": ["المؤلف", "مؤلف", "الكاتب", "author"],
    # In this library's Rewaa account the "Category" column holds the publisher (checked on the
    # real export, 2026-09-28); it is never used as a genre. English "category" must match exactly
    # so Arabic "فئة المنتج" (product type) is never mapped.
    "publisher": ["دار النشر", "الناشر", "publisher", "=category"],
    "price": ["سعر البيع", "السعر", "سعر", "retail price", "selling price", "price"],
    "quantity": ["الكميه المتوفره", "الكميه", "المخزون", "كميه", "quantity", "qty", "stock", "on hand"],
    "shelf": ["الرف", "الموقع", "رف", "location", "shelf", "bin"],
}
# Headers that must never be auto-mapped (the user said these are not needed).
IGNORE_HINTS = ["ضريب", "tax", "vat", "فئه", "category", "تكلفه", "cost", "مورد", "supplier",
                "buy price", "wholesale", "online price", "سعر الشراء", "سعر الجمله"]

# Non-book products sold in the same shop (cafe menu, stationery). Rows matching these are
# excluded from the catalog and listed in the plan with the reason (2026-09-28).
NON_BOOK_CATEGORY_WORDS = ["مشروبات", "drinks", "cafe", "كافيه", "الاضافات", "add-ons", "موهيتو", "mojito",
                           "ماتشا", "matcha", "refreshers", "المنعشات", "ادوات مكتبيه", "stationery"]
# Only for rows without a category: clear stationery / cafe names.
NON_BOOK_NAME_RE = re.compile(
    r"^(قلم|اقلام|طقم اقلام|مسطره|مقلمه|شنط|كروت هدايا|فواصل|ماء )|"
    r"\b(sticky|sticker|marker|pencils?|pen|coffee|coffe|decaf|cake|notebook|planner)\b|كيك")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")


def _norm_header(text):
    t = str(text or "").strip().lower()
    t = re.sub("[إأآٱ]", "ا", t).replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"\s+", " ", t)


def norm_title(text):
    t = _norm_header(text)
    t = re.sub(r"[ً-ْـ]", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def norm_code(value):
    """Barcode/ISBN: keep digits and X (ISBN-10 check digit), drop spaces/hyphens.
    Excel often turns long codes into floats like 9789953686516.0 or 9.78995E+12."""
    s = str(value or "").strip().translate(_ARABIC_DIGITS)
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    m = re.fullmatch(r"(\d)(?:\.(\d+))?[eE]\+?(\d+)", s)
    if m:
        # Scientific notation. Rewaa's CSV export writes codes this way with all significant
        # digits (trailing zeros dropped: 3.2456789113E11 = 324567891130), so it is exact when
        # at least 10 significant digits are present. Short forms such as Excel's display
        # "9.78995E+12" have lost digits and are refused.
        digits = m.group(1) + (m.group(2) or "")
        exp = int(m.group(3))
        if len(digits) > exp + 1 or exp + 1 > 15 or len(digits) < min(exp + 1, 10):
            return ""
        try:
            s = str(int(Decimal(s)))
        except InvalidOperation:
            return ""
    s = re.sub(r"[^0-9Xx]", "", s).upper()
    return s if len(s) >= 6 else ""


def norm_sku(value):
    """Rewaa SKUs arrive as floats in CSV exports ("10170.0")."""
    s = str(value or "").strip().translate(_ARABIC_DIGITS)
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return s[:60]


def is_isbn(code):
    """True for a checksum-valid ISBN-13 (978/979) or ISBN-10. Rewaa's own internal barcodes are not."""
    c = str(code or "")
    if re.fullmatch(r"97[89]\d{10}", c):
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(c[:12]))
        return (10 - total % 10) % 10 == int(c[12])
    if re.fullmatch(r"\d{9}[\dX]", c) and not c.startswith(("978", "979")):  # 978/979… of 10 digits = a cut ISBN-13
        total = sum((10 - i) * (10 if d == "X" else int(d)) for i, d in enumerate(c))
        return total % 11 == 0
    return False


def clean_title(text):
    """Keep Rewaa's name as it is, only joining broken lines and extra spaces. Number-only
    titles are real books (1984, 2666, 4321) that the CSV export wrote as floats ("1984.0")."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t.split(".")[0] if re.fullmatch(r"\d+\.0+", t) else t


def clean_publisher(text):
    """The Category column holds publishers for Arabic books but genre labels ("Fiction",
    "Manga (Fantasy)") for English imports; only real publisher names are kept."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t or _ARABIC_RE.search(t):
        return t[:120]
    return t[:120] if t.isupper() and len(t) <= 30 else ""


def guess_language(title, isbn=""):
    if _ARABIC_RE.search(title or ""):
        return "ar"
    if _LATIN_RE.search(title or "") or str(isbn).startswith(("9780", "9781")):  # 978-0/978-1 = English-language ISBNs
        return "en"
    return "ar"


def non_book_reason(title, category, stock_managed):
    cat = _norm_header(category)
    if stock_managed is not None and _norm_header(stock_managed) in ("no", "لا", "false", "0"):
        return "منتج بدون مخزون (قائمة الكافيه أو خدمة)"
    if cat and any(w in cat for w in NON_BOOK_CATEGORY_WORDS):
        return f"ليس كتابًا (الفئة: {category.strip()})"
    if not cat and NON_BOOK_NAME_RE.search(_norm_header(title)):
        return "ليس كتابًا (قرطاسية أو كافيه حسب الاسم)"
    return None


def parse_number(value):
    if value is None:
        return None
    s = str(value).strip().translate(_ARABIC_DIGITS)
    s = re.sub(r"(ر\.?س|sar|ريال|﷼)", "", s, flags=re.I).strip()
    if s.count(",") and s.count("."):
        s = s.replace(",", "")
    elif s.count(",") == 1 and len(s.split(",")[1]) in (1, 2):
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


# ------------------------------------------------------------------ file readers

def _read_csv(data):
    for enc in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


def _col_index(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read_xlsx(data):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        # first worksheet in workbook order
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        first = wb.find("m:sheets/m:sheet", ns)
        target = "xl/worksheets/sheet1.xml"
        if first is not None and "xl/_rels/workbook.xml.rels" in z.namelist():
            rid = first.get(f"{{{ns['r']}}}id")
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for rel in rels.findall("rel:Relationship", ns):
                if rel.get("Id") == rid:
                    t = rel.get("Target").lstrip("/")
                    target = t if t.startswith("xl/") else "xl/" + t
        sheet = ET.fromstring(z.read(target))
    rows = []
    for row in sheet.iter(f"{{{ns['m']}}}row"):
        cells = {}
        for c in row.findall("m:c", ns):
            ref, typ = c.get("r"), c.get("t")
            v = c.find("m:v", ns)
            if typ == "s" and v is not None:
                val = shared[int(v.text)]
            elif typ == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t"))
            elif typ == "b" and v is not None:
                val = "TRUE" if v.text == "1" else "FALSE"
            else:
                val = v.text if v is not None else ""
            cells[_col_index(ref) if ref else len(cells)] = val
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
    return rows


def read_table(file_name, data):
    name = (file_name or "").lower()
    if name.endswith(".xlsx"):
        try:
            rows = _read_xlsx(data)
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
            raise ValueError("تعذر قراءة ملف Excel") from e
    elif name.endswith((".csv", ".txt", ".tsv")):
        rows = _read_csv(data)
    elif name.endswith(".xls"):
        raise ValueError("صيغة xls القديمة غير مدعومة؛ احفظ الملف بصيغة xlsx أو CSV")
    else:
        raise ValueError("الملف لازم يكون CSV أو Excel (xlsx)")
    rows = [[str(c).strip() for c in r] for r in rows if any(str(c).strip() for c in r)]
    if len(rows) < 2:
        raise ValueError("الملف ما فيه بيانات كافية (يحتاج صف عناوين وصف واحد على الأقل)")
    header_row = max(range(min(5, len(rows))), key=lambda i: sum(1 for c in rows[i] if c))  # tolerate title rows
    headers = [h or f"عمود {i + 1}" for i, h in enumerate(rows[header_row])]
    body = rows[header_row + 1:][:MAX_ROWS]
    width = len(headers)
    body = [(r + [""] * width)[:width] for r in body]
    return headers, body


def suggest_mapping(headers):
    normalized = [_norm_header(h) for h in headers]
    mapping, used = {}, set()
    for field in FIELDS:
        for hint in HINTS[field]:
            h = _norm_header(hint)
            exact = h.startswith("=")
            if exact:
                h = h[1:]
            for i, name in enumerate(normalized):
                if i in used or (not exact and any(x in name for x in IGNORE_HINTS)):
                    continue
                if name == h or (not exact and h in name):
                    mapping[field] = i
                    used.add(i)
                    break
            if field in mapping:
                break
    return mapping


# ------------------------------------------------------------------ upload cache

def store_upload(file_name, data):
    headers, rows = read_table(file_name, data)
    now = time.time()
    for key in [k for k, v in _uploads.items() if now - v["at"] > UPLOAD_TTL]:
        _uploads.pop(key, None)
    upload_id = secrets.token_urlsafe(12)
    _uploads[upload_id] = {"at": now, "file_name": file_name, "headers": headers, "rows": rows}
    return {"upload_id": upload_id, "file_name": file_name, "headers": headers,
            "row_count": len(rows), "sample": rows[:5], "suggested": suggest_mapping(headers),
            "fields": FIELDS}


def _get_upload(upload_id):
    up = _uploads.get(upload_id)
    if not up:
        raise ValueError("انتهت صلاحية الملف المرفوع؛ ارفعه مرة ثانية")
    return up


def _clean_mapping(mapping, width):
    out = {}
    for field, idx in (mapping or {}).items():
        if field in FIELDS and idx not in (None, "", -1):
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < width:
                out[field] = idx
    if "title" not in out:
        raise ValueError("لازم تحدد عمود اسم الكتاب")
    if not ({"barcode", "sku"} & set(out)):
        # title-only matching is allowed but weaker; reported in the plan
        pass
    return out


def _aux_columns(headers):
    """Columns read automatically (never mapped by staff) to recognise non-book products."""
    out = {}
    for i, h in enumerate(headers):
        n = _norm_header(h)
        if "category" == n or n in ("الفئه", "التصنيف"):
            out.setdefault("category", i)
        if "stock management" in n or "اداره المخزون" in n or "تتبع المخزون" in n:
            out.setdefault("stock_managed", i)
    return out


def _row_values(row, mapping):
    get = lambda f: row[mapping[f]].strip() if f in mapping else ""
    qty = parse_number(get("quantity")) if "quantity" in mapping else None
    price = parse_number(get("price")) if "price" in mapping else None
    return {
        "title": clean_title(get("title")), "author": get("author"),
        "publisher": clean_publisher(get("publisher")) if "publisher" in mapping else "",
        "barcode": norm_code(get("barcode")) if "barcode" in mapping else "",
        "sku": norm_sku(get("sku")),
        "price": price, "quantity": None if qty is None else int(max(qty, 0)),
        "shelf": get("shelf")[:20],
        "raw_price": get("price"), "raw_quantity": get("quantity"),
    }


# ------------------------------------------------------------------ plan / apply

def build_plan(upload_id, mapping, missing_mode="zero"):
    up = _get_upload(upload_id)
    mapping = _clean_mapping(mapping, len(up["headers"]))
    conn = get_connection()
    try:
        books = [dict(r) for r in conn.execute("SELECT * FROM books")]
        del_codes, del_skus, del_titles = deleted_books.import_skip_keys(conn)
    finally:
        conn.close()
    del_titles = {norm_title(t) for t in del_titles}
    by_barcode = {b["barcode"]: b for b in books if b.get("barcode")}
    by_isbn = {norm_code(b.get("isbn")): b for b in books if norm_code(b.get("isbn"))}
    by_sku = {b["rewaa_sku"]: b for b in books if b.get("rewaa_sku")}
    by_title = {}
    for b in books:
        by_title.setdefault(norm_title(b["title"]), []).append(b)

    aux = _aux_columns(up["headers"])
    new, updated, unchanged, invalid, excluded, seen_ids, seen_keys = [], [], [], [], [], set(), set()
    for n, row in enumerate(up["rows"], start=2):
        v = _row_values(row, mapping)
        reason = non_book_reason(v["title"], row[aux["category"]] if "category" in aux else "",
                                 row[aux["stock_managed"]] if "stock_managed" in aux else None)
        if reason:
            excluded.append({"row": n, "title": v["title"], "reason": reason})
            continue
        if ((v["barcode"] and v["barcode"] in del_codes) or (v["sku"] and v["sku"] in del_skus)
                or (not v["barcode"] and not v["sku"] and norm_title(v["title"]) in del_titles)):
            excluded.append({"row": n, "title": v["title"], "reason": "محذوف يدويًا (ليس كتابًا) — يُسترجع من «المحذوفة»"})
            continue
        problems = []
        if not v["title"]:
            problems.append("بدون اسم")
        if "price" in mapping and v["raw_price"] and v["price"] is None:
            problems.append(f"سعر غير مفهوم «{v['raw_price']}»")
        if "price" in mapping and v["price"] is not None and v["price"] < 0:
            problems.append("سعر سالب")
        if "quantity" in mapping and v["raw_quantity"] and v["quantity"] is None:
            problems.append(f"كمية غير مفهومة «{v['raw_quantity']}»")
        key = v["barcode"] or v["sku"] or norm_title(v["title"])
        if key and key in seen_keys:
            problems.append("مكرر في الملف")
        if problems:
            invalid.append({"row": n, "title": v["title"], "problems": problems})
            continue
        seen_keys.add(key)

        match, how = None, None
        if v["barcode"] and (v["barcode"] in by_barcode or v["barcode"] in by_isbn):
            match, how = by_barcode.get(v["barcode"]) or by_isbn.get(v["barcode"]), "الباركود"
        elif v["sku"] and v["sku"] in by_sku:
            match, how = by_sku[v["sku"]], "رمز المنتج"
        else:
            cands = by_title.get(norm_title(v["title"]), [])
            if v["author"] and len(cands) > 1:
                cands = [b for b in cands if norm_title(b["author"]) == norm_title(v["author"])] or cands
            if len(cands) == 1:
                match, how = cands[0], "الاسم"
        if match is None:
            new.append({"row": n, **{k: v[k] for k in ("title", "author", "publisher", "barcode", "sku", "price", "quantity", "shelf")},
                        "isbn": v["barcode"] if is_isbn(v["barcode"]) else "", "language": guess_language(v["title"], v["barcode"] if is_isbn(v["barcode"]) else "")})
            continue
        if match["book_id"] in seen_ids:
            invalid.append({"row": n, "title": v["title"], "problems": [f"يطابق نفس كتاب سطر سابق ({match['title']})"]})
            continue
        seen_ids.add(match["book_id"])
        changes = {}
        if v["price"] is not None and float(match["price"] or 0) != v["price"]:
            changes["price"] = [match["price"], v["price"]]
        if v["quantity"] is not None and int(match["quantity"] or 0) != v["quantity"]:
            changes["quantity"] = [match["quantity"], v["quantity"]]
        if "shelf" in mapping and v["shelf"] and v["shelf"] != (match["shelf_location"] or ""):
            changes["shelf_location"] = [match["shelf_location"], v["shelf"]]
        if v["barcode"] and not match.get("barcode"):
            changes["barcode"] = [None, v["barcode"]]
        if v["sku"] and not match.get("rewaa_sku"):
            changes["rewaa_sku"] = [None, v["sku"]]
        if v["publisher"] and not match.get("publisher"):
            changes["publisher"] = [None, v["publisher"]]
        note = None
        if norm_title(v["title"]) != norm_title(match["title"]):
            note = f"الاسم في رواء «{v['title']}» يختلف عن الكتالوج (ما يتغير تلقائيًا)"
        item = {"row": n, "book_id": match["book_id"], "title": match["title"], "matched_by": how,
                "changes": changes, "note": note}
        (updated if changes else unchanged).append(item)

    missing = [{"book_id": b["book_id"], "title": b["title"], "quantity": b["quantity"],
                "source": b.get("source") or "seed"}
               for b in books if b["book_id"] not in seen_ids]
    return {
        "file_name": up["file_name"], "rows_read": len(up["rows"]), "mapping": mapping,
        "match_warning": None if {"barcode", "sku"} & set(mapping) else
        "ما حددت عمود باركود أو رمز منتج؛ المطابقة بتكون بالاسم فقط وقد تكون أقل دقة.",
        "new": new, "updated": updated, "unchanged": unchanged, "invalid": invalid, "excluded": excluded,
        "missing": missing, "missing_mode": missing_mode,
    }


def apply_plan(upload_id, mapping, missing_mode="zero", applied_by=None):
    """missing_mode: 'zero' (default since 2026-09-27: set quantity 0, hidden from visitors) or 'report' (change nothing)."""
    if missing_mode not in ("report", "zero"):
        raise ValueError("invalid missing_mode")
    plan = build_plan(upload_id, mapping, missing_mode)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = get_connection()
    zeroed = 0
    added_rows, zeroed_rows = [], []
    try:
        conn.execute("BEGIN")
        for item in plan["new"]:
            cur = conn.execute(
                """INSERT INTO books (title, author, publisher, isbn, language, price, quantity, shelf_location,
                                      active, barcode, rewaa_sku, source, last_synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'rewaa', ?)""",
                (item["title"], item["author"] or "غير معروف", item.get("publisher") or None,
                 item.get("isbn") or None, item.get("language") or "ar",
                 item["price"] if item["price"] is not None else 0.0,
                 item["quantity"] if item["quantity"] is not None else 0,
                 item["shelf"] or None, item["barcode"] or None, item["sku"] or None, now))
            conn.execute("INSERT OR IGNORE INTO book_profiles (book_id, overall_status) VALUES (?, 'pending')",
                         (cur.lastrowid,))
            added_rows.append({"book_id": cur.lastrowid, "title": item["title"], "author": item["author"] or "",
                               "publisher": item.get("publisher") or "", "quantity": item["quantity"], "price": item["price"]})
        for item in plan["updated"] + plan["unchanged"]:
            sets = {k: v[1] for k, v in item["changes"].items()}
            sets["last_synced_at"] = now
            sets["source"] = "rewaa"
            cols = ", ".join(f"{k} = ?" for k in sets)
            conn.execute(f"UPDATE books SET {cols} WHERE book_id = ?", (*sets.values(), item["book_id"]))
        if missing_mode == "zero":
            for m in plan["missing"]:
                if (m["quantity"] or 0) > 0:
                    conn.execute("UPDATE books SET quantity = 0, last_synced_at = ? WHERE book_id = ?",
                                 (now, m["book_id"]))
                    zeroed += 1
                    zeroed_rows.append({"book_id": m["book_id"], "title": m["title"], "quantity": m["quantity"]})
        details = {"new": added_rows, "zeroed": zeroed_rows,
                   "updated": [{"book_id": u["book_id"], "title": u["title"], "changes": u["changes"]} for u in plan["updated"]],
                   "invalid": [{"row": i["row"], "title": i["title"], "problems": i["problems"]} for i in plan["invalid"]],
                   "excluded": len(plan["excluded"]), "missing_mode": missing_mode}
        conn.execute(
            """INSERT INTO import_runs (source, file_name, applied_at, rows_read, added, updated, unchanged,
                                        missing, marked_out_of_stock, invalid, mapping, applied_by, details)
               VALUES ('rewaa', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (plan["file_name"], now, plan["rows_read"], len(plan["new"]), len(plan["updated"]),
             len(plan["unchanged"]), len(plan["missing"]), zeroed, len(plan["invalid"]),
             ",".join(f"{k}:{v}" for k, v in plan["mapping"].items()), applied_by,
             json.dumps(details, ensure_ascii=False)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _uploads.pop(upload_id, None)
    return {"applied_at": now, "added": len(plan["new"]), "updated": len(plan["updated"]),
            "unchanged": len(plan["unchanged"]), "missing": len(plan["missing"]),
            "marked_out_of_stock": zeroed, "invalid": len(plan["invalid"]), "excluded": len(plan["excluded"])}


def history(limit=10):
    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM import_runs ORDER BY run_id DESC LIMIT ?", (limit,))]
        for r in rows:
            r.pop("details", None)  # the per-book lists go to the inbox export, not to this short history
        return rows
    finally:
        conn.close()
