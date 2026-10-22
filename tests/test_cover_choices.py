"""«ابحث عن غلاف في النت» with choices (Ibrahim 2026-10-13): several sources searched at once, every image
downloaded and checked, duplicates removed; the staff member picks one, and only an offered image can be saved."""
import os, sys, json, random, struct, zlib, sqlite3, urllib.error
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models, covers, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

random.seed(11)
def png(w, h):
    raw = b"".join(b"\x00" + bytes(random.getrandbits(8) for _ in range(w * 3)) for _ in range(h))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")
A1, A2, A3 = png(120, 180), png(130, 200), png(100, 150)
BANNER = png(400, 200)

models.init_db()
con = sqlite3.connect(models.DB_PATH)
bid = con.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 1").fetchone()[0]
con.execute("update books set title='مدن الملح', author='عبد الرحمن منيف', isbn='9789953212345' where book_id=?", (bid,)); con.commit()

PAGES = {
    "https://covers.openlibrary.org/b/isbn/9789953212345-L.jpg?default=false": A1,
    "https://openlibrary.org/search.json": json.dumps({"docs": [{"title": "مدن الملح", "cover_i": 5, "key": "/works/OL1W"},
                                                                {"title": "كتاب آخر", "cover_i": 6}]}).encode(),
    "https://covers.openlibrary.org/b/id/5-L.jpg": A1,          # the same picture again: shown once
    "https://www.googleapis.com/books/v1/volumes": json.dumps({"items": [{"volumeInfo": {"title": "مدن الملح",
        "imageLinks": {"thumbnail": "http://books.google.com/books/content?id=X&zoom=1&edge=curl"}, "infoLink": "https://books.google.com/x"}}]}).encode(),
    "https://books.google.com/books/content?id=X&zoom=1": A2,
    "https://www.goodreads.com/search": b'<a href="/book/show/123.Cities_of_Salt">x</a>',
    "https://www.goodreads.com/book/isbn/9789953212345": b'<meta property="og:image" content="https://i.gr-assets.com/b3.jpg">',
    "https://www.goodreads.com/book/show/123.Cities_of_Salt": b'<meta property="og:image" content="https://i.gr-assets.com/banner.jpg">',
    "https://i.gr-assets.com/b3.jpg": A3,
    "https://i.gr-assets.com/banner.jpg": BANNER,               # a wide banner: refused
    "https://www.abjjad.com/search": b"<html>nothing</html>",
}
calls = []
def fake_get(url, timeout=15):
    calls.append(url)
    for k, v in PAGES.items():
        if url == k or url.startswith(k + "?"):
            return url, v
    raise urllib.error.URLError("no such page in the test")
covers.http_get = fake_get

book = dict(models.get_book(bid))
got = covers.search_candidates(book)
srcs = [c["source"] for c in got]
ok(len(got) == 3, f"three different covers offered (got {len(got)}: {srcs})")
ok(srcs[0] == "openlibrary", "the ISBN match comes first")
ok("google_books" in srcs and "goodreads" in srcs, "Google Books and Goodreads are searched too")
ok(all(c["preview"].startswith("data:image/png;base64,") for c in got), "each choice carries its own preview (no third-party image in the page)")
ok(not any("banner" in c["url"] for c in got), "a wide banner is not offered")
ok(len({c["preview"] for c in got}) == len(got) and not any(c["url"].endswith("5-L.jpg") for c in got),
   "the same picture found twice (ISBN and title search) is offered once")
ok(any("edge=curl" not in c["url"] and c["url"].startswith("https://books.google.com") for c in got), "Google Books image asked over https, no page-curl")

c = login(A.app.test_client(), "staff")
ok(A.app.test_client().post(f"/api/admin/books/{bid}/cover/search").status_code == 401, "search needs sign-in")
r = c.post(f"/api/admin/books/{bid}/cover/search")
ok(r.status_code == 200 and len(r.get_json()["candidates"]) == 3, "staff searches from the book card")
pick = next(x for x in r.get_json()["candidates"] if x["source"] == "goodreads")
r2 = c.post(f"/api/admin/books/{bid}/cover/choose", json={"url": "http://192.168.1.1/admin.png"})
ok(r2.status_code == 400 and r2.get_json()["error"] == "not offered", "an address that was not offered is refused (no download)")
r3 = c.post(f"/api/admin/books/{bid}/cover/choose", json={"url": pick["url"]})
row = dict(models.get_book(bid))
ok(r3.status_code == 200 and row["cover_source"] == "goodreads" and row["cover_url"].startswith("/covers/"), "the chosen cover is saved locally with its source")
saved = open(covers.cover_path(row["cover_url"].split("/covers/", 1)[1].split("?")[0]), "rb").read()
ok(saved == A3, "the saved file is the picture that was chosen")

# Abjjad: «kw» search, pages named for the book first, the page title checked, the real cover (not the banner)
A4, A5, A6 = png(110, 165), png(140, 210), png(90, 140)
PAGES.update({
    "https://www.abjjad.com/search": ('<a href="/book/1/%D9%83%D8%AA%D8%A7%D8%A8-%D8%A2%D8%AE%D8%B1/9">x</a>'
                                      '<a href="/book/2/%D9%85%D8%AF%D9%86-%D8%A7%D9%84%D9%85%D9%84%D8%AD/8">x</a>'
                                      '<a href="/book/3/%D9%85%D8%AF%D9%86-%D8%A7%D9%84%D9%85%D9%84%D8%AD-2/7">x</a>').encode(),
    "https://www.abjjad.com/book/2/%D9%85%D8%AF%D9%86-%D8%A7%D9%84%D9%85%D9%84%D8%AD/8":
        ('<meta property="og:title" content="مدن الملح - عبد الرحمن منيف">'
         '<meta property="og:image" content="https://cdn.abjjad.com/pub/share-banner.png">'
         '<div data-book-cover-url="https://cdn.abjjad.com/pub/aaa-192X290.png"></div>').encode(),
    "https://www.abjjad.com/book/3/%D9%85%D8%AF%D9%86-%D8%A7%D9%84%D9%85%D9%84%D8%AD-2/7":
        ('<meta property="og:title" content="كتاب مختلف تماما">'
         '<div data-book-cover-url="https://cdn.abjjad.com/pub/bbb-192X290.png"></div>').encode(),
    "https://cdn.abjjad.com/pub/aaa.png": A4,
    "https://cdn.abjjad.com/pub/bbb.png": A6,
    "https://cdn.abjjad.com/pub/share-banner.png": BANNER,
    "https://ar.wikipedia.org/w/api.php": json.dumps({"query": {"pages": {
        "1": {"title": "مدن الملح (رواية)", "index": 1, "thumbnail": {"source": "https://upload.wikimedia.org/ar-salt.jpg"}},
        "2": {"title": "مدن أخرى", "index": 2, "thumbnail": {"source": "https://upload.wikimedia.org/other.jpg"}}}}}).encode(),
    "https://en.wikipedia.org/w/api.php": json.dumps({"query": {"pages": {"7": {"title": "Cities of Salt",
        "thumbnail": {"source": "https://upload.wikimedia.org/cities.jpg"}}}}}).encode(),
    "https://upload.wikimedia.org/cities.jpg": A5,
    "https://upload.wikimedia.org/ar-salt.jpg": png(115, 170),
    "https://upload.wikimedia.org/other.jpg": A6,
})
calls.clear()
got = covers.search_candidates(book, sources=("abjjad", "wikipedia"))
ok(any(u.startswith("https://www.abjjad.com/search?kw=") for u in calls), "Abjjad is searched with «kw» (its «q» is ignored)")
ok(not any("/book/1/" in u for u in calls), "an Abjjad result whose address names another book is not opened")
ab = [c for c in got if c["source"] == "abjjad"]
ok(len(ab) == 1 and ab[0]["url"] == "https://cdn.abjjad.com/pub/aaa.png", "Abjjad: the full book cover is taken, not the share banner; a page of another title is skipped")
wk = [c for c in got if c["source"] == "wikipedia"]
ok(len(wk) == 1 and wk[0]["url"].endswith("ar-salt.jpg"), "Wikipedia: only the Arabic article named for the book (no English original's cover); other articles ignored")
ok(not any("en.wikipedia.org" in u for u in calls), "the English Wikipedia is not asked")


# web image search: «غلاف كتاب "<title>"»; only results whose page carries the title; landscape pictures dropped
def iusc(murl, purl, t):
    m = json.dumps({"murl": murl, "purl": purl, "t": t}, ensure_ascii=False).replace('"', "&quot;")
    return f'<a class="iusc" style="x" m="{m}" href="#">'
W1, W2, WIDE = png(100, 150), png(120, 120), png(300, 200)
PAGES["https://www.bing.com/images/search"] = ("<html>" + iusc("https://shop.example.com/salt.jpg", "https://shop.example.com/product/مدن-الملح/", "مدن الملح - متجر")
    + iusc("https://i.pinimg.com/x.jpg", "https://pinterest.com/pin/1", "تصميم غلاف كتاب")
    + iusc("https://store.example.org/a.png", "https://store.example.org/p/9", "رواية مدن الملح عبد الرحمن منيف")
    + iusc("https://news.example.net/wide.jpg", "https://news.example.net/مدن-الملح", "مقال عن مدن الملح")
    + iusc("http://192.168.1.5/c.jpg", "http://192.168.1.5/مدن-الملح", "مدن الملح") + "</html>").encode()
PAGES.update({"https://shop.example.com/salt.jpg": W1, "https://store.example.org/a.png": W2, "https://news.example.net/wide.jpg": WIDE,
              "https://i.pinimg.com/x.jpg": png(90, 130)})
calls.clear()
got = covers.search_candidates(book, sources=("web_images",))
q = [u for u in calls if u.startswith("https://www.bing.com/images/search")]
ok(q and "%D8%BA%D9%84%D8%A7%D9%81+%D9%83%D8%AA%D8%A7%D8%A8+%22" in q[0], "the web search asks «غلاف كتاب \"العنوان\"»")
ok([c["label"] for c in got] == ["shop.example.com", "store.example.org"], f"results whose page names the book, labelled with their site (got {[c['label'] for c in got]})")
ok(not any("192.168" in u or "pinimg" in u for u in calls), "a private address and an unrelated design picture are never downloaded")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
