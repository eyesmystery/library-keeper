"""The library's own book sheet (track SHELF, Ibrahim 2026-10-11): shelf + place, empty publisher /
genre filled, differing publisher only listed, keywords for search and linking, repeatable import."""
import glob, io, json, os, sqlite3, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models
models.init_db()
import app as A, library_sheet as L, relations, topics, sections
from auth_helper import login
from xlsx_helper import make_xlsx
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# ---- units
ok(L.shelf_code("R04") == "R04" and L.shelf_code(" r4 ") == "R04" and L.shelf_code("CO3") == "C03", "shelf codes normalized (case, padding, letter O)")
ok(L.shelf_code("kids01") == "Kids01" and L.shelf_code(".XC01") == "XC01" and L.shelf_code("٫C05") == "C05", "Kids and stray marks")
ok(L.shelf_code("319") is None and L.shelf_code("4304.0") is None and L.shelf_code("") is None and L.shelf_code("ABCD1") is None, "a bare number is not a shelf")
ok(L.location_text(["R04"]) == "روايات · الرف R04" and L.location_text(["Kids02"]) == "أطفال ويافعين · الرف Kids02", "place added to the code")
ok(L.location_text(["K02", "K05", "K02"]) == "روايات · الرف K02 / K05" and L.location_text(["Z09"]) == "الرف Z09" and L.location_text([]) is None, "several shelves / unknown letter / none")
ok(L.keywords_of("مراهقة، برلين، جريمة، صداقة، هجرة") == ["مراهقة", "برلين", "جريمة", "صداقة", "هجرة"], "keywords split on the Arabic comma")
ok(L.keywords_of("دمشق - حب - حرب") == ["دمشق", "حب", "حرب"] and L.keywords_of("ما بعد-الحداثة") == ["ما بعد-الحداثة"], "keywords split on spaced dashes only")
ok(L.keywords_of("أ، ، أ, ب") == ["أ", "ب"] and len(L.keywords_of("،".join(str(i) + "x" for i in range(20)))) == L.MAX_KEYWORDS, "keywords: empty and repeated dropped, capped")
cases = {"رواية (أدب عالمي)": "رواية", "6 -9 \nYears": "أطفال", "رواية يافعين": "يافعين", "فلسفة": "فكر", "Fiction": "رواية",
         "Non-Fiction / Memoir": "سيرة ذاتية", "مجموعة قصصية": "قصص قصيرة", "قصة مصورة (كوميكس)": "قصص مصورة", "سيرة روائية": "رواية",
         "الأعمال الكاملة": None, "دراسات مدينة": None, "علوم سياسية": "فكر", "تاريخ": "تاريخ", "شعر": "شعر", "أدب": None, "": None}
bad = {c: (L.genre_from_category(c), g) for c, g in cases.items() if L.genre_from_category(c) != g}
ok(not bad, f"library category -> our genre {bad}")

cat = L.Catalog([{"book_id": 1, "title": "أصل الأنواع", "author": "أحمد عبد اللطيف", "publisher": "منشورات حياة"}])
ok(cat.match("أصل الأنواع", "دار التنوير", "تشارلز داروين")[0] == "none" and cat.match("أصل الأنواع", "", "")[0] == "t"
   and cat.match("أصل الأنواع", "دار التنوير", "أحمد عبد اللطيف")[0] == "ta", "same title, other publisher and author = another book")

W = lambda t: L._content(t)
items = [(2, W("الحرب والسلام تولستوي روسيا"), W("روسيا نابليون حرب")), (3, W("موبي ديك ملفيل الحوت"), W("الحوت البحر قبطان")),
         (10, W("البؤساء هوغو باريس"), W("الحوت قبطان البحر ملفيل")), (11, W("الغريب كامو الجزائر"), W("هوغو باريس البؤساء فقر")),
         (12, W("الطاعون كامو وهران"), W("كامو الجزائر مورسو الغريب")), (13, W("كتاب بلا نص"), W("كامو وهران الطاعون وباء")),
         (14, W("عنوان آخر"), W("شيء")), (40, W("المسخ كافكا"), W("كافكا حشرة المسخ"))]
sh = L.shifted_rows(items)
ok({10, 11, 12, 13}.issubset(sh) and 2 not in sh and 3 not in sh and 40 not in sh, f"shifted stretch found {sorted(sh)}")

# ---- a sheet like the library's (headers mostly «Column N»)
con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
novels = [dict(r) for r in con.execute("select * from books where genre='رواية' and active=1 and quantity>0 and publisher is not null and publisher<>'' order by book_id limit 6")]
nopub = [dict(r) for r in con.execute("select * from books where (publisher is null or publisher='') and active=1 and quantity>0 order by book_id limit 2")]
hist = dict(con.execute("select * from books where genre='تاريخ' order by book_id limit 1").fetchone())
con.execute("update books set genre=NULL where book_id=?", (hist["book_id"],))
con.execute("update book_profiles set format='other' where book_id=?", (hist["book_id"],))
con.commit()
H = ["Column 1", "Column 2", "نظام الزمن", "Column 4", "المؤلف", "Column 6", "كلمات مفتاحية", "وصف", "Column 9"]
rows = [H]
for i, b in enumerate(novels):
    rows.append([f"K0{i + 1}", b["publisher"], b["title"], "", b["author"], "رواية", "صداقة، سفر، ذاكرة، حب" if i < 2 else "مدينة، عائلة", "وصف", 3])
rows.append(["A02", "دار المكتبة", nopub[0]["title"], "", nopub[0]["author"], "رواية", "منفى، وطن", "وصف", 3])
rows.append(["319", "", nopub[1]["title"], "", nopub[1]["author"], "رواية", "", "", 2])
rows.append(["a05", "", hist["title"], "", hist["author"], "تاريخ", "حضارة، دولة", "", 2])
rows.append(["Q01", "دار مختلفة تمامًا", novels[0]["title"], "", novels[0]["author"], "رواية", "", "", 1])
lng = max(novels, key=lambda b: len(b["title"]))
rows.append(["R03", lng["publisher"], lng["title"] + " طبعة جديدة مزيدة", "", lng["author"], "رواية", "", "", 1])
rows.append(["M01", "دار غير موجودة", "كتاب لا يوجد عندنا أبدًا", "", "أحد ما", "رواية", "", "", 1])
data = make_xlsx(rows)

anon, m = A.app.test_client(), login(A.app.test_client())
staff = login(A.app.test_client(), "staff")
ok(anon.post("/api/admin/library-sheet/upload").status_code == 401, "sign-in required")
r = staff.post("/api/admin/library-sheet/upload", data={"file": (io.BytesIO(data), "s.xlsx")}, content_type="multipart/form-data")
ok(r.status_code == 403, "manager only")
r = m.post("/api/admin/library-sheet/upload", data={"file": (io.BytesIO(data), "sheet.xlsx")}, content_type="multipart/form-data")
p = r.get_json()
ok(r.status_code == 200 and p.get("upload_id"), f"upload + plan {r.status_code}")
ok(p["columns"].get("shelf") == "Column 1" and p["columns"].get("title") == "نظام الزمن" and p["columns"].get("publisher") == "Column 2"
   and p["columns"].get("keywords") == "كلمات مفتاحية" and p["columns"].get("category") == "Column 6", f"columns found by content {p['columns']}")
before = sqlite3.connect(models.DB_PATH).execute("select count(*) from books where shelf_location is not null and shelf_location<>''").fetchone()[0]
ok(before == 0, "preview writes nothing")
upd = {u["book_id"]: u["changes"] for u in p["updated"]}
ok(upd[novels[1]["book_id"]]["shelf_location"][1] == "روايات · الرف K02", "shelf with its place")
ok(upd[novels[0]["book_id"]]["shelf_location"][1] == "روايات · الرف K01 / Q01", "a book on two rows keeps both shelves")
ok(upd[nopub[0]["book_id"]]["publisher"] == [nopub[0]["publisher"], "دار المكتبة"], "empty publisher filled")
ok(any(d["book_id"] == novels[0]["book_id"] and d["sheet"] == "دار مختلفة تمامًا" for d in p["publisher_differs"])
   and "publisher" not in upd[novels[0]["book_id"]], "a different publisher is listed, not changed")
ok(upd[hist["book_id"]]["genre"][1] == "تاريخ" and upd[hist["book_id"]]["shelf_location"][1] == "تاريخ · الرف A05", "empty genre from the library category")
ok("shelf_location" not in upd.get(nopub[1]["book_id"], {}) and any(n["value"] == "319" for n in p["numeric_codes"]), "a number is not written as a shelf")
ok(upd[novels[1]["book_id"]]["keywords"][1] == ["صداقة", "سفر", "ذاكرة", "حب"], "keywords")
ok(any(x["book_id"] == lng["book_id"] and x["kind"] == "starts" for x in p["probable"]), "a longer title of the same publisher = probable, listed")
ok(any(u["title"] == "كتاب لا يوجد عندنا أبدًا" for u in p["unmatched"]), "unknown book listed")

r = m.post("/api/admin/library-sheet/apply", json={"upload_id": p["upload_id"]})
res = r.get_json()
ok(r.status_code == 200 and res["backup"].endswith("-sheet.db"), f"apply {r.status_code} {res}")
ok(os.path.exists(os.path.join(os.path.dirname(models.DB_PATH), "..", "backups", res["backup"])), "backup made before writing")
con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
b1 = dict(con.execute("select * from books where book_id=?", (novels[1]["book_id"],)).fetchone())
ok(b1["shelf_location"] == "روايات · الرف K02" and json.loads(b1["keywords"]) == ["صداقة", "سفر", "ذاكرة", "حب"], "written")
ok(con.execute("select publisher from books where book_id=?", (novels[0]["book_id"],)).fetchone()[0] == novels[0]["publisher"], "differing publisher untouched")
ok(con.execute("select genre from books where book_id=?", (hist["book_id"],)).fetchone()[0] == "تاريخ"
   and con.execute("select format from book_profiles where book_id=?", (hist["book_id"],)).fetchone()[0] == "history", "genre + card format")
run = dict(con.execute("select * from import_runs order by run_id desc limit 1").fetchone())
ok(run["source"] == "library_sheet" and json.loads(run["details"])["updated"], "import recorded (inbox file)")
ok(m.post("/api/admin/library-sheet/apply", json={"upload_id": p["upload_id"]}).status_code == 400, "an upload is applied once")
pub = {x["book_id"]: x for x in anon.get("/api/books").get_json()}
ok(pub[novels[1]["book_id"]]["location"] == "روايات · الرف K02" and pub[novels[1]["book_id"]]["location_kind"] == "shelf", "visitor sees the location")

# repeated import: nothing left to change
r = m.post("/api/admin/library-sheet/upload", data={"file": (io.BytesIO(data), "sheet.xlsx")}, content_type="multipart/form-data")
ok(r.get_json()["updated_total"] == 0, "same sheet again: no change")

# keywords in the subject search and in the links between books
idx, counts, n = topics.index({b["book_id"]: b for b in [dict(x) for x in models.list_books(only_active=False)]})
ok("منفي" in idx[nopub[0]["book_id"]][1] or "منفى" in idx[nopub[0]["book_id"]][1] or any("منف" in w for w in idx[nopub[0]["book_id"]][1]), "keywords are subject words")
relations.rebuild()
rel = relations.for_book(novels[0]["book_id"])
ok(any(x["relation_type"] == "same_topic" and x["target_book_id"] == novels[1]["book_id"] and "يتقاطعان في" in x["reason_ar"] for x in rel),
   "two novels sharing keywords are linked «موضوع قريب»")
ok(relations.TYPES_AR["same_topic"] == "موضوع قريب", "relation label")
ok(not any(x["relation_type"] == "same_topic" and x["target_book_id"] == hist["book_id"] for x in rel), "no link across genres")

# the command-line form on another database
tmp = os.path.join(os.path.dirname(models.DB_PATH), "sheet_test.xlsx")
open(tmp, "wb").write(data)
import subprocess
out = subprocess.run([sys.executable, os.path.join("..", "scripts", "library_sheet.py"),
                      tmp, "--db", models.DB_PATH, "--plan", tmp + ".json"], capture_output=True, text=True)
ok(out.returncode == 0 and "dry run" in out.stdout and os.path.exists(tmp + ".json"), f"script dry run {out.stderr[-300:]}")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
