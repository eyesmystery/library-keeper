"""Other books by the author / from the publisher, opened from the book page (2026-10-08)."""
import os, sys, sqlite3
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models, app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
con = sqlite3.connect(models.DB_PATH)
c = A.app.test_client()
# an author with several books in stock
author, n = con.execute("""select author, count(*) n from books where active=1 and quantity>0 and author not in ('', 'غير معروف')
                           group by author having n >= 3 order by n desc limit 1""").fetchone()
bid = con.execute("select book_id from books where author=? and active=1 and quantity>0 order by book_id limit 1", (author,)).fetchone()[0]
d = c.get(f"/api/more?by=author&book_id={bid}").get_json()
ok(d["name"] == author and len(d["books"]) >= 2, "other books by the author")
ok(all(b["book_id"] != bid for b in d["books"]), "the book being read is left out")
ok(all(b["quantity"] > 0 and b["active"] for b in d["books"]), "only books in stock")
ok(all("location" in b and "origin" in b for b in d["books"]), "books come in the chat's card shape")
pub = con.execute("""select publisher from books where active=1 and quantity>0 and length(publisher) > 5
                     group by publisher having count(*) >= 3 limit 1""").fetchone()[0]
pid = con.execute("select book_id from books where publisher=? and active=1 and quantity>0 limit 1", (pub,)).fetchone()[0]
d = c.get(f"/api/more?by=publisher&book_id={pid}").get_json()
ok(d["name"] == pub and d["books"] and all(A.publisher_key(b["publisher"]) == A.publisher_key(pub) for b in d["books"]), "other books from the publisher")
titles = [(A.normalize(b["title"]), A.normalize(b["author"] or "")) for b in d["books"]]
ok(len(titles) == len(set(titles)), "an edition repeated is listed once")
ok(c.get("/api/more?by=author").status_code == 400 and c.get("/api/more?by=x&book_id=1").status_code == 404
   and c.get("/api/more?by=author&book_id=99999999").status_code == 404, "bad requests")
un = con.execute("select book_id from books where author='غير معروف' limit 1").fetchone()
if un:
    ok(c.get(f"/api/more?by=author&book_id={un[0]}").get_json()["books"] == [], "an unknown author opens nothing")
page = c.get("/").get_data(as_text=True)
ok('id="moreSheet"' in page and 'id="dPublisher"' in page, "book page has the publisher line and the sheet")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
