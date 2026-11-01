"""[GRURL] Ibrahim 2026-10-30 «اضف لي كم مطور في الفلتر في المكتبة الكتب اللي ما عندنا لها رابط في goodreads»: the column
books.goodreads_url (+ its source), filled by scripts/import_goodreads_urls.py from our own research only, sent in the
admin catalog for the developer's chip «بلا رابط Goodreads», and used by the visitor's «آراء القرّاء في Goodreads»."""
import os, sys, json, tempfile, importlib.util
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("igu", os.path.join(os.path.dirname(HERE), "scripts", "import_goodreads_urls.py"))
IGU = importlib.util.module_from_spec(spec); spec.loader.exec_module(IGU)
con = models.get_connection()
cols = {r[1] for r in con.execute("PRAGMA table_info(books)")}
ok({"goodreads_url", "goodreads_url_source"} <= cols, "books has goodreads_url and its source")
ok(IGU.canon("see http://goodreads.com/book/show/6163960-x?from=search") == "https://www.goodreads.com/book/show/6163960", "a Goodreads book page is kept in one form")
ok(IGU.canon("https://www.goodreads.com/search?q=x") is None and IGU.canon(None) is None, "a search page or nothing is no book page")

b1, b2, b3 = [r[0] for r in con.execute("SELECT book_id FROM books ORDER BY book_id LIMIT 3")]
con.execute("UPDATE books SET goodreads_url = NULL, summary_source = 'https://www.goodreads.com/book/show/222.Y' WHERE book_id = ?", (b2,))
con.execute("UPDATE books SET goodreads_url = NULL, summary_source = 'abjjad' WHERE book_id IN (?, ?)", (b1, b3))
con.commit()
res = tempfile.mkdtemp(); os.makedirs(os.path.join(res, "reviews_gr"))
json.dump({"book_id": str(b1), "goodreads": "https://www.goodreads.com/book/show/111"}, open(os.path.join(res, "reviews_gr", f"{b1}.json"), "w"))
json.dump({"book_id": str(b2), "goodreads": "https://www.goodreads.com/book/show/999"}, open(os.path.join(res, "reviews_gr", f"{b2}.json"), "w"))
found = IGU.collect(con, res)
ok(found.get(b1, ("",))[0].endswith("/111") and found[b1][1].startswith("reviews_gr"), "a reviews_gr research file gives the page, with its file as source")
ok(found.get(b2, ("",))[0].endswith("/999"), "reviews_gr comes before the summary's source")
ok(b3 not in found, "a book with no Goodreads page anywhere stays without one")

import app as A
from auth_helper import login
con.execute("UPDATE books SET goodreads_url = 'https://www.goodreads.com/book/show/111' WHERE book_id = ?", (b1,)); con.commit()
d = login(A.app.test_client(), "developer")
rows = {r["book_id"]: r for r in d.get("/api/admin/catalog").get_json()}
ok(rows[b1].get("goodreads_url", "").endswith("/111") and rows[b3].get("goodreads_url") is None, "the admin catalog carries goodreads_url for the chip")
html = open(os.path.join(os.path.dirname(HERE), "admin", "index.html"), encoding="utf-8").read()
ok("['nogoodreads', 'بلا رابط Goodreads', b => !b.goodreads_url]" in html, "the developer's chip «بلا رابط Goodreads» in «البيانات»")
# 4. «كيف اقدر اضيف الرابط للكتاب؟»: staff paste the book's Goodreads page in the card
st = login(A.app.test_client(), "staff")
r = st.patch(f"/api/books/{b3}", json={"goodreads_url": "https://www.goodreads.com/book/show/1"}, headers={"X-CSRF-Token": st.csrf})
ok(r.status_code == 403 and con.execute("SELECT goodreads_url FROM books WHERE book_id = ?", (b3,)).fetchone()[0] is None,
   "«الرابط ذا خله للمطور بس»: staff cannot set it")
ok(st.patch(f"/api/books/{b3}", json={"publisher": "دار للاختبار"}, headers={"X-CSRF-Token": st.csrf}).status_code == 200, "staff still edit the other fields")
s = d
H = {"X-CSRF-Token": s.csrf}
r = s.patch(f"/api/books/{b3}", json={"goodreads_url": "https://www.goodreads.com/ar/book/show/6163960-x?from_search=true"}, headers=H)
row = con.execute("SELECT goodreads_url, goodreads_url_source FROM books WHERE book_id = ?", (b3,)).fetchone()
ok(r.status_code == 200 and row[0] == "https://www.goodreads.com/book/show/6163960" and str(row[1]).startswith("developer: "),
   "a pasted Goodreads page is saved in one form, its source the developer")
# AUDIT 2026-10-31 P2: a new page drops the rating read on the old one; saving the same page keeps it
con.execute("UPDATE books SET goodreads_rating = 4.8, goodreads_ratings_count = 1000, goodreads_rating_source = 'https://www.goodreads.com/book/show/6163960', goodreads_rating_at = '2026-10-01' WHERE book_id = ?", (b3,)); con.commit()
s.patch(f"/api/books/{b3}", json={"goodreads_url": "https://www.goodreads.com/book/show/6163960"}, headers=H)
ok(con.execute("SELECT goodreads_rating FROM books WHERE book_id = ?", (b3,)).fetchone()[0] == 4.8, "the same page saved again keeps its rating")
s.patch(f"/api/books/{b3}", json={"goodreads_url": "https://www.goodreads.com/book/show/222"}, headers=H)
ok(tuple(con.execute("SELECT goodreads_rating, goodreads_ratings_count, goodreads_rating_source FROM books WHERE book_id = ?", (b3,)).fetchone()) == (None, None, None),
   "another page drops the old page's rating (AUDIT 2026-10-31 P2)")
s.patch(f"/api/books/{b3}", json={"goodreads_url": "https://www.goodreads.com/book/show/6163960"}, headers=H)
r = s.patch(f"/api/books/{b3}", json={"goodreads_url": "https://www.goodreads.com/search?q=x"}, headers=H)
ok(r.status_code == 400 and con.execute("SELECT goodreads_url FROM books WHERE book_id = ?", (b3,)).fetchone()[0].endswith("/6163960"),
   "a link that is not a Goodreads book page is refused and nothing changes")
aud = con.execute("SELECT details FROM audit_log WHERE action = 'book.update' AND target = ? ORDER BY log_id DESC LIMIT 1", (str(b3),)).fetchone()
ok(aud and "goodreads_url" in aud[0], "the change is in the audit (and so in the inbox §5)")
import inbox_export as IE
ok(IE.FIELD_AR.get("goodreads_url") == "رابط Goodreads" and "goodreads_url_source" in IE.SKIP_FIELDS, "inbox §5 names the field «رابط Goodreads»")
card = d.get(f"/api/admin/books/{b3}").get_json()
ok((card.get("book") or card).get("goodreads_url", "").endswith("/6163960"), "the book card carries the link")
r = s.patch(f"/api/books/{b3}", json={"goodreads_url": ""}, headers=H)
ok(r.status_code == 200 and tuple(con.execute("SELECT goodreads_url, goodreads_url_source FROM books WHERE book_id = ?", (b3,)).fetchone()) == (None, None),
   f"an empty field clears it ({r.status_code} {r.get_data(as_text=True)[:120]})")
ok('<div class="dev-only dev-box">' in html and "if (!isDev()) return;" in html and "String(b.isbn || '').trim() ||" in html, "the field is in a developer-only box; its search uses the ISBN first")
ok("async function saveGoodreads(id)" in html and 'id="fGr"' in html and "saveGoodreads(b.book_id)" in html, "the card has the field and its «حفظ»")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
