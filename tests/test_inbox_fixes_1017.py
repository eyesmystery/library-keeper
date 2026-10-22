"""[MGR] Library inbox 2026-10-11, re-checked 2026-10-17 (Ibrahim: «نفذ الاصلاحين وحدث صفحة الادارة لكل شيء انحل»):
§3.8 a question about a catalog author is a reading question, not off topic; §3.3 «وش دار نشره؟» is about the book
in view (no «عن «دار النشر»» subject) and one asked work carries all its editions; developer notes may open named books.
Pure checks with stand-in records (no model, no live database)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app, authors, dev_notes
from keyword_prefs import normalize
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# ---------------------------------------------------------------- §3.8 catalog_author_in
REC = {"author_id": 1898, "name_ar": "تركي بن فهد آل سعود"}
NAMES = {normalize("تركي بن فهد آل سعود"): REC, normalize("نجيب محفوظ"): {"author_id": 7, "name_ar": "نجيب محفوظ"}}
_lookup, _ids = authors.lookup, authors.book_ids
authors.lookup = lambda name, conn=None, spelling=True: NAMES.get(" ".join(normalize(name).split()))
authors.book_ids = lambda aid: {3554, 4656} if aid == 1898 else {99}
BOOKS = {3554: {"book_id": 3554, "quantity": 3, "active": 1}, 4656: {"book_id": 4656, "quantity": 2, "active": 1},
         99: {"book_id": 99, "quantity": 0, "active": 1}}
try:
    ok((app.catalog_author_in("هل تركي بن فهد آل سعود شاعر؟", BOOKS) or {}).get("author_id") == 1898,
       "«هل تركي بن فهد آل سعود شاعر؟» -> catalog author 1898 (books in stock)")
    ok(app.catalog_author_in("هل نجيب محفوظ شاعر؟", BOOKS) is None, "an author with no book in stock is not taken")
    ok(app.catalog_author_in("كيف الجو اليوم؟", BOOKS) is None, "no author name -> None")
    ok(app.catalog_author_in("تركي", BOOKS) is None, "a single word is never an author's full name")
finally:
    authors.lookup, authors.book_ids = _lookup, _ids

# ---------------------------------------------------------------- §3.3 book-detail words are not a subject
for u in ("دار النشر", "الناشر", "السعر", "الطبعات", "عدد الصفحات", "publisher"):
    ok(set(normalize(u).split()) <= app._BOOK_DETAIL_WORDS, f"«{u}» is a question about the book, not a subject")
for u in ("تاريخ النشر في العالم العربي", "الحرب", "صناعة الكتاب"):
    ok(not set(normalize(u).split()) <= app._BOOK_DETAIL_WORDS, f"«{u}» stays a subject")

# ---------------------------------------------------------------- §3.3 one fact per asked work
ED = {i: {"book_id": i, "title": "فن الحرب", "author": "سون تزو", "quantity": 3, "active": 1, "price": p}
      for i, p in ((1121, 21.34), (3322, 12.9), (4207, 39.61))}
eds = app.same_book_editions(ED[1121], ED)
ok({e["book_id"] for e in eds} == {3322, 4207}, "the other two editions of «فن الحرب» are found from any one of them")
src = open(app.__file__, encoding="utf-8").read()
ok("_one_per_work" in src and "asked_books = _one_per_work" in src, "asked books are reduced to one per work before the facts")

# ---------------------------------------------------------------- developer notes
seed = json.load(open(dev_notes.SEED, encoding="utf-8"))
refs = [n["ref"] for n in seed["notes"]]
ok(len(refs) == len(set(refs)), "developer-note refs are unique")
ok("DN-2026-10-17-inbox-solved" in refs and "DN-2026-10-17-book-4198" in refs, "the two notes of 2026-10-17 are in the seed")
n4198 = [n for n in seed["notes"] if n["ref"] == "DN-2026-10-17-book-4198"][0]
ok(n4198["action"] == {"type": "books", "label": "عرض الكتاب", "book_ids": [4198]}, "the 4198 note opens that book")
dsrc = open(dev_notes.__file__, encoding="utf-8").read()
ok('action.get("book_ids")' in dsrc and 'b["book_id"] in ids' in dsrc, "books_for() opens notes by book ids as well as by publisher")

print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
