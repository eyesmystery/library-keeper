import os, sys, json, sqlite3
# Run through tests/run_all.py: cwd is a throwaway copy of backend/ with its own data/ (prototype catalog).
sys.path.insert(0, os.getcwd())
import models; models.init_db()
import authors
P = F = 0
def ok(cond, msg):
    global P, F; print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

pp = authors.parse_people
ok(pp("آلان مور (رسوم ديف غيبونز)") == [("آلان مور", "author", 0), ("ديف غيبونز", "illustrator", 0)], "illustrator in parentheses")
ok(pp("جيل هارينغتون وروبرت نيماير (تحرير)") == [("جيل هارينغتون", "editor", 0), ("روبرت نيماير", "editor", 1)], "editors joined by و")
ok([n for n, _, _ in pp("Slavoj Žižek، Frank Ruda، Agon Hamza")] == ["Slavoj Žižek", "Frank Ruda", "Agon Hamza"], "co-authors split on Arabic comma")
ok(pp("جورج وليام موراي") == [("جورج وليام موراي", "author", 0)], "a name containing «وليام» is not split")
ok(pp("وليد سيف") == [("وليد سيف", "author", 0)], "a name starting with و is kept")
ok(pp("عبد الله ولد محمدي") == [("عبد الله ولد محمدي", "author", 0)] and [n for n, _, _ in pp("سامي محمد وليد العلي")] == ["سامي محمد وليد العلي"], "«ولد»/«وليد» inside a name is not the joining و")
ok(pp("ماريو بارغاس يوسا", "صالح علماني، أحمد") [1] == ("صالح علماني", "translator", 0), "translator field linked as translator")
ok(pp("غير معروف") == [] and pp("") == [], "unknown author creates nobody")
ok(authors.key("أحمد خالد توفيق") == authors.key("احمد خالد توفيق") and authors.key("جيلبير") != authors.key("جيلبرت"), "normalized key")

con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
bid = con.execute("select book_id from books b join book_profiles p using(book_id) where p.overall_status in ('enriched','needs_review') limit 1").fetchone()[0]
other = con.execute("select book_id from books where book_id<>? limit 1", (bid,)).fetchone()[0]
con.execute("update books set author='كاتب اختبار الأول (رسوم رسام الاختبار)', translator='مترجم الاختبار' where book_id=?", (bid,))
con.execute("update books set author='كاتب اختبار الاول' where book_id=?", (other,))  # same person, alef spelling differs
before = {r[0]: dict(r) for r in con.execute("select book_id, title, author, translator from books")}
con.commit()
r1 = authors.sync()
r2 = authors.sync()
ok(r1["authors"] == r2["authors"] and r1["links"] == r2["links"] and r2["new_authors"] == 0, "sync is idempotent")
after = {r[0]: dict(r) for r in con.execute("select book_id, title, author, translator from books")}
ok(before == after, "books are never changed by the sync")
aid = con.execute("select author_id from authors where name_ar like 'كاتب اختبار%'").fetchall()
ok(len(aid) == 1, "alef spelling variants merge into one author")
links = con.execute("select book_id, role from book_authors where author_id=? order by book_id", (aid[0][0],)).fetchall()
ok(sorted(tuple(x) for x in links) == sorted([(bid, "author"), (other, "author")]), "both books linked to the author")
roles = dict(con.execute("""select a.name_ar, ba.role from book_authors ba join authors a using(author_id) where ba.book_id=?""", (bid,)).fetchall())
ok(roles.get("رسام الاختبار") == "illustrator" and roles.get("مترجم الاختبار") == "translator", "illustrator and translator roles stored")
style = json.loads(con.execute("select catalog_style from authors where author_id=?", (aid[0][0],)).fetchone()[0] or "null")
researched = con.execute("""select count(*) from book_profiles where book_id in (?, ?)
                             and overall_status in ('enriched','needs_review','approved')""", (bid, other)).fetchone()[0]
ok(style and style["books"] == researched and isinstance(style["moods"], list), "catalog style computed from researched cards only")
con.execute("update book_profiles set overall_status='pending' where book_id in (?, ?)", (bid, other)); con.commit(); authors.sync()
ok(con.execute("select catalog_style from authors where author_id=?", (aid[0][0],)).fetchone()[0] is None, "no catalog style without researched books")
# a staff/research link survives a resync
con.execute("insert into book_authors (book_id, author_id, role, position, source) values (?, ?, 'editor', 0, 'staff')", (other, aid[0][0]))
con.commit(); authors.sync()
ok(con.execute("select count(*) from book_authors where source='staff'").fetchone()[0] == 1, "staff links are kept on resync")
con.execute("insert into authors (name_ar, aliases) values ('جيلبير سينويه', '[]'), ('جيلبرت سينويه', '[]'), ('جوزيه ر. دوس سانتوس', '[]'), ('جوزيه رودريغيز دوس سانتوس', '[]'), ('محمد علي', '[]'), ('أحمد علي', '[]')")
con.commit()
pairs = {(a, b) for _, a, _, b in authors.probable_duplicates()}
ok(("جيلبير سينويه", "جيلبرت سينويه") in pairs and ("جوزيه ر. دوس سانتوس", "جوزيه رودريغيز دوس سانتوس") in pairs, "probable duplicate spellings reported")
ok(not any("محمد علي" in p and "أحمد علي" in p for p in pairs), "different common first names are not reported")


print(f"TOTAL PASS={P} FAIL={F}")

# one person spelled twice: the displayed spelling is unified, then the records merge (2026-09-30)
con.execute("update books set author='جيلبرت سينويه تجربة', translator=NULL where book_id=?", (bid,))
con.execute("update books set author='جيلبير سينويه تجربة' where book_id=?", (other,))
con.commit(); authors.sync()
keep, drop = authors.find(con, "جيلبير سينويه تجربة"), authors.find(con, "جيلبرت سينويه تجربة")
changed = authors.unify_display(con, keep, drop); con.commit()
authors.merge_authors(keep, drop)
ok([c["book_id"] for c in changed] == [bid] and con.execute("select author from books where book_id=?", (bid,)).fetchone()[0] == "جيلبير سينويه تجربة", "variant spelling replaced in the displayed author")
authors.sync()
ok(authors.find(con, "جيلبرت سينويه تجربة") == keep and con.execute("select count(*) from book_authors where author_id=? and role='author'", (keep,)).fetchone()[0] == 2, "variant kept as alias; resync links both books to one record")
ok(authors.parse_people("غارون تسوتشيا (تأليف) ونوبواكي مينيغيشي (رسوم)") == [("غارون تسوتشيا", "author", 0), ("نوبواكي مينيغيشي", "illustrator", 0)]
   and authors.parse_people("محمد نور الدين (اختيار وترجمة)") == [("محمد نور الدين", "translator", 0)], "bare role labels apply to the names before them")

# author research import (batch records -> authors), 2026-09-30
import subprocess, tempfile
tmpdir = tempfile.mkdtemp()
target = authors.find(con, "جيلبير سينويه تجربة")
rec = dict(author_id=target, name_ar="جيلبير سينويه تجربة", name_latin="Gilbert Test", origin="مصر", aliases=["سينويه التجربة"],
           known_for=["كتاب"], formats=["novel"], subgenres=["رواية تاريخية"], moods=["suspenseful"], pace="medium",
           typical_length="long", themes=["الشرق"], audience="adult", style_note_ar="ملاحظة", confidence=0.9, research_status="researched")
bad = dict(rec, author_id=target, name_ar="اسم لا يطابق", name_latin=None, research_status="researched")
wrong_vocab = dict(rec, moods=["joyful"])
json.dump([rec, bad, wrong_vocab], open(os.path.join(tmpdir, "records.json"), "w", encoding="utf-8"), ensure_ascii=False)
script = os.path.join(os.getcwd(), "..", "scripts", "import_author_research.py")
out = subprocess.run([sys.executable, script, models.DB_PATH, tmpdir, "--apply"], capture_output=True, text=True)
rep = json.load(open(os.path.join(tmpdir, "import_report.json"), encoding="utf-8"))
row = con.execute("select * from authors where author_id=?", (target,)).fetchone()
ok(out.returncode == 0 and rep["summary"]["imported"] == 1 and rep["summary"]["skipped"] == 2, "import: mismatched author and bad vocabulary skipped " + str(target) + out.stdout[-300:] + out.stderr[-300:])
ok(row["research_status"] == "researched" and json.loads(row["moods"]) == ["suspenseful"] and row["confidence"] == 0.75, "import: style stored, confidence capped at 0.75")
ok(authors.find(con, "سينويه التجربة") == target, "import: alias added")
ok(con.execute("select author from books where book_id=?", (bid,)).fetchone()[0] == "جيلبير سينويه تجربة", "import never touches books")

# Wikidata results -> authors (sourced names/origin/known works), 2026-10-01
wd_dir = tempfile.mkdtemp()
con.execute("update authors set research_status='needs_sources', origin=NULL, known_for='[]' where author_id=?", (target,)); con.commit()
other_name = con.execute("select name_ar from authors where author_id<>? limit 1", (target,)).fetchone()[0]
wd = [{"author_id": target, "name": "جيلبير سينويه تجربة", "status": "matched", "qid": "Q1", "url": "https://www.wikidata.org/wiki/Q1",
       "labels": {"ar": "جيلبير سينويه تجربة", "en": "Gilbert Wd", "fr": "Gilbert Wd"}, "aliases": {"ar": ["سينويه ويكي", other_name]},
       "countries": ["مصر"], "occupations": ["روائي"], "genres": [], "notable_works": ["عمل مشهور"], "born": None},
      {"author_id": target, "name": "x", "status": "ambiguous", "candidates": []}]
json.dump(wd, open(os.path.join(wd_dir, "results.json"), "w", encoding="utf-8"), ensure_ascii=False)
script = os.path.join(os.getcwd(), "..", "scripts", "lookup_authors_wikidata.py")
out = subprocess.run([sys.executable, script, "apply", models.DB_PATH, os.path.join(wd_dir, "results.json"), "--apply"], capture_output=True, text=True)
row = con.execute("select * from authors where author_id=?", (target,)).fetchone()
ok(out.returncode == 0 and "https://www.wikidata.org/wiki/Q1" in json.loads(row["source_urls"]), "wikidata: source URL recorded " + out.stderr[-300:])
ok(authors.find(con, "سينويه ويكي") == target and authors.find(con, other_name) != target, "wikidata: alias added, another author's name refused")
ok(row["origin"] == "مصر" and json.loads(row["known_for"]) == ["عمل مشهور"] and row["research_status"] == "thin", "wikidata: origin/known works filled; needs_sources -> thin")

# Abjjad categories -> author style (2026-10-01)
sys.path.insert(0, os.path.join(os.getcwd(), "..", "scripts"))
import lookup_authors_abjjad as abj
f, sg, md = abj.style_from([["روايات رعب", "روايات خيالية", "روايات اجتماعية"], ["روايات رعب", "روايات غموض"], ["روايات رعب", "روايات اجتماعية"]])
ok(f == ["novel"] and sg == ["رعب", "رواية اجتماعية"] and "dark" in md and "mysterious" not in md, "abjjad: only recurring categories count; «روايات خيالية» ignored")
ok(abj.style_from([[]]) == ([], [], []), "abjjad: no categories, no style")
con.execute("update authors set research_status='thin', subgenres='[]', moods='[]', formats='[]' where author_id=?", (target,)); con.commit()
ab_dir = tempfile.mkdtemp()
json.dump([{"author_id": target, "name": "جيلبير سينويه تجربة", "status": "matched", "url": "https://www.abjjad.com/author/1/x", "confirmed": True,
            "rating": "4.1", "reviews": "12", "books": ["كتاب"], "categories": [["روايات رعب"], ["روايات رعب", "روايات غموض"]]}],
          open(os.path.join(ab_dir, "results.json"), "w", encoding="utf-8"), ensure_ascii=False)
out = subprocess.run([sys.executable, os.path.join(os.getcwd(), "..", "scripts", "lookup_authors_abjjad.py"), "apply", models.DB_PATH,
                      os.path.join(ab_dir, "results.json"), "--apply"], capture_output=True, text=True)
row = con.execute("select * from authors where author_id=?", (target,)).fetchone()
ok(out.returncode == 0 and json.loads(row["subgenres"]) == ["رعب"] and row["research_status"] == "researched"
   and "https://www.abjjad.com/author/1/x" in json.loads(row["source_urls"]), "abjjad apply: style filled, confirmed thin -> researched " + out.stderr[-300:])

# --fill-only keeps sourced values and never lowers the status (2026-10-01)
con.execute("update authors set research_status='researched', subgenres=?, origin='مصدر', moods='[]' where author_id=?", (json.dumps(["رعب"], ensure_ascii=False), target)); con.commit()
fo_dir = tempfile.mkdtemp()
json.dump([dict(rec, research_status="thin", subgenres=["رواية اجتماعية"], moods=["sad"], origin="تخمين", aliases=[])],
          open(os.path.join(fo_dir, "records.json"), "w", encoding="utf-8"), ensure_ascii=False)
subprocess.run([sys.executable, script if script.endswith("import_author_research.py") else os.path.join(os.getcwd(), "..", "scripts", "import_author_research.py"),
                models.DB_PATH, fo_dir, "--apply", "--fill-only"], capture_output=True, text=True)
row = con.execute("select * from authors where author_id=?", (target,)).fetchone()
ok(json.loads(row["subgenres"]) == ["رعب"] and row["origin"] == "مصدر" and json.loads(row["moods"]) == ["sad"] and row["research_status"] == "researched",
   "fill-only: sourced values kept, empty moods filled, status not lowered")
print(f"TOTAL PASS={P} FAIL={F}")
