"""Book location: never invented; typed shelf -> active category section -> ask a staff member (2026-09-27)."""
import os, sys, json, sqlite3, shutil, subprocess, tempfile
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models, sections, app as A
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# a fresh database gets the suggested sections, all switched off
tmpdb = os.path.join(tempfile.mkdtemp(), "fresh.db")
orig = models.DB_PATH
models.DB_PATH = tmpdb
models.init_db()
fresh = sqlite3.connect(tmpdb).execute("select name, active from library_sections").fetchall()
ok(len(fresh) == len(models.SUGGESTED_SECTIONS) and all(a == 0 for _, a in fresh), "suggested sections created once, switched off")
models.DB_PATH = orig
sections.invalidate()

models.init_db()
con = sqlite3.connect(models.DB_PATH)
ok(con.execute("select count(*) from books where shelf_location is not null and shelf_location <> ''").fetchone()[0] == 0,
   "no invented shelf codes in the catalog")
m = login(A.app.test_client())
staff = login(A.app.test_client(), "staff")
ok(staff.get("/api/admin/sections").status_code == 200 and A.app.test_client().get("/api/admin/sections").status_code == 401,
   "sections: staff may open them (settings, Ibrahim 2026-10-13); anonymous may not")

# start from a known state: one active section for novels, one inactive for children
r = m.post("/api/admin/sections", json={"sections": [
    {"name": "قسم الروايات", "genres": ["رواية", "قصص قصيرة"], "active": True},
    {"name": "قسم الأطفال", "genres": ["أطفال"], "active": False}]})
ok(r.status_code == 200 and [s["name"] for s in r.get_json()["sections"]] == ["قسم الروايات", "قسم الأطفال"], "manager saves sections")
novel = con.execute("select book_id from books where genre='رواية' and active=1 and quantity>0 limit 1").fetchone()[0]
kid = con.execute("select book_id from books where genre='أطفال' and active=1 and quantity>0 limit 1").fetchone()[0]
other = con.execute("select book_id from books where genre='تاريخ' and active=1 and quantity>0 limit 1").fetchone()[0]
pub = {b["book_id"]: b for b in A.app.test_client().get("/api/books").get_json()}
ok(pub[novel]["location"] == "قسم الروايات" and pub[novel]["location_kind"] == "section", "book shows its active category section")
ok(pub[kid]["location"] is None, "inactive section is not shown")
ok(pub[other]["location"] is None and pub[other]["location_kind"] is None, "no section -> no location (never invented)")
ok(A.book_fact(dict(models.get_book(other)), None, "x")["shelf"] is None and A.book_fact(dict(models.get_book(novel)), None, "x")["shelf"] == "قسم الروايات", "chat facts carry the real location or nothing")
ok("لا تذكر أي موقع ولا تخمّنه" in __import__("chat").RESPOND_SYSTEM_AR, "prompt forbids guessing a location")

# a location typed by staff wins over the section
ok(staff.patch(f"/api/books/{novel}", json={"shelf_location": "رف ٣ – الطابق الأرضي"}).status_code == 200, "staff types a location")
b = next(x for x in A.app.test_client().get("/api/books").get_json() if x["book_id"] == novel)
ok(b["location"] == "رف ٣ – الطابق الأرضي" and b["location_kind"] == "shelf", "typed location shown as written")
staff.patch(f"/api/books/{novel}", json={"shelf_location": ""})
b = next(x for x in A.app.test_client().get("/api/books").get_json() if x["book_id"] == novel)
ok(b["location_kind"] == "section", "clearing it falls back to the section")
r = A.app.test_client().post("/api/recommend", json={"query": "رواية"}).get_json()
ok(all("location" in x for x in r["results"]), "recommend results carry the location")

# validation
bad = [({"sections": [{"name": "أ", "genres": ["رواية"], "active": True}, {"name": "ب", "genres": ["رواية"], "active": True}]}, "a category in two sections refused"),
       ({"sections": [{"name": "أ", "genres": ["نوع غير موجود"]}]}, "unknown category refused"),
       ({"sections": [{"name": "  ", "genres": []}]}, "empty name refused"),
       ({"sections": "x"}, "bad payload refused")]
for body, why in bad:
    r = m.post("/api/admin/sections", json=body)
    ok(r.status_code == 400 and r.get_json().get("error"), why)
d = m.get("/api/admin/sections").get_json()
ok(d["without_location"] > 0 and d["available_books"] >= d["without_location"] and any(g["genre"] == "رواية" and g["books"] > 0 for g in d["genres"]),
   "summary counts books without a location and books per category")
ok("sections.set" in [a["action"] for a in __import__("auth").recent_audit(30)], "section changes are audited")

# the research import no longer invents shelf codes
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "work.db"); shutil.copy(models.DB_PATH, db)
seed = os.path.join(tmp, "seed.json"); shutil.copy("../data/seed_books.json", seed)
os.makedirs(os.path.join(tmp, "recs", "new"))
rows = [{"name": n, "value": v, "confidence": 0.8, "review_count": 3, "conflict": False, "source_site": "goodreads",
         "edition_scope": "original_or_any", "summary": "x", "urls": ["https://example.org/a"]}
        for n, v in (("moods", "warm"), ("pace", "medium"), ("emotional_weight", "light"))]
json.dump({"key": "1-1", "category": "x", "title_ar": "كتاب موقع تجريبي", "author_ar": "مؤلف", "catalog_genre": "رواية", "rows": rows},
          open(os.path.join(tmp, "recs", "new", "1-1.json"), "w"), ensure_ascii=False)
res = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.getcwd()), "scripts", "import_enrichment.py"), db, seed,
                      os.path.join(tmp, "recs"), "--apply"], capture_output=True, text=True)
if res.returncode:
    print(res.stderr[-800:])
row = sqlite3.connect(db).execute("select shelf_location from books where title='كتاب موقع تجريبي'").fetchone()
ok(row is not None and row[0] is None, "a researched new book gets no invented shelf")
ok(all(not b.get("shelf_location") for b in json.load(open(seed, encoding="utf-8"))), "seed file carries no invented shelves")
print(f"TOTAL PASS={P} FAIL={F}")
