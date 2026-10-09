import os, sys, io, json, shutil, sqlite3
# Run through tests/run_all.py: cwd is a throwaway copy of backend/ with its own data/.
sys.path.insert(0, os.getcwd())
import models; models.init_db()
import rewaa_import as R, app as A
from auth_helper import login
from xlsx_helper import make_xlsx
c = login(A.app.test_client()); P = F = 0
def ok(cond, msg):
    global P, F; print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)
con = lambda: sqlite3.connect(models.DB_PATH)
n0 = con().execute("select count(*) from books").fetchone()[0]

# --- unit: parsing helpers
ok(R.norm_code("978-9953-68-651-6") == "9789953686516" and R.norm_code("9789953686516.0") == "9789953686516", "barcode normalization")
ok(R.norm_code("9.78995E+12") == "", "scientific-notation barcode refused (precision lost)")
ok(R.parse_number("٤٥٫٥٠ ر.س") == 45.5 and R.parse_number("1,250.00") == 1250.0 and R.parse_number("12,5") == 12.5, "Arabic digits / currency / separators")

# --- CSV export with Arabic headers + ignored columns
csv_text = ("اسم المنتج,رمز المنتج,الباركود,نوع الضريبة,فئة المنتج,سعر البيع,الكمية المتوفرة,المؤلف\n"
            "موسم الهجرة إلى الشمال,RW-001,978-9953-68-651-6,ضريبة القيمة المضافة 15%,كتب,٦٥,٧,الطيب صالح\n"
            "كتاب جديد تمامًا,RW-900,9786030000001,15%,كتب,49.5,3,مؤلف جديد\n"
            "الحرافيش,RW-010,,15%,كتب,70,2,نجيب محفوظ\n"
            ",RW-999,,15%,كتب,10,1,\n"
            "كتاب بسعر غريب,RW-777,,15%,كتب,مجاني؟,1,س\n")
up = c.post("/api/admin/import/upload", data={"file": (io.BytesIO(csv_text.encode("utf-8-sig")), "rewaa.csv")},
            content_type="multipart/form-data").get_json()
ok(up["row_count"] == 5 and len(up["headers"]) == 8, "CSV parsed (UTF-8 BOM)")
sug = {k: up["headers"][v] for k, v in up["suggested"].items()}
print("   suggested:", sug)
ok(sug.get("title") == "اسم المنتج" and sug.get("barcode") == "الباركود" and sug.get("sku") == "رمز المنتج"
   and sug.get("price") == "سعر البيع" and sug.get("quantity") == "الكمية المتوفرة" and sug.get("author") == "المؤلف", "auto-mapping from Arabic headers")
ok("نوع الضريبة" not in sug.values() and "فئة المنتج" not in sug.values(), "tax type / product category never mapped")
plan = c.post("/api/admin/import/plan", json={"upload_id": up["upload_id"], "mapping": up["suggested"]}).get_json()
ok(len(plan["new"]) == 1 and plan["new"][0]["title"] == "كتاب جديد تمامًا", "new book detected")
upd = {u["title"]: u for u in plan["updated"]}
ok("موسم الهجرة إلى الشمال" in upd and upd["موسم الهجرة إلى الشمال"]["matched_by"] == "الاسم"
   and upd["موسم الهجرة إلى الشمال"]["changes"]["quantity"][1] == 7 and upd["موسم الهجرة إلى الشمال"]["changes"]["barcode"][1] == "9789953686516", "existing seed book matched by title; qty + barcode change planned")
ok(len(plan["invalid"]) == 2 and any("بدون اسم" in i["problems"] for i in plan["invalid"]) and any("سعر غير مفهوم" in p for i in plan["invalid"] for p in i["problems"]), "invalid rows reported (no name, bad price)")
ok(len(plan["missing"]) == n0 - 2, "books not in file listed as missing")
ok(con().execute("select count(*) from books").fetchone()[0] == n0, "plan is a dry run (nothing written)")
res = c.post("/api/admin/import/apply", json={"upload_id": up["upload_id"], "mapping": up["suggested"], "missing_mode": "report"}).get_json()
ok(res["added"] == 1 and res["updated"] == 2 and res["invalid"] == 2 and res["marked_out_of_stock"] == 0, "apply summary")
row = con().execute("select quantity, price, barcode, rewaa_sku, source, last_synced_at from books where title='موسم الهجرة إلى الشمال'").fetchone()
ok(row[0] == 7 and row[1] == 65.0 and row[2] == "9789953686516" and row[3] == "RW-001" and row[4] == "rewaa" and row[5], "existing book updated with inventory + identifiers")
nb = con().execute("select b.book_id, b.author, b.price, b.quantity, p.overall_status from books b join book_profiles p using(book_id) where b.title='كتاب جديد تمامًا'").fetchone()
ok(nb and nb[1] == "مؤلف جديد" and nb[2] == 49.5 and nb[3] == 3 and nb[4] == "pending", "new book inserted with pending card")
ev = con().execute("select count(*) from attribute_evidence where book_id=3").fetchone()[0]
ok(ev > 0, "reading attributes of updated book untouched")
ok(c.post("/api/admin/import/apply", json={"upload_id": up["upload_id"], "mapping": up["suggested"]}).status_code == 400, "upload consumed after apply")

# --- second sync: Excel file, match by barcode/SKU, idempotency, zero-missing option
buf = io.BytesIO(make_xlsx([
    ["تقرير المنتجات"],  # title row before headers
    ["Product Name", "SKU", "Barcode", "VAT", "Category", "Retail Price", "Quantity", "Location"],
    ["اسم مختلف لموسم الهجرة", "RW-001", "9789953686516", "15%", "Books", 65, 7, "A3"],
    ["كتاب جديد تمامًا", "RW-900", 9786030000001, "15%", "Books", 55, 0, "Z1"],
]))
up2 = c.post("/api/admin/import/upload", data={"file": (io.BytesIO(buf.getvalue()), "rewaa.xlsx")}, content_type="multipart/form-data").get_json()
ok(up2["row_count"] == 2 and up2["headers"][0] == "Product Name", "XLSX parsed without openpyxl (title row skipped)")
sug2 = {k: up2["headers"][v] for k, v in up2["suggested"].items()}
ok(sug2.get("barcode") == "Barcode" and sug2.get("shelf") == "Location" and "VAT" not in sug2.values() and sug2.get("publisher") == "Category", "English headers mapped, VAT ignored, Category read as publisher")
p2 = c.post("/api/admin/import/plan", json={"upload_id": up2["upload_id"], "mapping": up2["suggested"], "missing_mode": "zero"}).get_json()
u2 = {u["book_id"]: u for u in p2["updated"] + p2["unchanged"]}
ok(len(p2["new"]) == 0 and len(u2) == 2 and all(u["matched_by"] == "الباركود" for u in u2.values()), "second sync matches by barcode, no duplicates")
ok(any(u.get("note") for u in u2.values()), "title difference reported, not overwritten")
before_title = con().execute("select title from books where rewaa_sku='RW-001'").fetchone()[0]
r2 = c.post("/api/admin/import/apply", json={"upload_id": up2["upload_id"], "mapping": up2["suggested"], "missing_mode": "zero"}).get_json()
ok(con().execute("select title from books where rewaa_sku='RW-001'").fetchone()[0] == before_title, "title kept")
ok(con().execute("select quantity, price, shelf_location from books where rewaa_sku='RW-900'").fetchone() == (0, 55.0, "Z1"), "qty/price/shelf updated")
ok(r2["marked_out_of_stock"] > 0 and con().execute("select count(*) from books where quantity>0").fetchone()[0] == 1, "missing books set to quantity 0 only when chosen")
hist = c.get("/api/admin/import/history").get_json()
ok(len(hist) == 2 and hist[0]["file_name"] == "rewaa.xlsx" and hist[1]["added"] == 1, "import history recorded")
# --- each import's books and who applied it, in the inbox export (Ibrahim 2026-10-09)
run1 = con().execute("select applied_by, details from import_runs order by run_id limit 1").fetchone()
d1 = json.loads(run1[1])
ok(run1[0] and d1["new"] and d1["new"][0]["title"] == "كتاب جديد تمامًا" and d1["new"][0]["book_id"]
   and any(u["title"] == "موسم الهجرة إلى الشمال" and "quantity" in u["changes"] for u in d1["updated"]) and len(d1["invalid"]) == 2,
   "an import keeps who applied it, the new books, the changes and the refused rows")
ok("details" not in hist[0], "the short history leaves the per-book lists out")
d2 = json.loads(con().execute("select details from import_runs order by run_id desc limit 1").fetchone()[0])
ok(d2["zeroed"] and all("title" in z for z in d2["zeroed"]), "books set to quantity 0 are listed")
md = c.get("/api/admin/inbox/export").get_data(as_text=True)
sec6 = md.split("## 6. ", 1)[1] if "## 6. " in md else ""
ok(sec6.startswith("ملفات رواء المستوردة (2)") and "rewaa.csv" in sec6 and "rewaa.xlsx" in sec6
   and "كتب جديدة أُضيفت للمكتبة (1)" in sec6 and "«كتاب جديد تمامًا» — مؤلف جديد" in sec6
   and "صارت غير متوفرة لأنها ليست في ملف رواء" in sec6 and "أسطر مرفوضة (2)" in sec6, "the export lists both imports with their books")
ok("ملفات رواء المستوردة: 2" in md, "the file header counts the imports")

# --- real Rewaa export layout (simpleProducts CSV, 2026-09-28): Category = publisher, cafe/stationery rows, float SKUs
real = ("Product Name,Product SKU,BarCode,Category,Supplier,Brand,Description,Sellable,type,Enable stock management,Weighted,"
        "Tracked by batch,Tracked by serial,Purchasable,Cost,DEF Quantity,DEF Buy Price,DEF Retail Price,DEF WholeSale Price,DEF Online Price,DEF Tax Code\n"
        "يوميات القراءة,10170.0,3.24567891155E11,المركز القومي للترجمة,,,,Yes,simple,Yes,No,No,No,Yes,8.07,3,8.07,9.00,0.00,0.00,VATEX-SA-OOS\n"
        "\"نهاية الوحدة .. رواية من ألمانيا \nالرواية الحائزة\",10200.0,3.2456789113E11,دار  الحضارة,,التنوير,,Yes,simple,Yes,No,No,No,Yes,20,2,20,30.50,0,0,S\n"
        "\"BLUEST EYE, THE\",20001.0,9.780307278449E12,Fiction,,,,Yes,simple,Yes,No,No,No,Yes,30,1,30,55.00,0,0,S\n"
        "كابتشينو |Cappuccino,30001.0,,المشروبات الساخنة |Hot Drinks,,,,Yes,simple,No,No,No,No,Yes,0,0,0,14,0,0,S\n"
        "الدفتر الكبير,30002.0,,أدوات مكتبية,,,,Yes,simple,Yes,No,No,No,Yes,0,4,0,25,0,0,S\n"
        "قلم جاف/اللون ازرق,30003.0,,,,,,Yes,simple,Yes,No,No,No,Yes,0,4,0,5,0,0,S\n"
        "1234.0,30004.0,,,,,,Yes,simple,Yes,No,No,No,Yes,0,1,0,5,0,0,S\n"
        "كتاب بكمية سالبة,30005.0,,دار الجمل,,,,Yes,simple,Yes,No,No,No,Yes,0,-2,0,40,0,0,S\n")
ok(R.norm_code("3.2456789113E11") == "324567891130" and R.norm_code("9.780307278449E12") == "9780307278449", "Rewaa scientific-notation barcodes read exactly")
ok(R.is_isbn("9780307278449") and not R.is_isbn("324567891155") and not R.is_isbn("9780307278448")
   and R.is_isbn("0306406152") and not R.is_isbn("9783473743") and R.guess_language("1984", "9780451524935") == "en", "ISBN checksum separates real ISBNs from Rewaa internal codes")
up5 = c.post("/api/admin/import/upload", data={"file": (io.BytesIO(real.encode("utf-8-sig")), "simpleProducts.csv")},
             content_type="multipart/form-data").get_json()
s5 = {k: up5["headers"][v] for k, v in up5["suggested"].items()}
print("   suggested:", s5)
ok(s5.get("title") == "Product Name" and s5.get("sku") == "Product SKU" and s5.get("barcode") == "BarCode"
   and s5.get("publisher") == "Category" and s5.get("price") == "DEF Retail Price" and s5.get("quantity") == "DEF Quantity"
   and "author" not in s5, "real export mapped: Category -> publisher, retail (not buy) price, Brand not used as author")
p5 = c.post("/api/admin/import/plan", json={"upload_id": up5["upload_id"], "mapping": up5["suggested"], "missing_mode": "report"}).get_json()
reasons = {e["title"]: e["reason"] for e in p5["excluded"]}
ok(set(reasons) == {"كابتشينو |Cappuccino", "الدفتر الكبير", "قلم جاف/اللون ازرق"}, "cafe menu and stationery excluded with a reason")
ok(len(p5["invalid"]) == 0, "no invalid rows")
n5 = {i["sku"]: i for i in p5["new"]}
ok(set(n5) == {"10170", "10200", "20001", "30004", "30005"}, "float SKUs normalized (10170.0 -> 10170)")
ok(n5["30004"]["title"] == "1234", "number-only titles (1984, 2666) kept as books, float suffix removed")
ok(n5["10170"]["publisher"] == "المركز القومي للترجمة" and n5["10200"]["publisher"] == "دار الحضارة" and n5["20001"]["publisher"] == "",
   "publisher from Category; English genre label (Fiction) not taken as publisher")
ok(n5["10200"]["title"] == "نهاية الوحدة .. رواية من ألمانيا الرواية الحائزة", "broken lines in names joined, text kept")
ok(n5["20001"]["isbn"] == "9780307278449" and n5["10170"]["isbn"] == "" and n5["10170"]["barcode"] == "324567891155", "ISBN kept only when real; internal barcode kept as barcode")
ok(n5["20001"]["language"] == "en" and n5["10170"]["language"] == "ar", "English-only title imported as an English edition")
ok(n5["30005"]["quantity"] == 0 and n5["10200"]["price"] == 30.5, "negative stock -> 0; retail price used")
r5 = c.post("/api/admin/import/apply", json={"upload_id": up5["upload_id"], "mapping": up5["suggested"], "missing_mode": "report"}).get_json()
ok(r5["added"] == 5 and r5["excluded"] == 3, "apply adds books only, reports excluded")
row5 = con().execute("select publisher, isbn, barcode, language, source from books where rewaa_sku='20001'").fetchone()
ok(row5 == (None, "9780307278449", "9780307278449", "en", "rewaa"), "stored: isbn, barcode, language, no fake publisher")
ok(con().execute("select count(*) from books where title like 'كابتشينو%'").fetchone()[0] == 0, "no cafe product in the catalog")

# --- errors
bad = c.post("/api/admin/import/upload", data={"file": (io.BytesIO(b"x"), "a.pdf")}, content_type="multipart/form-data")
ok(bad.status_code == 400, "unsupported file type rejected")
ok(c.post("/api/admin/import/plan", json={"upload_id": "nope", "mapping": {}}).status_code == 400, "unknown upload rejected")
up3 = c.post("/api/admin/import/upload", data={"file": (io.BytesIO(csv_text.encode()), "x.csv")}, content_type="multipart/form-data").get_json()
ok(c.post("/api/admin/import/plan", json={"upload_id": up3["upload_id"], "mapping": {"price": 5}}).status_code == 400, "title column required")
semi = "الاسم;السعر;الكمية\nكتاب أ;10;1\n".encode("cp1256")
up4 = c.post("/api/admin/import/upload", data={"file": (io.BytesIO(semi), "win.csv")}, content_type="multipart/form-data").get_json()
ok(up4.get("headers") == ["الاسم", "السعر", "الكمية"], "Windows-Arabic (cp1256) + semicolon CSV")
big = c.post("/api/admin/import/upload", data={"file": (io.BytesIO(b"a" * (9 * 1024 * 1024)), "big.csv")}, content_type="multipart/form-data")
ok(big.status_code == 413, "oversized upload rejected")
cat = c.get("/api/admin/catalog").get_json()
ok(any(b["source"] == "rewaa" and b["last_synced_at"] for b in cat), "catalog exposes sync source/time")
import models as M
k = con(); k.execute("delete from book_profiles"); k.execute("delete from attribute_evidence"); k.execute("delete from books"); k.commit(); k.close()
M.seed_books()
ok(con().execute("select count(*) from books").fetchone()[0] == 0, "prototype seed never re-added after a Rewaa import")
print(f"TOTAL PASS={P} FAIL={F}")
