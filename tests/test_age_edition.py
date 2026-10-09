"""Edition language wish, children's target age, smaller QA items (2026-10-09)."""
import json, os, sqlite3, subprocess, sys, tempfile
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models, app as A, author_flow, topics
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
cols = {r[1] for r in con.execute("pragma table_info(books)")}
ok({"age_min", "age_max", "age_source", "age_note"} <= cols, "migration adds the age columns")

# ---- edition wish
W = A.wanted_edition
ok(W("عندكم الخيميائي بالإنجليزي؟") == "en" and W("ابي نسخته الانجليزية") == "en" and W("أبغاه بالعربي") == "ar", "Arabic ways of asking for a language")
ok(W("do you have any English novels?", "en") == "en" and W("anything in English") == "en", "English chat")
ok(W("رواية انجليزية مترجمة") is None and W("أدب إنجليزي") is None, "«رواية انجليزية» is the original language, not the copy")

# ---- age fit
ok(A.age_fit({"age_min": 6, "age_max": 9}, 7) == 1 and A.age_fit({"age_min": 6, "age_max": 9}, 10) == 1
   and A.age_fit({"age_min": 6, "age_max": 9}, 12) == -1 and A.age_fit({"age_min": 10, "age_max": None}, 4) == -1
   and A.age_fit({}, 5) == 0 and A.age_fit({"age_min": 3}, None) == 0, "age range with one year of slack; unknown stays neutral")
ok(A.AGE_RANGE_AR(4, 8) == "من ٤ إلى ٨ سنوات" and A.AGE_RANGE_AR(10, None) == "من ١٠ سنوات وفوق", "age range wording")

# ---- staff set the age in the card
bid = con.execute("select book_id from books where active=1 and quantity>0 order by book_id limit 1").fetchone()[0]
m = login(A.app.test_client())
ok(m.patch(f"/api/books/{bid}", json={"age_min": 9, "age_max": 4}).status_code == 400
   and m.patch(f"/api/books/{bid}", json={"age_min": 30}).status_code == 400, "a wrong range is refused")
ok(m.patch(f"/api/books/{bid}", json={"age_min": 4, "age_max": 8}).status_code == 200, "staff save an age range")
r = con.execute("select age_min, age_max, age_source from books where book_id=?", (bid,)).fetchone()
ok(tuple(r) == (4, 8, "staff"), "saved with source «staff»")
card = m.get(f"/api/admin/books/{bid}").get_json()
ok(card.get("age_min") == 4 or (card.get("book") or {}).get("age_min") == 4, "the admin card returns it")
fact = A.book_fact(dict(con.execute("select * from books where book_id=?", (bid,)).fetchone()), None, "x")
ok(fact.get("age_range") == "من ٤ إلى ٨ سنوات", "the chat facts carry the age range")

# ---- import script: staff ages kept, bad rows skipped
bid2 = con.execute("select book_id from books where active=1 and quantity>0 and book_id<>? order by book_id limit 1", (bid,)).fetchone()[0]
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "b.json")
json.dump([{"book_id": bid, "age_min": 1, "age_max": 2, "source": "https://x"},
           {"book_id": bid2, "age_min": 7, "age_max": 10, "source": "https://example.com/p", "note": "الناشر: 7–10"},
           {"book_id": bid2, "age_min": 5}, {"book_id": 99999999, "age_min": 3, "source": "s"}], open(path, "w"))
root = os.path.dirname(os.getcwd())
out = subprocess.run([sys.executable, os.path.join(root, "scripts", "import_ages.py"), path, "--apply"], capture_output=True, text=True, cwd=root)
res = json.loads(out.stdout)
ok(res["ok"] == 1 and len(res["skipped"]) == 3, "import: one good row; staff age, no source and unknown book skipped")
con2 = sqlite3.connect(models.DB_PATH)
ok(con2.execute("select age_min, age_max, age_source from books where book_id=?", (bid2,)).fetchone() == (7, 10, "https://example.com/p")
   and con2.execute("select age_min from books where book_id=?", (bid,)).fetchone()[0] == 4, "imported; staff value untouched")

# ---- smaller QA items
ok(author_flow.is_yes("ايه، وش أسهل كتاب له؟") and author_flow.is_yes("نعم") and not author_flow.is_yes("لا مو هو")
   and not author_flow.is_yes("هو " + "كلام " * 20), "«ايه، …» confirms the author")
idx = topics.index({b["book_id"]: dict(b) for b in A.list_books(only_active=False)})
gr = [r[0] for r in con.execute("select book_id from books where title like '%غرناطة%' limit 3")]
if gr:
    ok(topics.core_word("سقوط غرناطة", idx) in ("غرناطه", "غرناطة") and topics.core_word("غرناطة", idx) is None,
       "«سقوط غرناطة» falls back to «غرناطة» when no book has both words")
page = A.app.test_client().get("/admin").get_data(as_text=True)
ok("saveAge" in page and "fAgeMin" in page, "admin card has the age fields")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
