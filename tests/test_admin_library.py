"""«المكتبة» admin tab (2026-09-30): catalog fields for grouping/sorting, authors list/detail/edit/merge."""
import os, sys, json, sqlite3, re
sys.path.insert(0, os.getcwd())
import models, app as A, authors
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F; print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
b1, b2, b3 = [r[0] for r in con.execute("select book_id from books order by book_id limit 3")]
con.execute("update books set author='جيلبير سينويه', translator='مترجم تجربة' where book_id=?", (b1,))
con.execute("update books set author='جيلبرت سينويه' where book_id=?", (b2,))
con.execute("update books set author='كاتبة الاختبار' where book_id=?", (b3,))
con.commit()
before_books = [dict(r) for r in con.execute("select * from books order by book_id")]
authors.sync()
c = login(A.app.test_client())

cat = c.get("/api/admin/catalog").get_json()
need = {"publisher", "language", "original_language", "research_status", "researched_at", "origin", "translated"}
ok(cat and need <= set(cat[0]), "catalog rows carry the grouping/sorting fields")
ok(next(b for b in cat if b["book_id"] == b1)["translated"], "translated flag follows the translator field")

lst = c.get("/api/admin/authors").get_json()
by = {a["name"]: a for a in lst}
ok(len(lst) == con.execute("select count(*) from authors").fetchone()[0], "authors list has every person")
g1, g2 = by.get("جيلبير سينويه"), by.get("جيلبرت سينويه")
ok(g1 and g2 and any(d["id"] == g2["id"] for d in g1["duplicates"]), "probable duplicate spellings are flagged on both authors")
ok(by["مترجم تجربة"]["roles"] == {"translator": 1}, "roles are counted per person")
ok(all({"books", "in_stock", "researched", "style", "aliases"} <= set(a) for a in lst), "list rows carry counts, style and aliases")
tr_row = by["مترجم تجربة"]
ok(set(tr_row["role_stock"]) == {"translator"} and set(tr_row["role_researched"]) == {"translator"}
   and tr_row["role_stock"]["translator"] == tr_row["in_stock"] and tr_row["role_researched"]["translator"] == tr_row["researched"],
   "per-role stock and researched counts (translators section)")
ok(all(set(a["role_stock"]) == set(a["roles"]) and all(a["role_stock"][r] <= a["roles"][r] for r in a["roles"]) for a in lst),
   "role_stock covers every role and never exceeds the role's book count")

d = c.get(f"/api/admin/authors/{g1['id']}").get_json()
ok([b["book_id"] for b in d["books_list"]] == [b1] and d["books_list"][0]["role"] == "author", "detail lists the author's books with roles")
ok(c.get("/api/admin/authors/999999").status_code == 404, "unknown author 404")

# edit: rename keeps the old spelling as an alias, so the books stay linked after a resync
r = c.post(f"/api/admin/authors/{by['كاتبة الاختبار']['id']}", json={"name": "كاتبة الاختبار الكاملة", "latin": "Test Writer", "origin": "مصر", "verified": True})
ok(r.status_code == 200, "author edit saved")
row = con.execute("select * from authors where author_id=?", (by["كاتبة الاختبار"]["id"],)).fetchone()
ok(row["name_ar"] == "كاتبة الاختبار الكاملة" and "كاتبة الاختبار" in json.loads(row["aliases"]) and row["staff_verified"] == 1
   and row["name_latin"] == "Test Writer" and row["origin"] == "مصر", "rename stores the old spelling as an alias")
authors.sync()
ok(con.execute("select count(*) from authors where name_ar='كاتبة الاختبار'").fetchone()[0] == 0 and
   con.execute("select count(*) from book_authors where author_id=? and book_id=?", (row["author_id"], b3)).fetchone()[0] == 1,
   "resync after a rename creates no duplicate and keeps the link")
r = c.post(f"/api/admin/authors/{g1['id']}", json={"aliases": ["جيلبرت سينويه"]})
ok(r.status_code == 400 and "الدمج" in r.get_json()["error"], "an alias used by another author is refused (merge instead)")
ok(c.post(f"/api/admin/authors/{g1['id']}", json={"name": "  "}).status_code == 400, "empty Arabic name refused")

# merge
r = c.post("/api/admin/authors/merge", json={"keep": g1["id"], "drop": g2["id"]})
ok(r.status_code == 200 and r.get_json()["links_moved"] == 1, "merge moves the duplicate's links")
ok(con.execute("select count(*) from authors where author_id=?", (g2["id"],)).fetchone()[0] == 0, "merged author row removed")
ok(sorted(x[0] for x in con.execute("select book_id from book_authors where author_id=?", (g1["id"],))) == sorted([b1, b2]), "kept author now has both books")
authors.sync()
ok(con.execute("select count(*) from authors where name_ar='جيلبرت سينويه'").fetchone()[0] == 0, "resync after merge does not recreate the removed spelling")
ok(c.post("/api/admin/authors/merge", json={"keep": g1["id"], "drop": g1["id"]}).status_code == 400, "merging an author into itself refused")
ok(c.post("/api/admin/authors/merge", json={"keep": g1["id"], "drop": "x"}).status_code == 400, "invalid merge request refused")
ok([dict(r) for r in con.execute("select * from books order by book_id")] == before_books, "author edits and merges never change books")
audit = [r[0] for r in con.execute("select action from audit_log where action like 'author.%'")]
ok("author.update" in audit and "author.merge" in audit, "author edits and merges are audited")

# staff may use the authors screen
staff = login(A.app.test_client(), role="staff")
ok(staff.get("/api/admin/authors").status_code == 200 and staff.get(f"/api/admin/authors/{g1['id']}").status_code == 200,
   "staff can open the authors list and pages")


# suggestions are spelling variants only (Ibrahim 2026-09-30): a different or extra name word is another person
con.execute("""insert into authors (name_ar, aliases) values ('محمد نور الدين','[]'),('محمد ناصر الدين','[]'),
  ('أحمد خالد مصطفى','[]'),('أحمد خالد توفيق','[]'),('إبراهيم عيسى','[]'),('إبراهيم أحمد عيسى','[]'),
  ('أحمد حسن','[]'),('أحمد حسين','[]'),('جون كوينغ','[]'),('جو كوينن','[]'),
  ('ماريو بارغاس يوسا','[]'),('ماريو فارغاس يوسا','[]'),('غارون تسوتشيا','[]'),('جارون تسوتشيا','[]'),
  ('روديارد كبلينغ','[]'),('روديارد كبلنغ','[]'),('إييتشي شيميزو','[]'),('إيتشي شيميزو','[]')""")
con.commit()
pairs = {frozenset((a, b)) for _, a, _, b in authors.probable_duplicates()}
for a, b in [("محمد نور الدين", "محمد ناصر الدين"), ("أحمد خالد مصطفى", "أحمد خالد توفيق"), ("إبراهيم عيسى", "إبراهيم أحمد عيسى"),
             ("أحمد حسن", "أحمد حسين"), ("جون كوينغ", "جو كوينن")]:
    ok(frozenset((a, b)) not in pairs, f"different people not suggested: {a} / {b}")
for a, b in [("ماريو بارغاس يوسا", "ماريو فارغاس يوسا"), ("غارون تسوتشيا", "جارون تسوتشيا"),
             ("روديارد كبلينغ", "روديارد كبلنغ"), ("إييتشي شيميزو", "إيتشي شيميزو")]:
    ok(frozenset((a, b)) in pairs, f"spelling variant suggested: {a} / {b}")

html = open("../admin/index.html", encoding="utf-8").read()
ok('aria-controls="booksView">المكتبة</button>' in html, "main tab is named «المكتبة»")
ok('role="tablist" aria-label="أقسام المكتبة"' in html and 'aria-controls="libBooks"' in html and 'aria-controls="libAuthors"' in html,
   "library has books and authors sub-tabs")
ok(all(f'data-view="{v}"' in html for v in ("cards", "list", "shelf")), "books can be shown as cards, list or shelf")
ok(all(f'<option value="{v}">' in html for v in ("genre", "publisher", "research", "stock", "lang")), "books can be grouped")
ok('id="libTabTranslators"' in html and "['translators', 'libTabTranslators', 'libAuthors']" in html and 'id="libNTranslators"' in html,
   "library has a translators sub-tab next to books and authors")
ok('id="libTabOrigin"' in html and "['origin', 'libTabOrigin', 'libOrigin']" in html and 'id="originList"' in html,
   "library has the «أصل الكتاب» staff list")
import re as _re
_codes = _re.findall(r"\['([a-z]+)', '", html[html.index("const ORIGIN_LANGS"):html.index("const originPending")])
ok(_codes and all(x in A.LANG_NAMES for x in _codes) and "other" in _codes and "ar" not in _codes, "origin list offers only language codes the server accepts")
_st = login(A.app.test_client(), role="staff")
_b = con.execute("select book_id from books order by book_id desc limit 1").fetchone()[0]
ok(_st.patch(f"/api/books/{_b}", json={"original_language": "ar"}).status_code == 200
   and con.execute("select original_language from books where book_id=?", (_b,)).fetchone()[0] == "ar", "staff can mark a book as an Arabic original")
ok(_st.patch(f"/api/books/{_b}", json={"original_language": "other"}).status_code == 200
   and A.origin_label({"language": "ar", "original_language": "other", "translator": "فلان"}) == "مترجم، ترجمة فلان"
   and A.origin_label({"language": "ar", "original_language": "other"}, "en") == "Translated", "translated with an unknown language is shown as «مترجم» only")
ok(_st.patch(f"/api/books/{_b}", json={"original_language": ""}).status_code == 200
   and con.execute("select original_language from books where book_id=?", (_b,)).fetchone()[0] is None, "the choice can be undone (back to unknown)")
ok('id="bookChips"' in html and 'id="authorChips"' in html, "chip filters for books and authors")
ok("/api/admin/authors/merge" in html, "authors sheet can merge a duplicate")
print(f"TOTAL PASS={P} FAIL={F}")
