import os, sys, json, sqlite3, shutil
sys.path.insert(0, os.getcwd())
import models, app as A
from auth_helper import login
models.init_db(); models.seed_books()
# The frozen seed has no original language; the live catalog has one for 97% of its books. «رواية عربية» now
# asks for works written in Arabic (2026-10-06), so untranslated seed books get it here as in the live data.
_c = sqlite3.connect(models.DB_PATH)
_c.execute("update books set original_language='ar' where original_language is null and coalesce(translator,'')=''"); _c.commit(); _c.close()
c = login(A.app.test_client())
ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg)
for path in ["/", "/static/styles.css", "/static/app.js", "/admin", "/api/books"]:
    ok(c.get(path).status_code == 200, "GET " + path)
css = open("../frontend/styles.css", encoding="utf-8").read()
html = open("../frontend/index.html", encoding="utf-8").read()
js = open("../frontend/app.js", encoding="utf-8").read()
ok(".host-mark{width:56px;aspect-ratio:2652/1442}" in css and html.count('class="host-mark" src="/static/assets/manar-logo.svg" alt="منار"') == 4 and "marsa" not in css.lower() and "marsa" not in html.lower()
   and "مرسى" not in html and "مرسى" not in js and "Marsa" not in js,
   "Chat and book-detail headers carry the «منار» logo (old names and logos are gone)")
ok('id="modelStatus" class="model-status" role="status" hidden' in html and
   "setModelStatus(data.understanding !== 'ai')" in js,
   "Visitor chat reveals a compact fallback notice when AI is unavailable")
ok(".brand-signature{width:max-content;max-width:100%;margin:4px 0 46px auto" in css and
   ".guide-lockup{display:grid;direction:rtl;justify-items:end;gap:8px}" in css and
   "aspect-ratio:2652/1442" in css and 'class="brand-mark" src="/static/assets/manar-logo.svg" alt="منار"' in html and 'class="feedback-host" src="/static/assets/manar-logo.svg" alt="منار"' in html and "smart-tag" not in css + html
   and 'viewBox="0 0 1144 627"' in open("../frontend/assets/thalath-smart.svg", encoding="utf-8").read()[:200] and c.get("/static/assets/thalath-smart.svg").status_code == 200
   and "<title>منار | مكتبة ثلاث</title>" in html and "أنا منار" in js,
   "Welcome and feedback show the «منار» logo; the assistant is named «منار»")
ok("@media(min-width:700px){" in css and ".chat-books{grid-template-columns:repeat(2,minmax(0,1fr))" in css
   and ".app-shell{position:relative;width:min(100%,480px)" in css,
   "Visitor pages have a tablet layout from 700px (full screen, two book cards per row) and keep the phone layout")
books = c.get("/api/books").get_json()
N = len(json.load(open("../data/seed_books.json")))
ok(len(books) == N and sum(b["active"] for b in books) == N, f"catalog {N} rows / {N} active")
inactive = {b["book_id"] for b in books if not b["active"]}
def rec(q, ex=()):
    return c.post("/api/recommend", json={"query": q, "exclude": list(ex)}).get_json()
for q in ["أبغى رواية عربية ممتعة", "أبغى كتاب خفيف مع القهوة", "أبغى أبدأ عادة القراءة",
          "أبغى هدية لشخص يحب القراءة", "روايات نجيب محفوظ", "رواية ما تكون طويلة", "مسرحية لشكسبير", "أبغى سيرة ذاتية", "روايات غسان كنفاني", "أبغى ديوان شعر", "أشعر بالملل وأبغى رواية", "كتاب تاريخ", "كتب غازي القصيبي", "أبغى كتاب عن الفضاء", "أبغى أقرأ في الفلسفة"]:
    r = rec(q)
    print("\nQ:", q, "| msg:", r["message"])
    for b in r["results"]:
        print("  -", b["title"], "|", b["author"], "|", b["genre"], b["page_count"], "|", b["reason"])
    ok(3 <= len(r["results"]) <= 4, "3-4 results")
    ok(not ({b["book_id"] for b in r["results"]} & inactive), "no inactive books")
# more button: exclusion until exhausted
seen = []
for i in range(20):
    r = rec("مسرحية", seen)
    ids = [b["book_id"] for b in r["results"]]
    if not ids: print("exhausted after", i, "rounds:", r["message"]); break
    ok(not set(ids) & set(seen), f"round {i} new ids only")
    seen += ids
# feedback
ch = c.post("/api/choice", json={"book_id": books[0]["book_id"]}).get_json()
ok(c.post("/api/feedback", json={"rating": "perfect", "choice_id": ch["choice_id"], "choice_token": ch["choice_token"]}).status_code == 201, "feedback saved")
ok(c.post("/api/feedback", json={"rating": "bad"}).status_code == 400, "invalid rating rejected")
ok(c.post("/api/feedback", json={"rating": "no", "book_id": books[0]["book_id"]}).status_code == 400, "a rating without a choice is refused")
con = sqlite3.connect(models.DB_PATH)
ok(con.execute("select count(*) from feedback").fetchone()[0] == 1, "feedback row stored")
# patch validation
bid = books[0]["book_id"]
ok(c.patch(f"/api/books/{bid}", json={"quantity": 2.5}).status_code == 400, "fractional qty rejected")
ok(c.patch(f"/api/books/{bid}", json={"price": -1}).status_code == 400, "negative price rejected")
ok(c.patch("/api/books/99999", json={"price": 1}).status_code == 404, "missing book 404")
ok(c.patch(f"/api/books/{bid}", json={"quantity": 7}).status_code == 200, "valid patch ok")
ok(con.execute("select quantity, typeof(quantity) from books where book_id=?", (bid,)).fetchone() == (7, "integer"), "quantity stored as integer")
# fresh seed from JSON matches
os.rename(models.DB_PATH, models.DB_PATH + ".old")
models.init_db(); models.seed_books()
con2 = sqlite3.connect(models.DB_PATH)
ok(con2.execute("select count(*), sum(active) from books").fetchone() == (N, N), f"fresh seed from JSON gives {N}/{N}")
ok(con2.execute("select author from books where title='حديث الصباح والمساء'").fetchone()[0] == "نجيب محفوظ", "corrected author in fresh seed")
ok(con2.execute("select count(*) from books where title in ('موسم الهجرة إلى الشمال','الأيام','مجنون ليلى') and publisher is null").fetchone()[0] == 3, "replacements seeded with unknown publisher")
def genres_of(q): return {b["genre"] for b in rec(q)["results"] if b["match"] == "direct"}
ok(genres_of("أبغى ديوان شعر") == {"شعر"}, "poetry query -> poetry only")
ok("شعر" not in genres_of("أشعر بالملل وأبغى رواية"), "أشعر does not trigger poetry")
ok(all(b["author"] == "غازي القصيبي" for b in rec("كتب غازي القصيبي")["results"][:3]), "author search القصيبي")
ok(con2.execute("select count(*) from books where shelf_location glob '[A-H][0-9]*'").fetchone()[0] == 0, "no invented shelf codes (locations come from staff or library sections)")
ok(html.count('class="header-brand brand-home" type="button"') == 2 and html.count('class="detail-brand brand-home" type="button"') == 2
   and "function goHome() { if (noteMode) closeNote(); closeMore(); showSection('welcome'); }" in js
   and "document.querySelectorAll('.brand-home').forEach(button => button.addEventListener('click', goHome));" in js
   and "home: 'الرجوع للصفحة الرئيسية'" in js and "home: 'Back to the start page'" in js, "the «منار» logo in the chat and detail headers goes back to the start page")
