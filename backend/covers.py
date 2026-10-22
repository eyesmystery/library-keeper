"""Book cover images (approved by Ibrahim 2026-09-27).

Where a cover comes from, in order of preference:
  1. Open Library, by ISBN / barcode        (automatic, scripts/fetch_covers.py)
  2. the review sites: Goodreads, then Abjjad (automatic, same script; og:image of the book page)
  3. a general web search                    (done during book research; the found image URL is
                                              stored through scripts/import_enrichment.py)
  4. staff upload in the admin page          (when nothing else found one)

Every cover is stored as a local copy in data/covers/<book_id>.<ext> and served from
/covers/..., so visitors never load images from third-party sites. The database keeps
cover_url (local path), cover_source and cover_updated_at.
"""
import html
import os
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

import net
from datetime import datetime, timezone

from models import get_connection

COVERS_DIR = os.environ.get("LIBRARY_KEEPER_COVERS_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "covers")
MAX_BYTES = 5 * 1024 * 1024
MIN_BYTES = 1500          # smaller files are placeholders / broken images
MIN_WIDTH = 80
SOURCES = ("openlibrary", "goodreads", "abjjad", "web", "staff")
AUTO_SOURCES = ("openlibrary", "goodreads", "abjjad")
USER_AGENT = "LibraryKeeper/1.0 (bookstore catalog covers)"
_FILE_RE = re.compile(r"^\d+\.(jpg|png|webp)$")


class CoverError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- image checks
def image_type(data):
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def image_size(data, kind):
    """(width, height) or None when the header cannot be read."""
    try:
        if kind == "png":
            return struct.unpack(">II", data[16:24])
        if kind == "webp":
            chunk = data[12:16]
            if chunk == b"VP8X":
                w = int.from_bytes(data[24:27], "little") + 1
                h = int.from_bytes(data[27:30], "little") + 1
                return w, h
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b"VP8L":
                b = data[21:25]
                w = 1 + (((b[1] & 0x3F) << 8) | b[0])
                h = 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
                return w, h
            return None
        if kind == "jpg":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + length
    except (struct.error, IndexError):
        return None
    return None


def check_image(data):
    """Return the file extension, or raise CoverError with a short reason."""
    if not data or len(data) < MIN_BYTES:
        raise CoverError("image too small or empty")
    if len(data) > MAX_BYTES:
        raise CoverError("image larger than 5 MB")
    kind = image_type(data)
    if not kind:
        raise CoverError("not a JPEG, PNG or WebP image")
    size = image_size(data, kind)
    if size and (size[0] < MIN_WIDTH or size[1] < MIN_WIDTH):
        raise CoverError("image dimensions too small")
    if size and size[0] > size[1] * 1.6:
        # e.g. Abjjad's 1200x630 share banner (cover small in the middle); even landscape children's books stay below 1.6
        raise CoverError("wide banner image, not a book cover")
    return kind


# ---------------------------------------------------------------- storage
def cover_path(filename):
    """Absolute path of a stored cover file, or None for anything that is not one."""
    if not _FILE_RE.match(filename or ""):
        return None
    path = os.path.join(COVERS_DIR, filename)
    return path if os.path.isfile(path) else None


def _remove_files(book_id):
    for ext in ("jpg", "png", "webp"):
        path = os.path.join(COVERS_DIR, f"{int(book_id)}.{ext}")
        if os.path.exists(path):
            os.remove(path)


def save_cover(book_id, data, source):
    if source not in SOURCES:
        raise CoverError("unknown source")
    ext = check_image(data)
    os.makedirs(COVERS_DIR, exist_ok=True)
    _remove_files(book_id)
    name = f"{int(book_id)}.{ext}"
    tmp = os.path.join(COVERS_DIR, name + ".part")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, os.path.join(COVERS_DIR, name))
    url = f"/covers/{name}?v={int(time.time())}"
    conn = get_connection()
    conn.execute("UPDATE books SET cover_url = ?, cover_source = ?, cover_updated_at = ? WHERE book_id = ?",
                 (url, source, _now(), int(book_id)))
    conn.commit()
    conn.close()
    return {"cover_url": url, "cover_source": source}


def remove_cover(book_id):
    _remove_files(book_id)
    conn = get_connection()
    conn.execute("UPDATE books SET cover_url = NULL, cover_source = NULL, cover_updated_at = ? WHERE book_id = ?",
                 (_now(), int(book_id)))
    conn.commit()
    conn.close()


def has_local_cover(book):
    url = book.get("cover_url") or ""
    return url.startswith("/covers/") and cover_path(url.split("/covers/", 1)[1].split("?", 1)[0]) is not None


# ---------------------------------------------------------------- fetching
def http_get(url, timeout=15):
    """-> (final_url, bytes). Raises urllib errors. Replaced in tests."""
    url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")  # non-ASCII paths (Arabic slugs) -> %-encoded
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ar,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout, context=net.ssl_context()) as resp:
        return resp.geturl(), resp.read(MAX_BYTES + 1)


def isbn_candidates(*values):
    """Clean ISBN-13 / ISBN-10 strings from the isbn and barcode fields (13 -> 10 for 978)."""
    out = []
    for value in values:
        raw = re.sub(r"[^0-9Xx]", "", str(value or "")).upper()
        if len(raw) in (10, 13) and raw not in out:
            out.append(raw)
            if len(raw) == 13 and raw.startswith("978"):
                core = raw[3:12]
                total = sum((10 - i) * int(d) for i, d in enumerate(core))
                check = (11 - total % 11) % 11
                isbn10 = core + ("X" if check == 10 else str(check))
                if isbn10 not in out:
                    out.append(isbn10)
    return out


_DIACRITICS = re.compile("[ً-ٰٟـ]")


def norm_title(text):
    t = _DIACRITICS.sub("", str(text or "").lower())
    t = re.sub("[إأآٱ]", "ا", t).replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"[^\w]+", " ", t).strip()


def title_matches(book_title, page_title):
    """The page must carry the book's title (first four words, normalized) — avoids wrong covers."""
    want = norm_title(book_title).split()[:4]
    have = norm_title(page_title)
    return bool(want) and " ".join(want) in have


def _meta(page, prop):
    for pattern in (rf'<meta[^>]+property=["\']{prop}["\'][^>]+content=["\']([^"\']+)["\']',
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{prop}["\']'):
        m = re.search(pattern, page, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return None


def _page_title(page):
    t = _meta(page, "og:title")
    if t:
        return t
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    return html.unescape(m.group(1)).strip() if m else ""


def _image_from_page(page_url, page, book, placeholder_hints):
    if not title_matches(book.get("title"), _page_title(page)):
        return None
    img = _meta(page, "og:image")
    if not img or any(h in img.lower() for h in placeholder_hints):
        return None
    img = small_variant(urllib.parse.urljoin(page_url, img))
    _, data = http_get(img)
    check_image(data)
    return data, img


def from_openlibrary(book):
    for isbn in isbn_candidates(book.get("isbn"), book.get("barcode")):
        url = f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
        try:
            _, data = http_get(url)
            check_image(data)
            return data, url
        except (urllib.error.URLError, CoverError, OSError):
            continue
    return None


def from_goodreads(book):
    pages = [f"https://www.goodreads.com/book/isbn/{i}" for i in isbn_candidates(book.get("isbn"), book.get("barcode"))[:1]]
    query = urllib.parse.quote(f'{book.get("title", "")} {book.get("author", "")}'.strip())
    pages.append(f"https://www.goodreads.com/search?q={query}")
    for url in pages:
        try:
            final, raw = http_get(url)
            page = raw.decode("utf-8", "replace")
            if "/search" in final:
                m = re.search(r'href="(/book/show/[^"?#]+)', page)
                if not m:
                    continue
                final = urllib.parse.urljoin("https://www.goodreads.com", m.group(1))
                _, raw = http_get(final)
                page = raw.decode("utf-8", "replace")
            got = _image_from_page(final, page, book, ("nophoto", "no-cover"))
            if got:
                return got
        except (urllib.error.URLError, CoverError, OSError, UnicodeError):
            continue
    return None


def abjjad_pages(book, limit=3):
    """Abjjad book pages for the book: the search («kw»; «q» is ignored) gives loose word matches, so pages
    whose address carries the title come first; the caller still checks the page title."""
    query = urllib.parse.quote(f'{book.get("title", "")} {book.get("author", "")}'.strip())
    _, raw = http_get(f"https://www.abjjad.com/search?kw={query}")
    links = []
    for m in re.finditer(r'href="((?:https://www\.abjjad\.com)?/book/\d+[^"?#]*)', raw.decode("utf-8", "replace")):
        u = urllib.parse.urljoin("https://www.abjjad.com", m.group(1))
        if u not in links:
            links.append(u)
    def slug(u):
        parts = urllib.parse.unquote(urllib.parse.urlparse(u).path).split("/")
        return parts[3].replace("-", " ") if len(parts) > 3 else ""
    named = [u for u in links if title_matches(book.get("title"), slug(u))]
    return (named or links[:2])[:limit]


def from_abjjad(book):
    try:
        for url in abjjad_pages(book, limit=2):
            _, raw = http_get(url)
            page = raw.decode("utf-8", "replace")
            if not title_matches(book.get("title"), _page_title(page)):
                continue
            img = _page_image(page)
            if not img or any(h in img.lower() for h in _PLACEHOLDERS):
                continue
            img = urllib.parse.urljoin(url, img)
            _, data = http_get(img)
            check_image(data)
            return data, img
        return None
    except (urllib.error.URLError, CoverError, OSError, UnicodeError):
        return None


FETCHERS = {"openlibrary": from_openlibrary, "goodreads": from_goodreads, "abjjad": from_abjjad}


def fetch_cover(book, sources=AUTO_SOURCES):
    """Try each automatic source in order; save the first good image. -> {source, from} or None."""
    for source in sources:
        got = FETCHERS[source](book)
        if got:
            data, found_at = got
            save_cover(book["book_id"], data, source)
            return {"source": source, "from": found_at}
    return None


_AMAZON_IMG = re.compile(r"^(https://[^/]*media-amazon\.com/images/.+?/[^/._]+)(\._[^/]*_)?(\.(?:jpe?g|png|webp))$", re.I)


def small_variant(url):
    """Covers only need to be recognisable (approved 2026-09-28): ask Goodreads/Amazon image
    hosts for a ~300 px wide copy instead of the original (some originals exceed 5 MB)."""
    m = _AMAZON_IMG.match(url or "")
    return f"{m.group(1)}._SX300_{m.group(3)}" if m else url


def source_from_url(url):
    host = urllib.parse.urlparse(url or "").netloc.lower()
    if "openlibrary" in host:
        return "openlibrary"
    if "goodreads" in host or "gr-assets" in host:
        return "goodreads"
    if "abjjad" in host:
        return "abjjad"
    return "web"


_BOOK_PAGE = re.compile(r"^https?://(www\.)?(goodreads\.com/(\w+/)?book/show/|abjjad\.com/book/)", re.I)
# Arabic bookshop / publisher product pages seen during research (2026-09-29): their og:image is
# the product (cover) photo. Only product-page paths, never search or listing pages.
# Left out: neelwafurat.com (cover loaded by script, no og:image) and aseeralkotb (script challenge page).
_SHOP_PAGE = re.compile(r"^https?://(www\.)?(thebookhome\.com/product/|"
                        r"ceresbookshop\.com/|antoineonline\.com/|kotobon\.com/|"
                        r"mahrousaeg\.com/|takweenkw\.com/|diwanegypt\.com/product/|jamalon\.com/|"
                        r"elainpublishinghouse\.com/books/)", re.I)


def is_book_page(url):
    """A researched book page whose image can be read (Goodreads/Abjjad or a known bookshop product page)."""
    return bool(_BOOK_PAGE.match(url or "") or _SHOP_PAGE.match(url or ""))


def image_from_book_page(url):
    """A researched Goodreads/Abjjad book page -> its og:image URL (the page was matched during
    research, so no title check here). Raises CoverError when the page has no usable image."""
    last = ""
    for attempt in range(2):  # Goodreads sometimes serves a stripped page; one polite retry
        if attempt:
            time.sleep(PAGE_RETRY_DELAY)
        final, raw = http_get(url)
        page = raw.decode("utf-8", "replace")
        img = _page_image(page)
        if img:
            return urllib.parse.urljoin(final, img)
        last = f"{len(raw) // 1024} KB, title «{_page_title(page)[:60]}»"
    raise CoverError(f"no cover image on the book page ({last})")


PAGE_RETRY_DELAY = 4.0
_PLACEHOLDERS = ("nophoto", "no-cover", "no-image", "placeholder", "/default", "logo", "sitedefult")
_BOOK_IMG = re.compile(r'https://[^"\'\s<>]*(?:compressed\.photo\.goodreads\.com/books|i\.gr-assets\.com/images/S/compressed\.photo\.goodreads\.com/books|images-na\.ssl-images-amazon\.com/images/S/compressed\.photo\.goodreads\.com/books)/[^"\'\s<>]+\.(?:jpe?g|png|webp)', re.I)


_ABJJAD_COVER = re.compile(r'data-book-cover-url=["\'](https://cdn\.abjjad\.com/[^"\']+)["\']', re.I)


def _abjjad_full(url):
    """Abjjad lists a 192x290 copy; the same name without the size is the full cover."""
    return re.sub(r"-\d{2,4}X\d{2,4}(\.\w+)$", r"\1", url or "")


def _page_image(page):
    """Cover URL of a book page: Abjjad's book-cover marker, a JSON-LD ImageObject, og:image, then JSON-LD
    "image", then the first Goodreads book-cover URL. (On Abjjad og:image is a 1200x630 share banner.)"""
    m = _ABJJAD_COVER.search(page)
    candidates = [m and _abjjad_full(m.group(1))]
    m = re.search(r'"image"\s*:\s*\{[^{}]*?"url"\s*:\s*"(https?:[^"]+)"', page)
    candidates.append(m and m.group(1).replace("\\/", "/"))
    candidates.append(_meta(page, "og:image"))
    m = re.search(r'"image"\s*:\s*"(https?:[^"]+)"', page)
    candidates.append(m and m.group(1).replace("\\/", "/"))
    m = _BOOK_IMG.search(page)
    candidates.append(m and m.group(0))
    for img in candidates:
        if img and not any(h in img.lower() for h in _PLACEHOLDERS):
            return html.unescape(img)
    return None


def save_from_url(book_id, url):
    """Download a cover found during research (an image URL, or the researched Goodreads/Abjjad
    book page, whose og:image is used) and store it locally."""
    if not re.match(r"^https?://", url or ""):
        raise CoverError("not an http(s) URL")
    if is_book_page(url):
        url = image_from_book_page(url)
    small = small_variant(url)
    try:
        _, data = http_get(small)
        check_image(data)
    except (CoverError, urllib.error.URLError, OSError):
        if small == url:
            raise
        _, data = http_get(url)  # the small variant is not offered: fall back to the original
    return save_cover(book_id, data, source_from_url(url))


# ---------------------------------------------------------------- choices for the staff (2026-10-13)
# Ibrahim: «ابحث عن غلاف تلقائيا في بطاقة الكتاب … ودي ياخذ مجال اكبر ويصير يدور عالنت ويعط اختيارات اذا حصل الغلاف
# والموظف يختار». The automatic button used to save the first image it found; now several sources are searched at
# once, every image is downloaded and checked, and the staff member picks one (or none).
import base64
import hashlib
import json as _json
from concurrent.futures import ThreadPoolExecutor, as_completed

SEARCH_SOURCES = ("openlibrary", "web_images", "abjjad", "goodreads", "openlibrary_search", "google_books", "wikipedia")
SOURCE_LABELS = {"openlibrary": "Open Library (بالـISBN)", "openlibrary_search": "Open Library (بالعنوان)",
                 "google_books": "Google Books", "goodreads": "Goodreads", "abjjad": "أبجد", "wikipedia": "ويكيبيديا",
                 "web_images": "بحث الصور"}
MAX_CHOICES = 12
SEARCH_SECONDS = 35


def _q(book, with_author=True):
    return f'{book.get("title", "")} {book.get("author", "") if with_author else ""}'.strip()


def _cand_openlibrary(book):
    return [(f"https://covers.openlibrary.org/b/isbn/{i}-L.jpg?default=false", None)
            for i in isbn_candidates(book.get("isbn"), book.get("barcode"))]


def _cand_openlibrary_search(book):
    params = {"title": book.get("title") or "", "limit": "6", "fields": "title,cover_i,key"}
    if book.get("author"):
        params["author"] = book["author"]
    _, raw = http_get("https://openlibrary.org/search.json?" + urllib.parse.urlencode(params))
    out = []
    for d in (_json.loads(raw.decode("utf-8", "replace")).get("docs") or []):
        if d.get("cover_i") and title_matches(book.get("title"), d.get("title") or ""):
            out.append((f"https://covers.openlibrary.org/b/id/{d['cover_i']}-L.jpg",
                        "https://openlibrary.org" + d["key"] if d.get("key") else None))
    return out[:4]


def _cand_google_books(book):
    queries = [f"isbn:{i}" for i in isbn_candidates(book.get("isbn"), book.get("barcode"))[:1]]
    queries.append(f'intitle:{book.get("title", "")}' + (f' inauthor:{book["author"]}' if book.get("author") else ""))
    out = []
    for q in queries:
        _, raw = http_get("https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode({"q": q, "maxResults": "5"}))
        for it in (_json.loads(raw.decode("utf-8", "replace")).get("items") or []):
            info = it.get("volumeInfo") or {}
            img = (info.get("imageLinks") or {}).get("thumbnail") or (info.get("imageLinks") or {}).get("smallThumbnail")
            if img and (q.startswith("isbn:") or title_matches(book.get("title"), info.get("title") or "")):
                img = img.replace("http://", "https://").replace("&edge=curl", "")
                out.append((img, info.get("infoLink")))
    return out[:4]


def _book_page_images(pages, book, isbn_pages=()):
    """Cover of each book page whose title is the book's (a page reached by the ISBN is trusted)."""
    out = []
    for url in pages:
        try:
            final, raw = http_get(url)
        except (urllib.error.URLError, OSError, ValueError):
            continue
        page = raw.decode("utf-8", "replace")
        if url not in isbn_pages and not title_matches(book.get("title"), _page_title(page)):
            continue
        img = _page_image(page)
        if img:
            out.append((small_variant(urllib.parse.urljoin(final or url, img)), final or url))
    return out


def _cand_goodreads(book):
    by_isbn = [f"https://www.goodreads.com/book/isbn/{i}" for i in isbn_candidates(book.get("isbn"), book.get("barcode"))[:1]]
    pages = list(by_isbn)
    try:
        _, raw = http_get("https://www.goodreads.com/search?q=" + urllib.parse.quote(_q(book)))
        for m in re.finditer(r'href="(/book/show/[^"?#]+)', raw.decode("utf-8", "replace")):
            u = urllib.parse.urljoin("https://www.goodreads.com", m.group(1))
            if u not in pages:
                pages.append(u)
            if len(pages) >= 4:
                break
    except (urllib.error.URLError, OSError, ValueError):
        pass   # Goodreads often refuses automated search; the ISBN page may still work
    return _book_page_images(pages, book, isbn_pages=by_isbn)


def _cand_abjjad(book):
    return _book_page_images(abjjad_pages(book, limit=3), book)


def _cand_wikipedia(book):
    """The Arabic Wikipedia article named for the book (title checked) and its picture. Not the English
    article: its picture is the original edition's cover, not ours (Ibrahim 2026-10-13)."""
    api = "https://ar.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "generator": "search", "gsrsearch": book.get("title") or "", "gsrlimit": "4",
        "prop": "pageimages", "piprop": "thumbnail", "pithumbsize": "500", "format": "json"})
    _, raw = http_get(api)
    out = []
    for p in (_json.loads(raw.decode("utf-8", "replace")).get("query") or {}).get("pages", {}).values():
        title = re.sub(r"\s*\([^)]*\)\s*$", "", p.get("title") or "")
        if norm_title(title) == norm_title(book.get("title")) and (p.get("thumbnail") or {}).get("source"):
            out.append((p["thumbnail"]["source"], "https://ar.wikipedia.org/wiki/" + urllib.parse.quote(p["title"].replace(" ", "_"))))
    return out[:2]


WEB_RESULTS = 8


def _public_host(url):
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    return bool(host) and "." in host and host != "localhost" and not re.match(r"^[\d.]+$|^\[|.*\.local$", host)


def _cand_web_images(book):
    """Image search on the web, as Ibrahim asked (2026-10-13): «غلاف كتاب "<العنوان>"» (then with the author if
    that gives few). Results come from any site (bookshops, publishers, blogs); only those whose page title or
    address carries the book's title are kept — «غلاف كتاب» alone brings design-portfolio pictures."""
    title = (book.get("title") or "").strip()
    out = []
    for q in (f'غلاف كتاب "{title}"', f'غلاف كتاب "{title}" {book.get("author") or ""}'.strip()):
        _, raw = http_get("https://www.bing.com/images/search?" + urllib.parse.urlencode({"q": q, "form": "HDRSC2", "first": "1"}))
        for m in re.finditer(r'class="iusc"[^>]*?\bm="([^"]+)"', raw.decode("utf-8", "replace")):
            try:
                d = _json.loads(html.unescape(m.group(1)))
            except ValueError:
                continue
            img, page = d.get("murl") or "", d.get("purl") or ""
            if not img.startswith(("http://", "https://")) or not _public_host(img):
                continue
            where = f'{d.get("t") or ""} {urllib.parse.unquote(page).replace("-", " ").replace("_", " ")}'
            if title_matches(title, where) and all(img != o[0] for o in out):
                out.append((img, page or None))
        if len(out) >= 4 or not book.get("author"):
            break
    return out[:WEB_RESULTS]


CANDIDATE_FINDERS = {"openlibrary": _cand_openlibrary, "openlibrary_search": _cand_openlibrary_search,
                     "google_books": _cand_google_books, "goodreads": _cand_goodreads, "abjjad": _cand_abjjad,
                     "wikipedia": _cand_wikipedia, "web_images": _cand_web_images}
# the source stored with a chosen cover (covers.SOURCES)
STORE_SOURCE = {"openlibrary": "openlibrary", "openlibrary_search": "openlibrary", "google_books": "web",
                "goodreads": "goodreads", "abjjad": "abjjad", "wikipedia": "web", "web_images": "web"}


def _checked(url):
    """-> (bytes, kind, (w, h)) of a usable cover image at url, or None."""
    try:
        _, data = http_get(url)
        kind = check_image(data)
        return data, kind, image_size(data, kind)
    except (urllib.error.URLError, CoverError, OSError, ValueError):
        return None


def search_candidates(book, sources=SEARCH_SOURCES):
    """Cover images for the book from several sources, each downloaded and checked, duplicates removed.
    -> [{url, source, label, page, width, height, preview (data: URI)}], at most MAX_CHOICES, ISBN matches first."""
    found = []   # (order, source, img_url, page)
    pool = ThreadPoolExecutor(max_workers=6)   # not a «with»: a slow site must not hold the answer past the time limit
    try:
        futs = {pool.submit(CANDIDATE_FINDERS[s], book): (n, s) for n, s in enumerate(sources)}
        try:
            for f in as_completed(futs, timeout=SEARCH_SECONDS):
                n, s = futs[f]
                try:
                    for k, (img, page) in enumerate(f.result() or []):
                        found.append((n * 10 + k, s, img, page))
                except (urllib.error.URLError, CoverError, OSError, ValueError, UnicodeError):
                    continue
        except TimeoutError:
            pass
        found.sort()
        seen_url, todo = set(), []
        for o, s, img, page in found:
            if img not in seen_url:
                seen_url.add(img)
                todo.append((o, s, img, page))
        checks = {pool.submit(_checked, img): (o, s, img, page) for o, s, img, page in todo[:MAX_CHOICES * 2]}
        got = []
        try:
            for f in as_completed(checks, timeout=SEARCH_SECONDS):
                r = f.result()
                if r:
                    got.append((checks[f], r))
        except TimeoutError:
            pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    got.sort(key=lambda x: x[0][0])
    out, seen_hash = [], set()
    for (o, s, img, page), (data, kind, size) in got:
        if size and size[0] > size[1] * 1.05:
            continue   # wider than tall: an article picture or a banner, never a book cover
        h = hashlib.sha1(data).hexdigest()
        if h in seen_hash:
            continue
        seen_hash.add(h)
        mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind]
        label = SOURCE_LABELS.get(s, s)
        if s == "web_images" and page:
            label = (urllib.parse.urlparse(page).hostname or label).removeprefix("www.")
        out.append({"url": img, "source": s, "label": label, "page": page,
                    "width": size[0] if size else None, "height": size[1] if size else None,
                    "preview": f"data:{mime};base64," + base64.b64encode(data).decode("ascii")})
        if len(out) >= MAX_CHOICES:
            break
    return out


def save_chosen(book_id, url, source):
    """Store the image the staff member picked from search_candidates (downloaded and checked again)."""
    got = _checked(url)
    if not got:
        raise CoverError("the chosen image could not be downloaded")
    return save_cover(book_id, got[0], STORE_SOURCE.get(source, "web"))
