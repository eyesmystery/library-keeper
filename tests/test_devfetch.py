"""[DEVFETCH] Ibrahim 2026-10-29: «انا ابغى ابحث يدويا يعني يشوف المصدر وياخذ البيانات» — «اجلب من الرابط» in the developer's
search block: the pasted page is read (JSON-LD, «label: value» rows, meta tags), shown beside our values, and the ticked
values are saved with the page as their source. No network: the page and the address lookup are faked."""
import os, sys, sqlite3, json, socket, random, struct, zlib
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, covers, page_extract as PE
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

def _png(w, h):
    raw = b"".join(b"\x00" + bytes(random.getrandbits(8) for _ in range(w * 3)) for _ in range(h))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")
PNG = _png(120, 180)

SHOP = """<html><head><title>1984 – بنيان</title><meta property="og:image" content="https://shop.example.com/cdn/1984.png">
<script type="application/ld+json">{"@context":"http://schema.org/","@type":"Product","name":"1984","brand":{"@type":"Brand","name":"جورج أورويل"},
"gtin13":"9786589090540","image":"https://shop.example.com/cdn/1984.png"}</script></head><body>
<div class="desc"><p>عدد الصفحات : ٤٠٧ صفحة</p><p>ترجمة : حازم حامد سلامة</p><p>دار النشر : الأهلية للنشر والتوزيع</p>
<table><tr><th>المؤلف</th><td>جورج أورويل</td></tr><tr><th>سنة النشر</th><td>2019</td></tr></table>
<p>الصفحات الرئيسية</p></div></body></html>"""
GR = """<html><head><meta property="og:title" content="1984 by George Orwell | Goodreads">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Book","name":"1984","image":"https://gr.example.com/40961427.jpg",
"bookFormat":"Paperback","numberOfPages":298,"inLanguage":"English","author":[{"@type":"Person","name":"George Orwell"}],"isbn":"9780451524935"}</script>
</head><body></body></html>"""
NEXT = """<html><head><title>Aftermath | Goodreads</title><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"apolloState":
{"Book:kca1":{"__typename":"Book","title":"Aftermath","description":"After the disaster, a young woman returns to her village to look for her brother. <i>A story of loss.</i>",
"details":{"numPages":312,"isbn13":"9780000000002","publisher":"Example Press"}}}}}}</script></head><body>""" + "x" * 1200 + """</body></html>"""
SITE = """<html><head><title>Some book page</title><meta name="description" content="Example Books is the best online bookshop for every reader in the region.">
</head><body>""" + "y" * 1200 + "</body></html>"
HV = "<html><head><title>Human Verification</title></head><body>" + "z" * 1200 + "</body></html>"
PAGES = {"https://shop.example.com/products/1984": SHOP, "https://www.goodreads.example.com/book/show/1": GR,
         "https://www.goodreads.example.com/book/show/2": NEXT, "https://shop.example.com/site": SITE, "https://shop.example.com/hv": HV}

def fake_get(url, timeout=15):
    if url in PAGES:
        return url, PAGES[url].encode("utf-8")
    if url.endswith((".png", ".jpg")):
        return url, PNG
    raise OSError("no page")
covers.http_get = fake_get
_real_gai = socket.getaddrinfo
def fake_gai(host, port, *a, **k):
    ip = "127.0.0.1" if host in ("localhost", "intranet.example.com") else "93.184.216.34"
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
socket.getaddrinfo = fake_gai

PE.RETRY_DELAY = 0
r = PE.extract("https://shop.example.com/products/1984")
f = r["fields"]
ok(f.get("title") == "1984" and f.get("isbn") == "9786589090540", f"shop page: title and ISBN from JSON-LD ({f})")
ok(f.get("publisher") == "الأهلية للنشر والتوزيع", "the shop's «brand» (the author there) is not taken as the publisher; «دار النشر :» is")
ok(f.get("translator") == "حازم حامد سلامة" and f.get("page_count") == 407, "«ترجمة :» and «عدد الصفحات : ٤٠٧ صفحة» (Arabic digits) read")
ok(f.get("author") == "جورج أورويل" and f.get("publication_year") == 2019, "a label in one table cell and its value in the next")
ok(r["cover"] == "https://shop.example.com/cdn/1984.png", "the page's cover image")
g = PE.extract("https://www.goodreads.example.com/book/show/1")["fields"]
ok(g == {"title": "1984", "author": "George Orwell", "isbn": "9780451524935", "page_count": 298}, f"a Goodreads-like Book JSON-LD ({g})")
for bad, why in (("ftp://shop.example.com/x", "not http(s)"), ("http://localhost:8802/api", "this computer"),
                 ("https://intranet.example.com/p", "a private address")):
    try:
        PE.extract(bad); ok(False, f"refused: {why}")
    except PE.PageError:
        ok(True, f"refused: {why}")
try:
    PE.extract("https://shop.example.com/missing"); ok(False, "a page that cannot be read says so")
except PE.PageError as e:
    ok("ما قدرت أقرأ الصفحة" in str(e), "a page that cannot be read says so")

n = PE.extract("https://www.goodreads.example.com/book/show/2")
ok(n["description"].startswith("After the disaster") and "<i>" not in n["description"] and n["description_arabic"] is False
   and n["fields"].get("page_count") == 312 and n["fields"].get("isbn") == "9780000000002", "a Goodreads-like __NEXT_DATA__ Book: description and details")
ok(PE.extract("https://shop.example.com/site")["description"] is None, "a site's own blurb in meta description is not taken as the book's")
try:
    PE.extract("https://shop.example.com/hv"); ok(False, "a «human verification» page says so")
except PE.PageError as e:
    ok("أنك إنسان" in str(e), "a «human verification» page says so")

PAGES["https://shop.example.com/empty"] = ""
try:
    PE.extract("https://shop.example.com/empty"); ok(False, "an empty page says so")
except PE.PageError as e:
    ok("فاضية" in str(e), "an empty page (after one retry) says so")

con = sqlite3.connect(models.DB_PATH)
bid = con.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 1").fetchone()[0]
con.execute("update books set isbn=NULL, translator=NULL, page_count=NULL where book_id=?", (bid,)); con.commit()
d = login(A.app.test_client(), "developer"); s = login(A.app.test_client(), "staff")
H = lambda c: {"X-CSRF-Token": c.csrf}
URL = "https://shop.example.com/products/1984"
ok(s.post(f"/api/admin/books/{bid}/from-page", json={"url": URL}, headers=H(s)).status_code == 403, "developer only")
r = d.post(f"/api/admin/books/{bid}/from-page", json={"url": URL}, headers=H(d))
j = r.get_json()
ok(r.status_code == 200 and j["fields"]["isbn"] == "9786589090540" and j["current"]["isbn"] is None, "the developer gets the page's values and ours")
ok(d.post(f"/api/admin/books/{bid}/from-page", json={"url": "http://localhost/x"}, headers=H(d)).status_code == 400, "a local address is refused (400)")
before = con.execute("select isbn, translator, page_count from books where book_id=?", (bid,)).fetchone()
ok(before == (None, None, None), "reading the page wrote nothing")
r = d.patch(f"/api/books/{bid}", json={"isbn": j["fields"]["isbn"], "translator": j["fields"]["translator"],
                                         "page_count": j["fields"]["page_count"], "source_url": URL}, headers=H(d))
row = con.execute("select isbn, translator, page_count from books where book_id=?", (bid,)).fetchone()
ok(r.status_code == 200 and row == ("9786589090540", "حازم حامد سلامة", 407), f"ticked values saved ({row})")
det = json.loads(con.execute("select details from audit_log where action='book.update' and target=? order by log_id desc limit 1", (str(bid),)).fetchone()[0])
ok(det.get("source") == URL, "with the page as their source in the audit")
r = d.post(f"/api/admin/books/{bid}/cover/choose", json={"url": j["cover"]}, headers=H(d))
ok(r.status_code == 200 and con.execute("select cover_source from books where book_id=?", (bid,)).fetchone()[0] == "web",
   f"the page's cover is offered to «cover/choose» and saved ({r.status_code})")
# «اكتب نبذة بالعربي من هذا الوصف»
calls = []
def fake_complete(system, messages, schema, max_tokens=700, active=None):
    calls.append((system, messages)); return {"summary": "  تعود شابة إلى قريتها بعد الكارثة بحثًا عن أخيها.  "}
A.llm.complete_json = fake_complete
DESC = "After the disaster, a young woman returns to her village to look for her brother. A story of loss and hope."
ok(s.post(f"/api/admin/books/{bid}/summary-from-text", json={"text": DESC}, headers=H(s)).status_code == 403, "Arabic summary: developer only")
ok(d.post(f"/api/admin/books/{bid}/summary-from-text", json={"text": "short"}, headers=H(d)).status_code == 400, "a too-short description is refused")
r = d.post(f"/api/admin/books/{bid}/summary-from-text", json={"text": DESC}, headers=H(d)); j2 = r.get_json()
ok(r.status_code == 200 and j2["summary"] == "تعود شابة إلى قريتها بعد الكارثة بحثًا عن أخيها.", f"the model's Arabic summary is returned, trimmed ({j2})")
ok("بكلماتك أنت لا ترجمة حرفية" in calls[-1][0] and DESC in calls[-1][1][0]["content"] and "الكتاب بالعربية" in calls[-1][1][0]["content"],
   "asked for an Arabic نبذة in our own words from the original description, with the book's Arabic title")
ok(con.execute("select summary from books where book_id=?", (bid,)).fetchone()[0] != j2["summary"], "nothing saved until the developer saves it")
def boom(*a, **k): raise RuntimeError("timeout")
A.llm.complete_json = boom
ok(d.post(f"/api/admin/books/{bid}/summary-from-text", json={"text": DESC}, headers=H(d)).status_code == 503, "model down → 503 with a message")
r = d.patch(f"/api/books/{bid}", json={"summary": j2["summary"], "source_url": "https://www.goodreads.example.com/book/show/2"}, headers=H(d))
ok(r.status_code == 200 and con.execute("select summary_source from books where book_id=?", (bid,)).fetchone()[0] == "https://www.goodreads.example.com/book/show/2",
   "saved with the original's page as the summary's source")
html = open(os.path.join(os.path.dirname(A.__file__), "..", "admin", "index.html"), encoding="utf-8").read()
ok("async function devSummary(b, j)" in html and "اكتب نبذة بالعربي من هذا الوصف" in html and "summary: text, source_url: j.url" in html,
   "the description, «اكتب نبذة بالعربي من هذا الوصف» and «احفظ النبذة» are on the page")
ok('<button id="devFetch" class="btn" type="button">اجلب من الرابط</button>' in html and "async function devFetch(b)" in html
   and "async function devApply(b, j)" in html and "source_url: j.url" in html, "the button and the pick-and-save table are on the page")
socket.getaddrinfo = _real_gai
con.close()
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
