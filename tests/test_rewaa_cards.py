import os, sys, json, sqlite3, subprocess, tempfile
# Run through tests/run_all.py: cwd is a throwaway copy of backend/ with its own data/.
sys.path.insert(0, os.getcwd())
import models; models.init_db()
import app as A
P = F = 0
def ok(cond, msg):
    global P, F; print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)
DB = models.DB_PATH
con = lambda: sqlite3.connect(DB)
k = con()
cur = k.execute("INSERT INTO books (title, author, publisher, language, price, quantity, active, barcode, rewaa_sku, source) "
                "VALUES ('المادية السنوية', 'غير معروف', 'منشورات الجمل', 'ar', 50, 2, 1, '324567890001', '99001', 'rewaa')")
b1 = cur.lastrowid
k.execute("INSERT INTO book_profiles (book_id, overall_status) VALUES (?, 'pending')", (b1,))
cur = k.execute("INSERT INTO books (title, author, language, price, quantity, active, rewaa_sku, source) "
                "VALUES ('الموت', 'غير معروف', 'ar', 20, 1, 1, '99002', 'rewaa')")
b2 = cur.lastrowid
k.execute("INSERT INTO book_profiles (book_id, overall_status) VALUES (?, 'pending')", (b2,))
k.commit(); k.close()
d = tempfile.mkdtemp()
R = lambda n, v, c, rc, site="goodreads", scope="reader_signal", **x: dict(name=n, value=v, confidence=c, review_count=rc, source_site=site, edition_scope=scope, summary="…", urls=["https://example.org"], **x)
json.dump({"book_id": b1, "match": {"status": "matched", "note": "typo fixed"}, "title_display": "المأدبة السنوية",
           "author": "ماتياس إينار", "original_language": "fr", "edition_language": "ar", "translator": "مترجم",
           "publication_year": 2020, "edition_year": 2022, "page_count": 531, "isbn13": "9780307278449",
           "format": "novel", "subjects": ["الريف"], "themes": ["الموت", "الذاكرة"], "setting": "فرنسا",
           "summary_ar": "نبذة قصيرة بكلماتنا.", "hook_ar": "جملة جذب.", "summary_source_url": "https://example.org",
           "cover_image_url": "https://example.org/c.jpg", "series_name": "سلسلة", "series_position": 2,
           "rows": [R("format", "novel", 0.9, 5, scope="work"), R("moods", "funny", 0.85, 5), R("moods", "dark", 0.85, 3),
                    R("pace", "slow", 0.75, 3), R("language_difficulty", "medium", 0.75, 3, scope="work"),
                    R("style_complexity", "literary", 0.75, 3, scope="edition"), R("ending_tone", "open", 0.7, 2),
                    R("sensitive_content", "grief", 0.75, 3, scope="work", severity=2, frequency="recurring", note_ar="فقد"),
                    R("moods", "tense", 0.5, 1), R("pace", "bogus", 0.9, 5)]},
          open(os.path.join(d, f"{b1}.json"), "w"), ensure_ascii=False)
json.dump({"book_id": b2, "match": {"status": "needs_confirmation", "note": "generic title"}, "rows": [],
           "candidates": [{"title": "الموت", "author": "كاتب أ", "publisher": "دار", "year": 2020, "url": "https://example.org/a"},
                          {"title": "الموت", "author": "كاتب ب"}, {"author": "بدون عنوان"}, {"title": "4"}, {"title": "5"}]},
          open(os.path.join(d, f"{b2}.json"), "w"), ensure_ascii=False)
script = os.path.join(os.path.dirname(os.getcwd()), "scripts", "import_rewaa_cards.py")
dry = subprocess.run([sys.executable, script, DB, d], capture_output=True, text=True)
ok(dry.returncode == 0 and '"imported": 1' in dry.stdout and '"flagged": 1' in dry.stdout, "dry run: 1 import, 1 flagged")
ok(con().execute("select author from books where book_id=?", (b1,)).fetchone()[0] == "غير معروف", "dry run writes nothing")
run = subprocess.run([sys.executable, script, DB, d, "--apply"], capture_output=True, text=True)
ok(run.returncode == 0, "apply ran " + run.stderr[-200:])
b = dict(zip(["title","author","genre","language","original_language","translator","publication_year","page_count","isbn","summary","hook","setting","series_name","series_position","research_status","price","quantity","rewaa_sku"],
             con().execute("select title,author,genre,language,original_language,translator,publication_year,page_count,isbn,summary,hook,setting,series_name,series_position,research_status,price,quantity,rewaa_sku from books where book_id=?", (b1,)).fetchone()))
ok(b["title"] == "المأدبة السنوية" and b["author"] == "ماتياس إينار" and b["genre"] == "رواية", "title fixed, author and genre set")
ok(b["publication_year"] == 2022 and b["page_count"] == 531 and b["isbn"] == "9780307278449", "edition year (not original year), pages, valid ISBN")
ok(b["original_language"] == "fr" and b["translator"] == "مترجم" and b["language"] == "ar", "language of the work")
ok(b["summary"] and b["hook"] and b["setting"] == "فرنسا" and b["series_name"] == "سلسلة" and b["series_position"] == 2, "v2 book-level fields")
ok(b["price"] == 50 and b["quantity"] == 2 and b["rewaa_sku"] == "99001" and b["research_status"] == "matched", "inventory untouched; status matched")
p = con().execute("select moods, pace, language_difficulty, style_complexity, ending_tone, themes, sensitive_content, overall_status, format from book_profiles where book_id=?", (b1,)).fetchone()
ok(json.loads(p[0]) == ["funny", "dark"] and p[1] == "slow", "moods/pace from rows (low-confidence and bad values dropped)")
ok(p[2] is None and p[3] == "literary", "translated work: language difficulty needs Arabic-edition evidence; edition-scoped style kept")
ok(p[4] == "open" and json.loads(p[5]) == ["الموت", "الذاكرة"] and json.loads(p[6]) == ["grief"] and p[8] == "novel", "v2 card: ending tone, themes, grief warning")
ok(p[7] == "enriched", "card enriched (High evidence, no conflict)")
ev = con().execute("select confidence, severity, frequency, note_ar from attribute_evidence where book_id=? and attribute_value='dark'", (b1,)).fetchone()
ok(ev[0] == 0.84, "High tier capped when fewer than 5 reviews")
g = con().execute("select severity, frequency, note_ar from attribute_evidence where book_id=? and attribute_value='grief'", (b1,)).fetchone()
ok(g == (2, "recurring", "فقد"), "content warning severity/frequency/note stored")
ok(con().execute("select research_status, author from books where book_id=?", (b2,)).fetchone() == ("needs_confirmation", "غير معروف"), "unconfirmed book only flagged")
cands = json.loads(con().execute("select research_candidates from books where book_id=?", (b2,)).fetchone()[0])
ok(len(cands) == 3 and cands[0]["author"] == "كاتب أ" and all(c["title"] for c in cands), "up to 3 candidates stored for staff to choose from")
ok(json.load(open(os.path.join(d, "covers_todo.json"))) == {str(b1): "https://example.org/c.jpg"}, "cover URL queued for fetch_covers")
cards = models.get_profile_cards()
ok(cards[b1]["severity"].get("grief") == 2, "card severity map read from evidence")
book = dict(con().execute("select * from books where book_id=?", (b1,)).fetchone() and A.get_book(b1))
fact = A.book_fact(book, cards[b1], "سبب")
ok(fact.get("hook") == "جملة جذب." and fact.get("themes") == ["الموت", "الذاكرة"] and fact.get("subjects") == ["الريف"]
   and fact.get("setting") == "فرنسا" and fact.get("series") == "سلسلة #2", "chat facts carry hook, themes, subjects, setting, series")
ok(any("فقد وحزن" in x for x in fact["impression"] + fact["confirmed"]), "grief warning has an Arabic label")
from auth_helper import login
c = login(A.app.test_client())
card = c.get(f"/api/admin/books/{b1}").get_json()
ok(card["about"]["hook"] == "جملة جذب." and card["about"]["subjects"] == ["الريف"] and card["profile"]["themes"] == ["الموت", "الذاكرة"]
   and card["about"]["setting"] == "فرنسا" and card["about"]["series"] == "سلسلة" and card["about"]["series_position"] == 2 and card["about"]["research_label"], "admin card: about section")
att = {a["key"]: a for a in card["attributes"]}
ok(att["ending_tone"]["card"] == [{"value": "open", "label": "مفتوحة"}] and att["style_complexity"]["name"] == "الأسلوب", "admin card: v2 attributes with Arabic labels")
g = [e for e in att["sensitive_content"]["evidence"] if e["value"] == "grief"][0]
ok(g["severity_label"] == "واضحة" and g["frequency_label"] == "متكرر" and g["note"] == "فقد" and g["label"] == "فقد وحزن", "admin card: warning severity, frequency, note")
card2 = c.get(f"/api/admin/books/{b2}").get_json()
ok(card2["about"]["research_status"] == "needs_confirmation" and card2["about"]["research_label"] == "يحتاج تأكيد هوية الكتاب", "admin card: unconfirmed book is labelled")
ev = [e for e in att["ending_tone"]["evidence"]][0]["evidence_id"]
r = c.post(f"/api/admin/books/{b1}/attributes/ending_tone", json={"action": "set", "value": "sad"})
ok(r.status_code == 200 and con().execute("select ending_tone from book_profiles where book_id=?", (b1,)).fetchone()[0] == "sad", "staff can set a v2 attribute; card recomputed")
r = c.post(f"/api/admin/books/{b1}/attributes/moods", json={"action": "confirm", "evidence_id": [e for e in att["moods"]["evidence"]][0]["evidence_id"]})
ok(r.status_code == 200 and con().execute("select style_complexity, ending_tone from book_profiles where book_id=?", (b1,)).fetchone() == ("literary", "sad"), "reviewing v1 attributes keeps v2 values")
# out-of-range edition year from a source listing (book 4350: 2028) is not stored, and is listed for staff (AUDFIX 2026-10-14)
import time
Y = time.localtime().tm_year
k = con()
ids = []
for t in ("سنة بعيدة", "سنة قادمة"):
    ids.append(k.execute("INSERT INTO books (title, author, language, price, quantity, active, rewaa_sku, source) "
                         "VALUES (?, 'كاتب', 'ar', 20, 1, 1, ?, 'rewaa')", (t, "9900" + str(len(ids) + 5))).lastrowid)
    k.execute("INSERT INTO book_profiles (book_id, overall_status) VALUES (?, 'pending')", (ids[-1],))
k.commit(); k.close()
d2 = tempfile.mkdtemp()
for bid, y in zip(ids, (Y + 2, Y + 1)):
    json.dump({"book_id": bid, "match": {"status": "matched"}, "edition_language": "ar", "original_language": "ar",
               "edition_year": y, "page_count": 150, "format": "biography", "rows": [R("format", "biography", 0.9, 0, site="web", scope="work")]},
              open(os.path.join(d2, f"{bid}.json"), "w"), ensure_ascii=False)
run = subprocess.run([sys.executable, script, DB, d2, "--apply"], capture_output=True, text=True)
rep = json.load(open(os.path.join(d2, "import_report.json"), encoding="utf-8"))
yrs = [con().execute("select publication_year, page_count from books where book_id=?", (i,)).fetchone() for i in ids]
ok(run.returncode == 0 and yrs[0] == (None, 150), "edition year after next year not stored (rest of the record imported)")
ok(any("out of range" in x for x in rep["issues"].get(str(ids[0]), [])), "out-of-range year listed in the import report for staff")
ok(yrs[1] == (Y + 1, 150) and str(ids[1]) not in rep["issues"], "next year's edition still accepted")
print(f"TOTAL PASS={P} FAIL={F}")
