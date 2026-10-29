"""«اجلب من الرابط» (DEVFETCH, Ibrahim 2026-10-29: «انا ابغى ابحث يدويا يعني يشوف المصدر وياخذ البيانات»).

The developer searches by hand (the «بحث يدوي عن بيانات الكتاب» links), pastes the page of the right edition, and this
module reads that page and returns what it says about the book — title, author, publisher, translator, ISBN, pages,
year, cover image — for the developer to tick and save (nothing is written here). Read in this order:
  1. schema.org JSON-LD (Book / Product) — Goodreads, many bookshops;
  2. «label: value» rows of the page text (الناشر، المؤلف، المترجم، ردمك/ISBN، عدد الصفحات، سنة النشر …) — Arabic shops;
  3. meta tags (og:title, book:isbn, og:image).
Only http(s) pages on public addresses are read (no local or private network addresses).
"""
import html
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse

import covers


class PageError(Exception):
    pass


RETRY_DELAY = 3.0
FIELDS = ("title", "author", "publisher", "translator", "isbn", "page_count", "publication_year")
LABELS = {
    "publisher": r"الناشر|دار النشر|الناشر\s*/\s*الدار|دار|publisher|published by",
    "author": r"المؤلف|المؤلفون|تأليف|الكاتب|author|authors|by",
    "translator": r"المترجم|ترجمة|تعريب|translator|translated by",
    "isbn": r"isbn(?:\s*-?1[03])?|ردمك|الرقم الدولي(?: الموحد)?|رقم isbn|ر\.د\.م\.ك",
    "page_count": r"عدد الصفحات|الصفحات|عدد صفحات الكتاب|pages|number of pages|print length|page count",
    "publication_year": r"سنة النشر|تاريخ النشر|سنة الإصدار|تاريخ الإصدار|سنة الطبع|publication date|published|year",
}
_LABEL_RE = {k: re.compile(rf"^\s*(?:{v})(?!\w)\s*[:：\-–]?\s*(.*)$", re.I) for k, v in LABELS.items()}
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _check_public(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise PageError("الرابط لازم يبدأ بـ http أو https")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror:
        raise PageError("ما قدرت أوصل لهذا الموقع (العنوان غير معروف)")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise PageError("هذا عنوان داخلي، ما يُقرأ")


def _clean(text):
    return re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip(" \t\r\n:،,-–|")


def isbn_of(value):
    raw = re.sub(r"[^0-9Xx]", "", str(value or "").translate(_DIGITS)).upper()
    return raw if len(raw) in (10, 13) else None


def pages_of(value):
    m = re.search(r"\d{1,5}", str(value or "").translate(_DIGITS))
    n = int(m.group(0)) if m else 0
    return n if 1 <= n <= 20000 else None


def year_of(value):
    m = re.search(r"(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)", str(value or "").translate(_DIGITS))
    return int(m.group(1)) if m else None


def _desc(text):
    t = re.sub(r"<[^>]+>", " ", html.unescape(str(text or "")))
    return re.sub(r"\s+", " ", t).strip()[:3000]


def is_arabic(text):
    t = re.sub(r"[^\w]", "", str(text or ""))
    return bool(t) and sum("\u0600" <= c <= "\u06ff" for c in t) / len(t) > 0.4


def _names(v):
    if isinstance(v, list):
        return "، ".join(n for n in (_names(x) for x in v) if n)
    if isinstance(v, dict):
        return _clean(v.get("name"))
    return _clean(v)


def _jsonld_items(page):
    out = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack += x
            elif isinstance(x, dict):
                if "@graph" in x:
                    stack.append(x["@graph"])
                if "workExample" in x:
                    stack.append(x["workExample"])
                t = x.get("@type")
                types = t if isinstance(t, list) else [t]
                if any(str(tt).lower() in ("book", "product") for tt in types):
                    out.append(x)
    out.sort(key=lambda x: "book" not in str(x.get("@type")).lower())   # a Book before a Product
    return out


def _from_jsonld(page):
    got = {}
    for x in _jsonld_items(page):
        cand = {
            "title": _clean(x.get("name")),
            "author": _names(x.get("author")),
            "publisher": _names(x.get("publisher")),   # not «brand»: shops put the author or the shop there
            "translator": _names(x.get("translator")),
            "isbn": isbn_of(x.get("isbn") or x.get("gtin13") or x.get("gtin")),
            "page_count": pages_of(x.get("numberOfPages")),
            "publication_year": year_of(x.get("datePublished")),
        }
        for k, v in cand.items():
            if v and k not in got:
                got[k] = v
        if x.get("description") and "description" not in got:
            got["description"] = _desc(x.get("description"))
        img = x.get("image")
        if isinstance(img, list):
            img = img[0] if img else None
        if isinstance(img, dict):
            img = img.get("url")
        if img and "cover" not in got:
            got["cover"] = str(img)
    return got


def _from_next_data(page):
    """Goodreads (and other Next.js pages): the Book object of __NEXT_DATA__ — its description and details are not in the
    JSON-LD there."""
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return {}
    stack, got = [data], {}
    while stack and not got:
        x = stack.pop()
        if isinstance(x, list):
            stack += x
        elif isinstance(x, dict):
            if x.get("__typename") == "Book" and x.get("description"):
                det = x.get("details") if isinstance(x.get("details"), dict) else {}
                got = {"title": _clean(x.get("title")), "description": _desc(x.get("description")),
                       "isbn": isbn_of(det.get("isbn13") or det.get("isbn")), "page_count": pages_of(det.get("numPages")),
                       "publisher": _clean(det.get("publisher"))}
                got = {k: v for k, v in got.items() if v}
            else:
                stack += list(x.values())
    return got


def _from_openlibrary(url, fetch):
    """openlibrary.org/works/… or /books/…: the record's JSON has the description (the page's meta text is the site's)."""
    m = re.match(r"^https?://(www\.)?openlibrary\.org/(works|books)/(OL\w+)", url)
    if not m:
        return {}
    try:
        _, raw = fetch(f"https://openlibrary.org/{m.group(2)}/{m.group(3)}.json")
        d = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, OSError, urllib.error.URLError):
        return {}
    desc = d.get("description")
    desc = desc.get("value") if isinstance(desc, dict) else desc
    got = {"title": _clean(d.get("title")), "description": _desc(desc) if desc else None,
           "isbn": isbn_of((d.get("isbn_13") or d.get("isbn_10") or [None])[0]), "page_count": pages_of(d.get("number_of_pages")),
           "publisher": _clean((d.get("publishers") or [None])[0]), "publication_year": year_of(d.get("publish_date"))}
    return {k: v for k, v in got.items() if v}


def _text_lines(page):
    page = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    page = re.sub(r"<br\s*/?>|</(p|div|li|tr|td|th|dt|dd|h\d|span|strong|b|label)>", "\n", page, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return [ln for ln in (re.sub(r"[ \t ]+", " ", l).strip() for l in text.split("\n")) if ln]


def _from_labels(page):
    lines, got = _text_lines(page), {}
    for i, line in enumerate(lines):
        if len(line) > 160:
            continue
        for k, rx in _LABEL_RE.items():
            if k in got:
                continue
            m = rx.match(line)
            if not m:
                continue
            value = m.group(1).strip()
            if not value and i + 1 < len(lines) and len(lines[i + 1]) <= 120:
                value = lines[i + 1]
            if k == "isbn":
                value = isbn_of(value)
            elif k == "page_count":
                value = pages_of(value)
            elif k == "publication_year":
                value = year_of(value)
            else:
                value = _clean(value)
                if not value or len(value) > 100 or any(r.match(value) for r in _LABEL_RE.values()):
                    value = None
            if value:
                got[k] = value
    return got


def _from_meta(page):
    got = {}
    t = covers._meta(page, "og:title")
    if not t:
        m = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
        t = html.unescape(m.group(1)).strip() if m else ""
    t = re.split(r"\s+[|–\-]\s+", _clean(t))[0] if t else ""
    if t:
        got["title"] = t
    d = covers._meta(page, "og:description")
    if not d:
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', page, re.I)
        d = m and m.group(1)
    og_type = (covers._meta(page, "og:type") or "").lower()
    # a site's own blurb («Open Library هو كتالوج…») is not the book's: kept only on a book/product page or when it names the title
    if d and (og_type in ("book", "books.book", "product", "og:product") or (t and covers.norm_title(t) in covers.norm_title(d))):
        got["description"] = _desc(d)
    for prop in ("book:isbn", "books:isbn"):
        v = isbn_of(covers._meta(page, prop))
        if v:
            got["isbn"] = v
            break
    return got


def extract(url, fetch=None):
    """-> {"url", "final_url", "fields": {name: value}, "via": {name: "jsonld"|"labels"|"meta"}, "cover": url|None}.
    Raises PageError with an Arabic message."""
    url = str(url or "").strip()
    _check_public(url)
    fetch = fetch or covers.http_get
    try:
        final, raw = fetch(url)
        if len(raw) < 1000:          # Goodreads sometimes answers with an empty page: one polite retry
            time.sleep(RETRY_DELAY)
            final, raw = fetch(url)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise PageError(f"ما قدرت أقرأ الصفحة ({getattr(e, 'code', '') or type(e).__name__})")
    _check_public(final)
    page = raw[:covers.MAX_BYTES].decode("utf-8", "replace")
    if len(page.strip()) < 200:
        raise PageError("الموقع رجّع صفحة فاضية (يمنع القراءة الآلية أحيانًا) — جرّب بعد شوي أو انقل البيانات بيدك")
    if re.search(r"<title[^>]*>\s*(human verification|just a moment|attention required|access denied|are you a robot)", page, re.I):
        raise PageError("الموقع طلب تحقّق «أنك إنسان» وما سمح بالقراءة الآلية — افتح الصفحة وانقل البيانات بيدك، أو جرّب بعد شوي")
    fields, via = {}, {}
    for name, part in (("jsonld", _from_jsonld(page)), ("openlibrary", _from_openlibrary(final, fetch)),
                       ("next_data", _from_next_data(page)), ("labels", _from_labels(page)), ("meta", _from_meta(page))):
        for k, v in part.items():
            if k not in fields and v:
                fields[k], via[k] = v, name
    cover = fields.pop("cover", None) or covers._page_image(page)
    via.pop("cover", None)
    description = fields.pop("description", None)   # the publisher's / shop's text: shown, never saved as it is
    via.pop("description", None)
    if cover:
        cover = urllib.parse.urljoin(final, html.unescape(cover))
    return {"url": url, "final_url": final, "fields": {k: fields[k] for k in FIELDS if k in fields}, "via": via,
            "cover": cover, "description": description, "description_arabic": is_arabic(description) if description else None}
