"""Covers, summaries, stock visibility and the weekly-sync default (2026-09-27)."""
import os, sys, io, json, random, sqlite3, struct, zlib, shutil, subprocess, tempfile, importlib.util, urllib.error
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models, covers, rewaa_import, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

random.seed(7)
def png(w, h):
    raw = b"".join(b"\x00" + bytes(random.getrandbits(8) for _ in range(w * 3)) for _ in range(h))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")
def jpg(w, h):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof + os.urandom(3000) + b"\xff\xd9"
COVER = png(120, 180)

# ---- image checks and storage
ok(covers.check_image(COVER) == "png" and covers.image_size(COVER, "png") == (120, 180), "PNG accepted, size read")
ok(covers.check_image(jpg(420, 300)) == "jpg", "landscape children's-book cover still accepted")
ok(covers.check_image(jpg(300, 450)) == "jpg" and covers.image_size(jpg(300, 450), "jpg") == (300, 450), "JPEG accepted, size read from SOF")
for bad, why in [(b"GIF89a" + os.urandom(4000), "GIF refused"), (png(40, 60), "tiny image refused"), (b"\xff\xd8\xff" + b"0" * 100, "too small file refused"),
                  (jpg(1200, 630), "wide share banner (1200x630) refused")]:
    try:
        covers.check_image(bad); ok(False, why)
    except covers.CoverError:
        ok(True, why)
ok(covers.cover_path("../app.py") is None and covers.cover_path("1.gif") is None and covers.cover_path("x/1.png") is None, "cover paths cannot escape the folder")
ok(covers.isbn_candidates("978-0-14-032872-1", "9780140328721") == ["9780140328721", "0140328726"], "ISBN-13 plus ISBN-10 derived, no duplicates")
ok(covers.title_matches("الأيام", "الأيام by طه حسين | Goodreads") and not covers.title_matches("عرس الزين", "موسم الهجرة إلى الشمال"), "title check guards against wrong covers")

models.init_db()
c = login(A.app.test_client(), "staff")
con = sqlite3.connect(models.DB_PATH)
bid = con.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 1").fetchone()[0]

# ---- visibility: zero stock / inactive never reach the public list
out_id = con.execute("select book_id from books where book_id != ? order by book_id limit 1", (bid,)).fetchone()[0]
con.execute("update books set quantity=0 where book_id=?", (out_id,)); con.commit()
pub = A.app.test_client().get("/api/books").get_json()
ok(all(b["quantity"] > 0 and b["active"] for b in pub) and out_id not in [b["book_id"] for b in pub], "public /api/books lists only in-stock, active books")
r = A.app.test_client().post("/api/recommend", json={"query": "رواية"}).get_json()
ok(out_id not in [b["book_id"] for b in r["results"]], "zero-stock book never recommended")

# ---- upload (staff), serve, delete
raw = A.app.test_client()
ok(raw.post(f"/api/admin/books/{bid}/cover", data={"file": (io.BytesIO(COVER), "c.png")}, content_type="multipart/form-data").status_code == 401, "upload needs sign-in")
ok(c._c.post(f"/api/admin/books/{bid}/cover", data={"file": (io.BytesIO(COVER), "c.png")}, content_type="multipart/form-data").status_code == 403, "upload needs the CSRF token")
r = c.post(f"/api/admin/books/{bid}/cover", data={"file": (io.BytesIO(COVER), "c.png")}, content_type="multipart/form-data")
ok(r.status_code == 200 and r.get_json()["cover_source"] == "staff", "staff uploads a cover")
url = dict(models.get_book(bid))["cover_url"]
got = raw.get(url.split("?")[0])
ok(got.status_code == 200 and got.data == COVER and "max-age" in got.headers.get("Cache-Control", ""), "cover served from /covers with caching")
ok(raw.get("/covers/..%2Fapp.py").status_code == 404 and raw.get("/covers/999999.png").status_code == 404, "unknown / escaping cover paths 404")
pub = A.app.test_client().get("/api/books").get_json()
ok(next(b for b in pub if b["book_id"] == bid)["cover_url"] == url, "visitor book data carries cover_url")
cat = c.get("/api/admin/catalog").get_json()
ok(next(b for b in cat if b["book_id"] == bid)["cover_source"] == "staff", "admin catalog shows cover and source")
r = c.post(f"/api/admin/books/{bid}/cover", data={"file": (io.BytesIO(b"not an image" * 300), "x.png")}, content_type="multipart/form-data")
ok(r.status_code == 400 and "JPEG" in r.get_json()["error"], "non-image upload refused with a reason")
os.remove(covers.cover_path(url.split("/covers/")[1].split("?")[0]))
ok(A.with_origin(dict(models.get_book(bid)))["cover_url"] is None, "a cover whose file is missing is not sent")
ok(c.delete(f"/api/admin/books/{bid}/cover").status_code == 200 and dict(models.get_book(bid))["cover_url"] is None, "cover removed")
audit = [a["action"] for a in auth_list] if (auth_list := __import__("auth").recent_audit(20)) else []
ok("cover.upload" in audit and "cover.delete" in audit, "cover changes are in the audit log")

# ---- automatic search: Open Library -> Goodreads -> Abjjad (network mocked)
title = dict(models.get_book(bid))["title"]
con.execute("update books set isbn='9780140328721' where book_id=?", (bid,)); con.commit()
calls = []
def fake_get(responses):
    def get(url, timeout=15):
        calls.append(url)
        for prefix, value in responses:
            if url.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                return value
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    return get
covers.http_get = fake_get([("https://covers.openlibrary.org/b/isbn/9780140328721", ("u", COVER))])
r = c.post(f"/api/admin/books/{bid}/cover/fetch").get_json()
ok(r["ok"] and r["cover_source"] == "openlibrary", "Open Library first (by ISBN)")
c.delete(f"/api/admin/books/{bid}/cover")
page = f'<html><head><meta property="og:title" content="{title} by x"><meta property="og:image" content="https://i.gr-assets.com/c.jpg"></head></html>'.encode()
covers.http_get = fake_get([("https://www.goodreads.com/book/isbn/", ("https://www.goodreads.com/book/show/1-x", page)),
                            ("https://i.gr-assets.com/c.jpg", ("u", COVER))])
calls.clear()
r = c.post(f"/api/admin/books/{bid}/cover/fetch").get_json()
ok(r["ok"] and r["cover_source"] == "goodreads" and calls[0].startswith("https://covers.openlibrary.org"), "falls back to Goodreads when Open Library has none")
c.delete(f"/api/admin/books/{bid}/cover")
wrong = b'<meta property="og:title" content="A different book"><meta property="og:image" content="https://i.gr-assets.com/c.jpg">'
abj_search = b'<a href="/book/123/x">x</a>'
abj_page = f'<meta property="og:title" content="{title}"><meta property="og:image" content="https://www.abjjad.com/img/c.png">'.encode()
covers.http_get = fake_get([("https://www.goodreads.com/", ("https://www.goodreads.com/book/show/9", wrong)),
                            ("https://i.gr-assets.com/", ("u", COVER)),
                            ("https://www.abjjad.com/search", ("https://www.abjjad.com/search", abj_search)),
                            ("https://www.abjjad.com/book/123", ("https://www.abjjad.com/book/123/x", abj_page)),
                            ("https://www.abjjad.com/img/c.png", ("u", COVER))])
r = c.post(f"/api/admin/books/{bid}/cover/fetch").get_json()
ok(r["ok"] and r["cover_source"] == "abjjad", "wrong-title Goodreads page skipped; Abjjad used")
c.delete(f"/api/admin/books/{bid}/cover")
covers.http_get = fake_get([])
r = c.post(f"/api/admin/books/{bid}/cover/fetch")
ok(r.status_code == 404 and r.get_json()["error"] == "no cover found", "nothing found -> clear message for manual upload")

# ---- fetch_covers script (in-process, network mocked)
spec = importlib.util.spec_from_file_location("fetch_covers", os.path.join(os.path.dirname(os.getcwd()), "scripts", "fetch_covers.py"))
fc = importlib.util.module_from_spec(spec); spec.loader.exec_module(fc)
covers.http_get = fake_get([("https://covers.openlibrary.org/b/isbn/9780140328721", ("u", COVER))])
report = os.path.join(tempfile.mkdtemp(), "missing.csv")
fc.main(["--limit", "3", "--delay", "0", "--report", report])
ok(dict(models.get_book(bid))["cover_source"] == "openlibrary", "script stores the Open Library cover")
lines = open(report, encoding="utf-8-sig").read().strip().splitlines()
ok(lines[0].startswith("book_id") and len(lines) == 3, "script lists the books still without a cover")
other = int(lines[1].split(",")[0])
todo = os.path.join(tempfile.mkdtemp(), "covers_todo.json")
json.dump({str(other): "https://example.org/found.jpg"}, open(todo, "w"))
covers.http_get = fake_get([("https://example.org/found.jpg", ("u", jpg(300, 450)))])
fc.main(["--urls", todo, "--delay", "0"])
ok(dict(models.get_book(other))["cover_source"] == "web", "covers found in a web search are stored (source web)")
c.delete(f"/api/admin/books/{bid}/cover")
recs = tempfile.mkdtemp()
json.dump({"book_id": bid, "match": {"status": "matched"}}, open(os.path.join(recs, f"{bid}.json"), "w"))
json.dump({"book_id": other, "match": {"status": "needs_confirmation"}}, open(os.path.join(recs, f"{other}.json"), "w"))
json.dump([{"book_id": 1}], open(os.path.join(recs, "_list.json"), "w"))
calls.clear()
covers.http_get = fake_get([("https://covers.openlibrary.org/b/isbn/9780140328721", ("u", COVER))])
fc.main(["--records", recs, "--delay", "0", "--report", report])
ok(dict(models.get_book(bid))["cover_source"] == "openlibrary" and len(calls) == 1, "--records searches only identified books of a research folder that lack a cover")

# ---- summary: staff edit, chat facts, limits
r = c.patch(f"/api/books/{bid}", json={"summary": "  رواية عن   قرية سودانية وعرس غير متوقع.  "})
b = dict(models.get_book(bid))
ok(r.status_code == 200 and b["summary"] == "رواية عن قرية سودانية وعرس غير متوقع." and b["summary_source"] == "staff", "staff saves a summary (spaces tidied)")
ok(A.book_fact(b, None, "x").get("summary") == b["summary"], "chat facts carry the summary")
ok(c.patch(f"/api/books/{bid}", json={"summary": "س" * 701}).status_code == 400, "summary over 700 characters refused")
ok("summary" in __import__("chat").RESPOND_SYSTEM_AR and "لا تضف عليها" in __import__("chat").RESPOND_SYSTEM_AR, "prompt tells the model how to use the summary")
ok(c.patch(f"/api/books/{bid}", json={"cover_url": "https://evil.example/x.png"}).status_code == 200
   and dict(models.get_book(bid))["cover_url"] != "https://evil.example/x.png", "cover_url cannot be set to an outside link through PATCH")

# ---- weekly Rewaa sync: books missing from the new file become out of stock by default
import inspect
ok(inspect.signature(rewaa_import.apply_plan).parameters["missing_mode"].default == "zero"
   and inspect.signature(rewaa_import.build_plan).parameters["missing_mode"].default == "zero", "missing books -> quantity 0 by default")

# ---- research import: summary for new and existing books, cover URLs to a to-do list
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "work.db"); shutil.copy(models.DB_PATH, db)
seed = os.path.join(tmp, "seed.json"); shutil.copy("../data/seed_books.json", seed)
os.makedirs(os.path.join(tmp, "recs", "new")); os.makedirs(os.path.join(tmp, "recs", "existing"))
rows = [{"name": n, "value": v, "confidence": 0.8, "review_count": 3, "conflict": False, "source_site": "goodreads",
         "edition_scope": "original_or_any", "summary": "x", "urls": ["https://example.org/a"]}
        for n, v in (("moods", "warm"), ("pace", "medium"), ("emotional_weight", "light"))]
json.dump({"key": "1-1", "category": "x", "title_ar": "كتاب نبذة تجريبي", "author_ar": "مؤلف", "catalog_genre": "رواية", "rows": rows,
           "summary_ar": "كتاب عن رحلة شاب يبحث عن معنى الصداقة.", "summary_source_url": "https://www.abjjad.com/book/1",
           "cover_image_url": "https://example.org/new.jpg"}, open(os.path.join(tmp, "recs", "new", "1-1.json"), "w"), ensure_ascii=False)
ex = con.execute("select book_id from books where (summary is null or summary='') and book_id != ? limit 1", (bid,)).fetchone()[0]
json.dump({"book_id": ex, "summary_ar": "نبذة لكتاب موجود.", "summary_source_url": "https://www.goodreads.com/book/show/2",
           "cover_image_url": "https://example.org/ex.jpg"}, open(os.path.join(tmp, "recs", "existing", f"{ex}.json"), "w"), ensure_ascii=False)
json.dump({"key": "1-2", "category": "x", "title_ar": "كتاب نبذة منسوخة", "author_ar": "مؤلف", "catalog_genre": "رواية", "rows": rows,
           "summary_ar": "«نص منقول من الموقع»"}, open(os.path.join(tmp, "recs", "new", "1-2.json"), "w"), ensure_ascii=False)
script = os.path.join(os.path.dirname(os.getcwd()), "scripts", "import_enrichment.py")
res = subprocess.run([sys.executable, script, db, seed, os.path.join(tmp, "recs"), "--apply"], capture_output=True, text=True)
if res.returncode:
    print(res.stderr[-1500:])
w = sqlite3.connect(db)
new = w.execute("select book_id, summary, summary_source from books where title='كتاب نبذة تجريبي'").fetchone()
ok(new and new[1] == "كتاب عن رحلة شاب يبحث عن معنى الصداقة." and new[2] == "https://www.abjjad.com/book/1", "new book gets its summary and source")
ok(w.execute("select summary from books where book_id=?", (ex,)).fetchone()[0] == "نبذة لكتاب موجود.", "existing book gets a summary")
ok(w.execute("select summary from books where title='كتاب نبذة منسوخة'").fetchone()[0] is None, "a quoted (copied) summary is refused")
todo = json.load(open(os.path.join(tmp, "recs", "covers_todo.json")))
ok(todo.get(str(new[0])) == "https://example.org/new.jpg" and todo.get(str(ex)) == "https://example.org/ex.jpg", "cover URLs from research go to covers_todo.json")
import covers as _cv
ok(_cv.small_variant("https://m.media-amazon.com/images/S/compressed.photo.goodreads.com/books/1i/2.jpg").endswith("/2._SX300_.jpg")
   and _cv.small_variant("https://books.google.com/x?img=1") == "https://books.google.com/x?img=1", "Goodreads/Amazon covers requested at ~300 px")
_orig_get = _cv.http_get
_calls = []
def _fake_get(url, timeout=15):
    _calls.append(url)
    if "_SX300_" in url:
        raise _cv.urllib.error.URLError("no small copy")
    return url, b"\x89PNG\r\n\x1a\n" + b"\x00" * 200  # not a valid cover; only the requested URLs matter here
_cv.http_get = _fake_get
try:
    _cv.save_from_url(999999, "https://m.media-amazon.com/images/S/compressed.photo.goodreads.com/books/1i/2.jpg")
except Exception:
    pass
ok(len(_calls) == 2 and "_SX300_" in _calls[0] and "_SX300_" not in _calls[1], "falls back to the original when the small copy is missing")
_calls.clear()
_gr_page = b'<meta property="og:image" content="https://m.media-amazon.com/images/S/compressed.photo.goodreads.com/books/9i/9.jpg">'
def _fake_page(url, timeout=15):
    _calls.append(url)
    if "/book/show/" in url:
        return url, _gr_page
    raise _cv.urllib.error.URLError("stop")
_cv.http_get = _fake_page
try:
    _cv.save_from_url(999999, "https://www.goodreads.com/en/book/show/40495148")
except Exception:
    pass
ok(_calls[:2] == ["https://www.goodreads.com/en/book/show/40495148",
                  "https://m.media-amazon.com/images/S/compressed.photo.goodreads.com/books/9i/9._SX300_.jpg"],
   "a researched Goodreads book page -> its og:image (small copy)")
_cv.PAGE_RETRY_DELAY = 0
_ld = b'<script type="application/ld+json">{"@type":"Book","image":"https://m.media-amazon.com/images/S/compressed.photo.goodreads.com/books/7i/7.jpg"}</script>'
ok(_cv._page_image(_ld.decode()) == "https://m.media-amazon.com/images/S/compressed.photo.goodreads.com/books/7i/7.jpg", "no og:image -> JSON-LD image used")
_seq = [b"<html><title>stripped</title></html>", _gr_page]
_calls.clear()
def _flaky(url, timeout=15):
    _calls.append(url)
    if "/book/show/" in url:
        return url, _seq.pop(0)
    raise _cv.urllib.error.URLError("stop")
_cv.http_get = _flaky
try:
    _cv.save_from_url(999999, "https://www.goodreads.com/book/show/2")
except Exception:
    pass
ok(len(_calls) >= 3 and _calls[1] == "https://www.goodreads.com/book/show/2", "a stripped page is retried once")
_cv.http_get = lambda url, timeout=15: (url, b'<meta property="og:image" content="https://s.gr-assets.com/assets/nophoto/book/111x148.png">')
try:
    _cv.save_from_url(999999, "https://www.goodreads.com/book/show/1")
    _bad = False
except _cv.CoverError:
    _bad = True
ok(_bad, "a book page without a real cover is refused")
ok(_cv._BOOK_PAGE.match("https://www.abjjad.com/book/1034086088726/x") and not _cv._BOOK_PAGE.match("https://example.org/a.jpg"), "book-page URLs recognised")
ok(_cv.is_book_page("https://www.thebookhome.com/product/index/69586/") and not _cv.is_book_page("https://www.neelwafurat.com/itempage.aspx?id=egb1") and not _cv.is_book_page("https://www.neelwafurat.com/search.aspx?q=x") and not _cv.is_book_page("https://www.goodreads.com/search?q=x"), "bookshop product pages recognised, search pages not")
ok(_cv._page_image('<meta property="og:image" content="https://shop.example/img/site-logo.png">') is None, "a site logo is not taken as a cover")
_cv.http_get = _orig_get
print(f"TOTAL PASS={P} FAIL={F}")
