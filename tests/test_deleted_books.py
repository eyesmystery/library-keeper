"""«ليس كتابًا» (2026-09-30): delete a non-book product after confirmation, keep a restorable snapshot,
and make the weekly Rewaa import skip it while it stays deleted."""
import os, sys, io, json, sqlite3
sys.path.insert(0, os.getcwd())
import models; models.init_db()
import app as A, authors, deleted_books
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F; print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)
con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
authors.sync()
c = login(A.app.test_client())
staff = login(A.app.test_client(), "staff")

# a book with a card, evidence, relations, author links, a choice, daily counts and a rating
bid, other = [r[0] for r in con.execute("""select b.book_id from books b join attribute_evidence e using(book_id)
                     group by b.book_id order by b.book_id limit 2""")]
con.execute("""insert into book_relations (source_book_id, target_book_id, relation_type, reason_ar, confidence, created_at)
               values (?, ?, 'similar', 'اختبار', 0.8, '2026-09-30')""", (bid, other))
con.execute("""insert into book_relations (source_book_id, target_book_id, relation_type, reason_ar, confidence, created_at)
               values (?, ?, 'similar', 'اختبار', 0.8, '2026-09-30')""", (other, bid))
con.execute("update books set barcode='6281234500017', rewaa_sku='SKU-TOY', quantity=4 where book_id=?", (bid,))
con.execute("insert into book_choices (book_id, created_at, language, match) values (?, '2026-09-30T10:00:00', 'ar', 'x')", (bid,))
con.execute("insert or replace into book_daily (day, book_id, shown, chosen) values ('2026-09-30', ?, 3, 1)", (bid,))
con.execute("insert into feedback (book_id, rating) values (?, 'close')", (bid,))
con.commit()
def counts(book_id):
    return {t: con.execute(f"select count(*) from {t} where {w}", {"id": book_id}).fetchone()[0] for t, w in deleted_books._TABLES}
before = counts(bid)
before_book = dict(con.execute("select * from books where book_id=?", (bid,)).fetchone())
ok(all(before[t] > 0 for t in ("books", "book_profiles", "attribute_evidence", "book_relations", "book_authors", "book_choices", "book_daily", "feedback")),
   "fixture book has rows in every related table")
n_books = con.execute("select count(*) from books").fetchone()[0]

# confirmation is required
r = c.post(f"/api/admin/books/{bid}/delete", json={"reason": "not_a_book"})
ok(r.status_code == 400 and con.execute("select count(*) from books where book_id=?", (bid,)).fetchone()[0] == 1, "no delete without confirm:true")
ok(c.post(f"/api/admin/books/{bid}/delete", json={"confirm": "yes"}).status_code == 400, "confirm must be exactly true")
ok(c.post(f"/api/admin/books/{bid}/delete", json={"confirm": True, "reason": "boring"}).status_code == 400, "unknown reason refused")
ok(c.post("/api/admin/books/99999999/delete", json={"confirm": True}).status_code == 404, "unknown book 404")
ok(A.app.test_client().post(f"/api/admin/books/{bid}/delete", json={"confirm": True}).status_code in (401, 403), "signed-out delete refused")

# staff may delete (card work); snapshot + everything removed
r = staff.post(f"/api/admin/books/{bid}/delete", json={"confirm": True, "reason": "not_a_book"})
d = r.get_json()
ok(r.status_code == 200 and d["ok"] and d["title"] == before_book["title"], "staff delete ok")
ok(all(v == 0 for v in counts(bid).values()), "book and all attached rows removed")
ok(con.execute("select count(*) from books").fetchone()[0] == n_books - 1, "exactly one book removed")
ok(bid not in [b["book_id"] for b in c.get("/api/admin/catalog").get_json()], "gone from the admin catalog")
ok(bid not in [b["book_id"] for b in A.app.test_client().get("/api/books").get_json()], "gone from the visitor list")
dl = c.get("/api/admin/books/deleted").get_json()
ok(len(dl) == 1 and dl[0]["book_id"] == bid and dl[0]["reason_label"] == "ليس كتابًا" and dl[0]["deleted_by"] and dl[0]["barcode"] == "6281234500017",
   "listed in «المحذوفة» with reason, who and barcode")
ok(staff.get("/api/admin/books/deleted").status_code == 200, "staff can see the deleted list")
aud = con.execute("select * from audit_log where action='book.delete' order by log_id desc").fetchone()
ok(aud and int(aud["target"]) == bid and json.loads(aud["details"])["title"] == before_book["title"], "deletion audited")

# weekly import skips it (barcode, sku, or title when the row has no code)
csv_text = ("اسم المنتج,رمز المنتج,الباركود,سعر البيع,الكمية المتوفرة\n"
            f"{before_book['title']},SKU-TOY,6281234500017,10,5\n"
            f"اسم آخر لنفس اللعبة,SKU-TOY,,10,5\n"
            f"اسم ثالث,,6281234500017,10,5\n"
            f"{before_book['title']},,,10,5\n"
            "كتاب جديد للاختبار,SKU-NEW,,20,2\n")
up = c.post("/api/admin/import/upload", data={"file": (io.BytesIO(csv_text.encode("utf-8-sig")), "rewaa.csv")},
            content_type="multipart/form-data").get_json()
plan = c.post("/api/admin/import/plan", json={"upload_id": up["upload_id"], "mapping": up["suggested"]}).get_json()
skipped = [e for e in plan["excluded"] if "محذوف يدويًا" in e["reason"]]
ok(len(skipped) == 4, f"import skips the deleted product by barcode, sku and title ({len(skipped)})")
ok([x["title"] for x in plan["new"]] == ["كتاب جديد للاختبار"], f"other new rows still added {[x['title'] for x in plan['new']]} {plan['invalid']}")

# restore brings everything back
did = dl[0]["deletion_id"]
ok(staff.post(f"/api/admin/books/deleted/{did}/restore").status_code == 403, "staff cannot restore (developer only, ADMSTF)")
ok(c.post(f"/api/admin/books/deleted/{did}/restore").status_code == 403, "manager cannot restore (developer only, ADMSTF)")
c = login(A.app.test_client(), "developer")
r = c.post(f"/api/admin/books/deleted/{did}/restore")
ok(r.status_code == 200 and r.get_json()["book_id"] == bid, "restore ok")
ok(counts(bid) == before, f"every row restored ({counts(bid)})")
ok(dict(con.execute("select * from books where book_id=?", (bid,)).fetchone()) == before_book, "book row identical after restore")
ok(c.get("/api/admin/books/deleted").get_json() == [], "restored item leaves «المحذوفة»")
ok(c.post(f"/api/admin/books/deleted/{did}/restore").status_code == 409, "second restore refused")
ok(c.post("/api/admin/books/deleted/999999/restore").status_code == 404, "unknown deletion 404")
plan = c.post("/api/admin/import/plan", json={"upload_id": up["upload_id"], "mapping": up["suggested"]}).get_json()
ok(not any("محذوف يدويًا" in e["reason"] for e in plan["excluded"]), "after restore the import matches it again")
ok(con.execute("select count(*) from audit_log where action='book.restore'").fetchone()[0] == 1, "restore audited")

html = open(os.path.join(os.getcwd(), "..", "admin", "index.html"), encoding="utf-8").read() if os.path.exists(os.path.join(os.getcwd(), "..", "admin", "index.html")) else ""
ok("ليس كتابًا" in html and "نعم، احذفه" in html and 'id="libTabDeleted"' in html and "/restore" in html,
   "admin: «ليس كتابًا» button, confirmation, «المحذوفة» tab with restore")
print(f"TOTAL PASS={P} FAIL={F}")
