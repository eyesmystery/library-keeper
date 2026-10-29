"""[BKREV] Ibrahim 2026-10-29:
1. «لما نراجع كتاب في المكتبة ونعدل في البيانات كيف خلاص نأكد انها تم مراجعتها عشان نصفي اللي تحتاج مراجعة» —
   «تمت مراجعة الكتاب» in the book card (and «إلغاء المراجعة»).
2. «لما اعدل بيانات كتاب ابغاها تنحط في قائمة مستقلة يعيد البحث عنها بناءا على البيانات المضافة. مثلا لو اضفت الغلاف او الدار»
   — «بانتظار إعادة البحث».
3. «لما اعدل موقع الكتاب واحطه في الرف … احط الحروف بس لكن ابغاك ترجع تحط اسم القسم» — «R02» → «روايات · الرف R02»,
   for new edits and for places saved before."""
import os, sys, sqlite3, json, importlib.util
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, auth, library_sheet as L, research_queue as RQ, inbox_export
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# ---- 3. the place name put back
cases = {"R02": "روايات · الرف R02", "q4": "روايات · الرف Q04", " r02 ": "روايات · الرف R02", "R02 / R05": "روايات · الرف R02 / R05",
         "الرف R02": "روايات · الرف R02", "رف A3": "تاريخ · الرف A03", "kids1": "أطفال ويافعين · الرف Kids01", "XD02": "تطوير الذات وعلم النفس · الرف XD02",
         "روايات · الرف Q04": "روايات · الرف Q04", "رف ٣ – الطابق الأرضي": "رف ٣ – الطابق الأرضي", "عند الكاشير": "عند الكاشير", "": ""}
for given, want in cases.items():
    got = L.normalize_location(given)
    ok(got == want, f"place «{given}» → «{got}»")

con = sqlite3.connect(models.DB_PATH)
ids = [r[0] for r in con.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 4")]
b1, b2, b3, b4 = ids
d = login(A.app.test_client(), "developer")
s = login(A.app.test_client(), "staff")
H = lambda c: {"X-CSRF-Token": c.csrf}

r = s.patch(f"/api/books/{b1}", json={"shelf_location": "R02"}, headers=H(s))
ok(r.status_code == 200 and con.execute("select shelf_location from books where book_id=?", (b1,)).fetchone()[0] == "روايات · الرف R02",
   "staff save «R02» → «روايات · الرف R02»")
ok(RQ.for_book(b1) is None, "a shelf change alone does not queue the book for research")

# saved before this version: fixed at start-up, written once in the audit
con.execute("update books set shelf_location='Q4' where book_id=?", (b2,)); con.commit()
c2 = models.get_connection(); n = L.normalize_saved_locations(c2); c2.close()
ok(n >= 1 and con.execute("select shelf_location from books where book_id=?", (b2,)).fetchone()[0] == "روايات · الرف Q04",
   f"start-up pass fixes a place saved as «Q4» ({n})")
last = con.execute("select action, details from audit_log where action='shelf.normalized' order by log_id desc limit 1").fetchone()
ok(last and [b2, "Q4", "روايات · الرف Q04"] in json.loads(last[1])["books"], "…and writes before → after in the audit")
c2 = models.get_connection(); ok(L.normalize_saved_locations(c2) == 0, "the pass changes nothing the second time"); c2.close()
html = open(os.path.join(os.path.dirname(A.__file__), "..", "admin", "index.html"), encoding="utf-8").read()
ok("اكتب رمز الرف فقط (R02)، واسم القسم ينكتب تلقائيًا" in html and "await reopenCard(id, 'invState', 'تم الحفظ')" in html,
   "the place field says so and shows the saved place after «حفظ»")

# ---- 2. «بانتظار إعادة البحث»
r = s.patch(f"/api/books/{b3}", json={"publisher": "دار تجريبية للاختبار"}, headers=H(s))
q = RQ.for_book(b3)
ok(r.status_code == 200 and q and q["changed"] == ["publisher"] and q["changed_ar"] == ["الدار"], f"a publisher change queues the book ({q})")
ok(q["created_by"] == "اختبار staff", "with the name of who changed it")
import covers
import random, struct, zlib
def _png(w, h):   # as tests/test_covers.py
    raw = b"".join(b"\x00" + bytes(random.getrandbits(8) for _ in range(w * 3)) for _ in range(h))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")
png = _png(120, 180)
r = s.post(f"/api/admin/books/{b3}/cover", data={"file": (__import__("io").BytesIO(png), "c.png")}, headers=H(s),
           content_type="multipart/form-data")
q = RQ.for_book(b3)
ok(r.status_code == 200 and q["changed"] == ["publisher", "cover"], f"a cover upload joins the same open row ({r.status_code}, {q and q['changed']})")
ok(con.execute("select count(*) from research_queue where book_id=? and status='open'", (b3,)).fetchone()[0] == 1, "one open row per book")
cat = {x["book_id"]: x for x in s.get("/api/admin/catalog").get_json()}
ok(cat[b3]["requeue"] is True and cat[b1]["requeue"] is False, "the catalog marks it (filter «بانتظار إعادة البحث»)")
card = s.get(f"/api/admin/books/{b3}").get_json()
ok(card["research_queue"] and card["research_queue"]["changed_ar"] == ["الدار", "الغلاف"], "the book card shows what changed")
text, counts = inbox_export.build("open")
ok("## 13. كتب تنتظر إعادة البحث بعد تعديل بياناتها (1)" in text and "بعد تعديل: الدار، الغلاف" in text and counts["research"] == 1,
   "the downloaded inbox file lists it for Claude")
r = s.post(f"/api/admin/books/{b3}/research-done", json={}, headers=H(s))
ok(r.status_code == 403, "staff cannot close it: «تمت إعادة البحث» is the developer's")
r = d.post(f"/api/admin/books/{b3}/research-done", json={"note": "نبذة وبطاقة جديدة"}, headers=H(d))
ok(r.status_code == 200 and r.get_json()["closed"] == 1 and RQ.for_book(b3) is None, "the developer closes it after the research")
# at the library: closed by the next update (inbox_resolutions.json, kind «research»)
RQ.add(b4, ["title"], "موظف")
qid = RQ.for_book(b4)["queue_id"]
spec = importlib.util.spec_from_file_location("apply_update", os.path.join(os.getcwd(), "..", "scripts", "apply_update.py"))
if not os.path.exists(spec.origin):
    spec = importlib.util.spec_from_file_location("apply_update", os.path.join(os.path.dirname(os.getcwd()), "scripts", "apply_update.py"))
U = importlib.util.module_from_spec(spec); spec.loader.exec_module(U)
c3 = sqlite3.connect(models.DB_PATH)
ok(U.inbox_matches(c3, {"kind": "research", "id": qid, "book_id": b4}) == [qid], "an update item «research» + id + book_id finds the row")
ok(U.inbox_matches(c3, {"kind": "research", "id": qid, "book_id": b3}) == [], "…and not with another book_id")
c3.close()
mr = open(os.path.join(os.path.dirname(os.getcwd()), "scripts", "make_release.py"), encoding="utf-8").read() \
    if os.path.exists(os.path.join(os.path.dirname(os.getcwd()), "scripts", "make_release.py")) else ""
ok('"research_queue"]' in mr, "packages leave the list empty (the library's own)")
ok(s.patch(f"/api/books/{b1}", json={"price": 12}, headers=H(s)).status_code == 200 and RQ.for_book(b1) is None,
   "a price change does not queue it")

# ---- 1. «تمت مراجعة الكتاب»
bid = con.execute("""select b.book_id from books b join attribute_evidence e using (book_id)
                     where coalesce(e.rejected,0)=0 and e.source_site<>'derived' and e.attribute_value<>'unknown'
                       and coalesce(e.human_verified,0)=0 group by b.book_id limit 1""").fetchone()[0]
con.execute("update books set research_status='needs_confirmation', research_note_ar=NULL where book_id=?", (bid,)); con.commit()
cat = {x["book_id"]: x for x in s.get("/api/admin/catalog").get_json()}
ok(cat[bid]["reviewed_at"] is None and cat[bid]["research_status"] == "needs_confirmation", "before: in «تحتاج مراجعة»")
r = s.post(f"/api/admin/books/{bid}/reviewed", json={}, headers=H(s))
ok(r.status_code == 400, "needs the confirmation")
# a staff review: identity confirmed, the reader-impression values (developer-only in the card) untouched
r = s.post(f"/api/admin/books/{bid}/reviewed", json={"confirm": True}, headers=H(s))
js = r.get_json()
ok(r.status_code == 200 and js["confirmed"] == 0 and js["identity_confirmed"], f"staff review: identity yes, card values no ({js})")
ok(con.execute("select research_status from books where book_id=?", (bid,)).fetchone()[0] == "matched", "…identity «تم التعرف»")
cat = {x["book_id"]: x for x in s.get("/api/admin/catalog").get_json()}
ok(cat[bid]["reviewed_by"] == "اختبار staff", "…and the book is marked reviewed by them")
ok(s.delete(f"/api/admin/books/{bid}/reviewed", headers=H(s)).status_code == 200
   and con.execute("select research_status from books where book_id=?", (bid,)).fetchone()[0] == "needs_confirmation", "…undone")
# a developer's review confirms the card's values too
s = d
r = s.post(f"/api/admin/books/{bid}/reviewed", json={"confirm": True}, headers=H(s))
j = r.get_json()
ok(r.status_code == 200 and j["confirmed"] > 0 and j["identity_confirmed"], f"developer marks it reviewed ({j})")
left = con.execute("""select count(*) from attribute_evidence where book_id=? and coalesce(rejected,0)=0 and source_site<>'derived'
                      and attribute_value<>'unknown' and coalesce(human_verified,0)=0""", (bid,)).fetchone()[0]
ok(left == 0, "every value on its card is confirmed")
row = con.execute("select research_status, research_note_ar from books where book_id=?", (bid,)).fetchone()
ok(row[0] == "matched" and "اختبار developer" in (row[1] or ""), f"identity confirmed in their name ({row[1]})")
ok(con.execute("select verified_by from attribute_evidence where book_id=? and human_verified=1 limit 1", (bid,)).fetchone()[0] == "اختبار developer",
   "the values carry the reviewer's name")
st = con.execute("select overall_status from book_profiles where book_id=?", (bid,)).fetchone()[0]
ok(st == "approved", f"card «معتمد» ({st})")
cat = {x["book_id"]: x for x in s.get("/api/admin/catalog").get_json()}
ok(cat[bid]["reviewed_at"] and cat[bid]["reviewed_by"] == "اختبار developer" and cat[bid]["counts"]["conflict"] == 0,
   "the catalog shows it reviewed (filter «تمت مراجعته»), out of «تحتاج مراجعة»")
card = s.get(f"/api/admin/books/{bid}").get_json()
ok(card["reviewed"] and card["reviewed"]["by"] == "اختبار developer", "the card says who reviewed it")
r = s.delete(f"/api/admin/books/{bid}/reviewed", headers=H(s))
ok(r.status_code == 200, "«إلغاء المراجعة»")
left2 = con.execute("""select count(*) from attribute_evidence where book_id=? and coalesce(rejected,0)=0 and source_site<>'derived'
                       and attribute_value<>'unknown' and coalesce(human_verified,0)=0""", (bid,)).fetchone()[0]
ok(left2 == j["confirmed"] and con.execute("select research_status from books where book_id=?", (bid,)).fetchone()[0] == "needs_confirmation",
   "undo puts back exactly what the review changed")
cat = {x["book_id"]: x for x in s.get("/api/admin/catalog").get_json()}
ok(cat[bid]["reviewed_at"] is None, "and it is back in «تحتاج مراجعة»")
ok(s.delete(f"/api/admin/books/{bid}/reviewed", headers=H(s)).status_code == 400, "undo twice is refused")
ok("function reviewBlock(d)" in html and "${reviewBlock(d)}" in html and "✓ تمت مراجعة الكتاب" in html
   and "['reviewed', 'تمت مراجعته'" in html and "['requeue', 'بانتظار إعادة البحث'" in html,
   "the card block and the filters are on the page")
ok("function reviewChecks(d)" in html and all(x in html for x in ("item('الدار'", "item('عدد الصفحات'", "item('ISBN'", "item('الغلاف'", "item('الرف'"))
   and "<span class=\"warn\">ناقص</span>" in html, "the block lists what to check, each with its value or «ناقص»")
ok('data-chip="toreview"' in html and 'data-chip="requeue"' in html and "lib.chips.clear(); if (!only) lib.chips.add(k);" in html,
   "a tap on the count «تحتاج مراجعة» / «بانتظار إعادة البحث» shows that list")
con.close()
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
