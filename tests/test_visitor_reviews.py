"""«ماذا تقرأ الآن؟» (Ibrahim 2026-10-27): visitors' reviews (stars, traits, words, optional first name; shown after staff
approve), books suggested for the shelves (ours in stock shown, ours out of stock kept, others looked up and confirmed),
«تفضيلات قرّاء مكتبة ثلاث» in the chat once enough books have reviews, and at least five helpers under an answer."""
import os, re, sys, sqlite3, json
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
os.environ["LIBRARY_KEEPER_RATE_NOTES"] = "500"
import models; models.init_db()
import app as A, visitor_reviews as VR, shelf_suggestions as SS, helper_chips as H, start_paths as SP, chat as C
import outside_books as OB, inbox_export
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

con = sqlite3.connect(models.DB_PATH)
visitor = A.app.test_client()
ids = [r[0] for r in con.execute("select book_id from books where active=1 and quantity>0 and title is not null order by book_id limit 14")]
bid = ids[0]
title = con.execute("select title from books where book_id=?", (bid,)).fetchone()[0]
out_id = ids[1]
con.execute("update books set quantity=0 where book_id=?", (out_id,)); con.commit()
out_title = con.execute("select title from books where book_id=?", (out_id,)).fetchone()[0]

# ---- writing a review
ok(visitor.post("/api/reviews", json={"book_id": bid, "stars": 0}).status_code == 400
   and visitor.post("/api/reviews", json={"book_id": bid, "stars": 6}).status_code == 400
   and visitor.post("/api/reviews", json={"book_id": bid, "stars": "4.5"}).status_code == 400
   and visitor.post("/api/reviews", json={"book_id": bid, "stars": True}).status_code == 400
   and visitor.post("/api/reviews", json={"book_id": bid}).status_code == 400, "stars 1–5 are required (0, 6, 4.5, true refused)")
ok(visitor.post("/api/reviews", json={"book_id": 999999999, "stars": 4}).status_code == 400
   and visitor.post("/api/reviews", data="{x", content_type="application/json").status_code == 400, "unknown book or bad request refused")
r = visitor.post("/api/reviews", json={"book_id": bid, "stars": 5, "tags": ["gripping", "fun", "nonsense", "fun"],
                                       "text": "رواية حلوة مرة، كلموني على 0551234567 أو www.example.com @someone", "first_name": "سارة",
                                       "language": "ar"})
ok(r.status_code == 201 and r.get_json()["ok"], "a review is saved")
rid = r.get_json()["review_id"]
row = con.execute("select stars, tags, text, clean_text, first_name, status from visitor_reviews where review_id=?", (rid,)).fetchone()
ok(row[0] == 5 and json.loads(row[1]) == ["gripping", "fun"], "unknown and repeated traits are dropped")
ok("0551234567" in row[2] and "0551234567" not in row[3] and "example.com" not in row[3] and "@someone" not in row[3],
   "the shown copy has no phone number, link or @handle (the visitor's own words are kept for staff)")
ok(row[4] == "سارة" and row[5] == "pending", "first name kept; the review waits for approval")
r2 = visitor.post("/api/reviews", json={"book_id": out_id, "stars": 2, "tags": ["slow"], "first_name": "abc123@x"}).get_json()
ok(con.execute("select first_name from visitor_reviews where review_id=?", (r2["review_id"],)).fetchone()[0] is None,
   "a «name» with digits or @ is not kept; a book out of stock can be reviewed")
cols = [c[1] for c in con.execute("pragma table_info(visitor_reviews)")]
ok(not any(c in cols for c in ("ip", "address", "conversation_id", "visitor_id", "phone", "email")), "no visitor address or conversation stored")

# ---- nothing shown before approval
ok(visitor.get(f"/api/reviews/book/{bid}").get_json() == {"count": 0}, "before approval the book page shows no review")
book = dict(A.get_book(bid))
ok("thalath_readers" not in A.book_fact(book, None, "", "ar"), "before approval the chat knows nothing of it")

# ---- staff
m = login(A.app.test_client())
ok(visitor.get("/api/admin/inbox/reviews").status_code == 401 and visitor.post(f"/api/admin/inbox/reviews/{rid}", json={"status": "approved"}).status_code == 401
   and visitor.get("/api/admin/inbox/candidates").status_code == 401, "the review and candidate lists need sign-in")
d = m.get("/api/admin/inbox/reviews").get_json()
ok(d["counts"]["pending"] == 2 and len(d["items"]) == 2 and d["items"][0]["tag_labels"] in (["يشد", "ممتع"], ["بطيء"]), "staff see both waiting, with trait labels")
c = m.get("/api/admin/inbox/count").get_json()
ok(c["reviews_open"] == 2, "the inbox badge counts the waiting reviews")
al = {i["key"]: i for i in m.get("/api/admin/inbox/alerts").get_json()["items"]}
ok(al["reviews"]["open"] == 2 and al["reviews"]["go"] == "inbox:reviews", "«التنبيهات» lists the waiting reviews")
ok(m.post(f"/api/admin/inbox/reviews/{rid}", json={"status": "approved"}, headers={"X-CSRF-Token": m.csrf}).status_code == 200,
   "approve")
s = visitor.get(f"/api/reviews/book/{bid}").get_json()
ok(s["count"] == 1 and s["avg"] == 5 and s["tags"][0]["label"] == "يشد" and s["reviews"][0]["name"] == "سارة"
   and "0551234567" not in s["reviews"][0]["text"], "after approval the book page shows it (cleaned words, first name)")
f = A.book_fact(dict(A.get_book(bid)), None, "", "ar")
ok(f.get("thalath_readers", {}).get("readers") == 1 and f["thalath_readers"]["stars_out_of_5"] == 5 and f["thalath_readers"]["quotes"],
   "the chat's facts carry «قرّاء مكتبة ثلاث»")
m.post(f"/api/admin/inbox/reviews/{rid}", json={"status": "approved", "hide_text": True}, headers={"X-CSRF-Token": m.csrf})
s = visitor.get(f"/api/reviews/book/{bid}").get_json()
ok(s["count"] == 1 and s["reviews"] == [], "«اعتماد بدون النص»: the stars count, the words are not shown")
m.post(f"/api/admin/inbox/reviews/{r2['review_id']}", json={"status": "rejected"}, headers={"X-CSRF-Token": m.csrf})
ok(visitor.get(f"/api/reviews/book/{out_id}").get_json() == {"count": 0}, "a rejected review is never shown")
ok(m.post(f"/api/admin/inbox/reviews/{rid}", json={"status": "bogus"}, headers={"X-CSRF-Token": m.csrf}).status_code == 400
   and m.post("/api/admin/inbox/reviews/987654321", json={"status": "approved"}, headers={"X-CSRF-Token": m.csrf}).status_code == 404,
   "bad status or unknown review refused")

# ---- search: in stock or not, one edition per work
sr = visitor.get("/api/reviews/search?q=" + out_title[:12]).get_json()["books"]
ok(any(b["book_id"] == out_id and b["available"] is False for b in sr), "the review search finds a book that is out of stock")
ok(all("barcode" not in b and "research_note" not in b for b in sr), "no stock or research bookkeeping reaches the visitor")
ok(visitor.get("/api/reviews/search?q=ا").get_json() == {"books": []}, "a one-letter search returns nothing")

# ---- shelf suggestions
r = visitor.post("/api/shelf/check", json={"title": title}).get_json()
ok(r["result"] == "available" and r["books"][0]["book_id"] == bid, "a book we have in stock: the visitor is told and sees its card")
r = visitor.post("/api/shelf/check", json={"title": out_title}).get_json()
ok(r["result"] == "out_of_stock" and r["book"]["book_id"] == out_id, "a book of ours out of stock: said so")
ok(visitor.post("/api/shelf/suggest", json={"book_id": bid}).status_code == 400, "a book on the shelf is not a candidate")
r = visitor.post("/api/shelf/suggest", json={"book_id": out_id, "asked_as": out_title})
ok(r.status_code == 201 and r.get_json()["new"], "«أبغاه يتوفر» keeps it as a candidate")
visitor.post("/api/shelf/suggest", json={"book_id": out_id})
ok(con.execute("select times_suggested, source from candidate_books where book_id=?", (out_id,)).fetchone() == (2, "catalog"),
   "the same book again counts, not a new row")
os.environ["LIBRARY_KEEPER_OUTSIDE"] = "0"
r = visitor.post("/api/shelf/check", json={"title": "كتاب لا وجود له أبدا زززز"}).get_json()
ok(r["result"] == "not_found", "a book found nowhere: the visitor's words are kept as a suggestion instead")
# a book we do not have, looked up before (no network in the test): the visitor is asked to confirm it
asked = "مدينة النحاس المفقودة"
k = OB.key_of(asked)
c2 = models.get_connection(); OB.ensure_table(c2)
c2.execute("INSERT OR REPLACE INTO outside_books (key, asked_title, status, title, author, year, description, sources, looked_at, enriched_at)"
           " VALUES (?,?,?,?,?,?,?,?,?,?)", (k, asked, "found", asked, "كاتب تجريبي", 2001, "رواية عن مدينة قديمة.",
                                            json.dumps(["https://ar.wikipedia.org/wiki/x"]), "2026-10-27T00:00:00Z", "2026-10-27T00:00:00Z"))
c2.commit(); c2.close()
os.environ["LIBRARY_KEEPER_OUTSIDE"] = "1"
r = visitor.post("/api/shelf/check", json={"title": asked}).get_json()
ok(r["result"] == "found_outside" and r["candidate"]["key"] == k and r["candidate"]["author"] == "كاتب تجريبي"
   and r["candidate"]["sources"] == ["ويكيبيديا"], "a book we do not have: what the lookup found is shown with its source, to confirm")
r = visitor.post("/api/shelf/check", json={"title": asked, "author": "شخص آخر تماما"}).get_json()
ok(r["result"] == "not_found", "a found book by another author than the one written is not offered")
os.environ["LIBRARY_KEEPER_OUTSIDE"] = "0"
ok(visitor.post("/api/shelf/suggest", json={"key": "لا يوجد مفتاح كهذا"}).status_code == 400, "an unknown lookup key is refused")
r = visitor.post("/api/shelf/suggest", json={"key": k, "asked_as": asked, "note": "ودي فيه"})
cand = con.execute("select source, title, author, year, summary, notes from candidate_books where outside_key=?", (k,)).fetchone()
ok(r.status_code == 201 and cand[:4] == ("outside", asked, "كاتب تجريبي", 2001) and cand[4] and json.loads(cand[5]) == ["ودي فيه"],
   "«نعم، هذا هو»: its card is kept as a candidate book")
d = m.get("/api/admin/inbox/candidates").get_json()
ok(d["counts"]["open"] == 2 and d["items"][0]["times_suggested"] == 2, "staff see the candidates, most asked first")
cid = d["items"][0]["candidate_id"]
ok(m.post(f"/api/admin/inbox/candidates/{cid}", json={"status": "added"}, headers={"X-CSRF-Token": m.csrf}).status_code == 200
   and m.get("/api/admin/inbox/candidates").get_json()["counts"]["added"] == 1, "«تم توفيره»")
body, counts = inbox_export.build("all", "test")
ok("## 11. مراجعات القرّاء" in body and "## 12. كتب رشّحها الزوار للرفوف" in body and asked in body and "سارة" not in body
   and counts["reviews"] == 2 and counts["candidates"] == 2, "the inbox file carries reviews and candidates, without first names")

# ---- «تفضيلات قرّاء مكتبة ثلاث»
ok(visitor.get("/api/reviews/status").get_json() == {"favorites_ready": False}, "not offered while few books have reviews")
res = A.run_chat([{"role": "user", "content": H.FAVORITES["ar"]}], active=False)
ok(res["books"] == [] and "آراء كافية" in res["reply"] and res["_debug"]["mode"] == "thalath_favorites", "tapped early: said plainly")
con.execute("update books set quantity=3 where book_id=?", (out_id,)); con.commit()
c3 = models.get_connection()
for i, b in enumerate(ids[2:2 + VR.READY_BOOKS]):
    c3.execute("INSERT INTO visitor_reviews (book_id, book_title, stars, tags, created_at, status, handled_at) VALUES (?,?,?,?,?,?,?)",
               (b, "x", 5 if i < 3 else 3, '["fun"]', "2026-10-27T00:00:00+00:00", "approved", "2026-10-27T00:00:01+00:00"))
c3.commit(); c3.close()
ok(visitor.get("/api/reviews/status").get_json() == {"favorites_ready": True}, "offered once enough books have reviews")
res = A.run_chat([{"role": "user", "content": H.FAVORITES["ar"]}], active=False)
got = [b["book_id"] for b in res["books"]]
ok(res["_debug"]["mode"] == "thalath_favorites" and len(got) == 4 and set(got) == set([bid] + ids[2:5])
   and all(b["reason"].startswith("قرّاء مكتبة ثلاث") for b in res["books"]), "the readers' highest rated books in stock, with their rating")
ok(H.FAVORITES_MORE["ar"] in res["suggestions"] and len(res["suggestions"]) >= 5, "«غيرها من تفضيلات القرّاء» and five helpers")
res2 = A.run_chat([{"role": "user", "content": H.FAVORITES["ar"]},
                   {"role": "assistant", "content": res["reply"], "book_ids": got},
                   {"role": "user", "content": H.FAVORITES_MORE["ar"]}], active=False)
ok(not set(b["book_id"] for b in res2["books"]) & set(got), "«غيرها»: the books already shown are not repeated")
en = A.run_chat([{"role": "user", "content": H.FAVORITES["en"]}], active=False, language="en")
ok(en["books"] and en["books"][0]["reason"].startswith("Thalath readers"), "the same in English")
ok("thalath_favorites" in C.INTERPRET_SCHEMA["properties"] and "thalath_favorites" in C.INTERPRET_SCHEMA["required"]
   and "منار" not in C.INTERPRET_SYSTEM, "the model reads the same wish in the visitor's own words")
_src = open(os.path.join(os.path.dirname(A.__file__), "app.py"), encoding="utf-8").read()
ok('if it.get("thalath_favorites"):' in _src, "the model's reading leads to the readers' favourites")

# ---- at least five helpers
ok(H.pad(["شي أقصر", "رشّحي لي رواية", "وش جو لاعب الشطرنج؟"], True) == ["شي أقصر", "رشّحي لي رواية", "وش جو لاعب الشطرنج؟", "ورّني غيرها", "شي أخف"],
   "three helpers become five, none repeating one already there in other words")
ok(len(H.pad(["للطفل", "للكبار"], False)) == 5 and H.pad([], False, none_on_purpose=True) == [] and len(H.pad([], True)) == 5
   and len(H.pad([], False)) == 5,   # QADFIX22 (cases 11.1, 28.1): only a start path that asks for a title stays without
   "a question's two answers become five; an answer with none on purpose stays without")
ok(H.FAVORITES["ar"] in H.pad(["شي أقصر"], True, "ar", favorites_ready=True) and H.FAVORITES["ar"] not in H.pad(["شي أقصر"], True, "ar"),
   "«تفضيلات قرّاء مكتبة ثلاث» is among the helpers only when ready")
ok(len(set(H.pad(["ورّني غيرها", "ورّني غيرها", "شي أخف"], True))) == len(H.pad(["ورّني غيرها", "ورّني غيرها", "شي أخف"], True)), "no duplicates")
fb = A.run_chat([{"role": "user", "content": "أبغى رواية"}], active=False)
ok(len(fb["suggestions"]) >= 5, "the keyword fallback's answer too has five helpers")
ok(all(len(ch) >= 5 for lang in SP.PATHS.values() for _, ch in lang.values() if ch), "every start path offers five answers")
ok("خمسة ردود قصيرة" in C.RESPOND_SYSTEM_AR and "five very short tap-ready replies" in C.RESPOND_SYSTEM_EN, "the model is asked for five")
ok("thalath_readers" in C.RESPOND_SYSTEM_AR and "thalath_readers" in C.RESPOND_SYSTEM_EN, "the model is told how to speak of the readers' reviews")

# ---- the library's own data
ROOT = os.path.join(os.path.dirname(os.path.abspath(A.__file__)), "..")
src = open(os.path.join(ROOT, "scripts", "make_release.py"), encoding="utf-8").read()
ok('"visitor_reviews", "candidate_books"' in src, "never in a release package")
snap = open(os.path.join(ROOT, "scripts", "catalog_snapshot.py"), encoding="utf-8").read()
ok("visitor_reviews" not in snap and "candidate_books" not in snap, "never in the catalog snapshot")
html = open(os.path.join(ROOT, "frontend", "index.html"), encoding="utf-8").read()
js = open(os.path.join(ROOT, "frontend", "app.js"), encoding="utf-8").read()
ok('id="readingBtn"' in html and "ماذا تقرأ الآن؟" in html and 'id="dReviews"' in html, "the start page and the book page carry it")
ok(all(f"'{k}'" in js for k in VR.TAGS), "every trait the server accepts is offered to the visitor")

# ---- AUDFIX 2026-10-27 (Codex audit of 2026-10-27, three P2 findings)
import llm
b_empty, b_fail = ids[12], ids[13]
_real_complete, _real_active = llm.complete_json, A.active_within_budget
secret = "رواية جميلة، أنا أسكن في حي النخيل شارع ١٢ عند بيت فلان الفلاني"
try:
    A.active_within_budget = lambda: "test"
    # 1) the cleaning took every word out: "" is respected, the original never comes back
    llm.complete_json = lambda *a, **k: {"clean_text": "", "offensive": False, "spoiler": False, "off_topic": True, "traits": []}
    pr = VR.process(secret, "test")
    ok(pr is not None and pr["clean_text"] == "" and "emptied" in pr["flags"] and "off_topic" in pr["flags"],
       "AUDFIX 1: an empty cleaning is a reading (\"\"), told to the staff, not «no reading»")
    r = visitor.post("/api/reviews", json={"book_id": b_empty, "stars": 4, "tags": ["fun"], "text": secret})
    rid_e = r.get_json()["review_id"]
    row = con.execute("select text, clean_text, ai_flags from visitor_reviews where review_id=?", (rid_e,)).fetchone()
    ok(r.status_code == 201 and row[0] == secret and not row[1] and "emptied" in json.loads(row[2]),
       "AUDFIX 1: the visitor's words are kept for staff only; the shown copy stays empty")
    m.post(f"/api/admin/inbox/reviews/{rid_e}", json={"status": "approved"}, headers={"X-CSRF-Token": m.csrf})
    s = visitor.get(f"/api/reviews/book/{b_empty}").get_json()
    ok(s["count"] == 1 and s["avg"] == 4 and s["reviews"] == [] and "النخيل" not in json.dumps(s, ensure_ascii=False),
       "AUDFIX 1: approved, its stars and traits count and the original words are not shown")
    f = A.book_fact(dict(A.get_book(b_empty)), None, "", "ar")
    ok("النخيل" not in json.dumps(f, ensure_ascii=False, default=str), "AUDFIX 1: nor do they reach the chat's facts")
    # a reading that keeps words is still shown as cleaned
    llm.complete_json = lambda *a, **k: {"clean_text": "رواية جميلة", "offensive": False, "spoiler": False, "off_topic": False, "traits": ["fun"]}
    r = visitor.post("/api/reviews", json={"book_id": b_fail, "stars": 4, "text": secret})
    ok(con.execute("select clean_text from visitor_reviews where review_id=?", (r.get_json()["review_id"],)).fetchone()[0] == "رواية جميلة",
       "a cleaned copy with words is what is shown")
    # 2) no reading (the model fails or answers without clean_text): the regex-scrubbed words, as before
    def boom(*a, **k): raise RuntimeError("down")
    llm.complete_json = boom
    ok(VR.process(secret, "test") is None, "the model failing is «no reading»")
    llm.complete_json = lambda *a, **k: {"offensive": False}
    ok(VR.process(secret, "test") is None, "an answer without clean_text is «no reading», not an empty one")
    r = visitor.post("/api/reviews", json={"book_id": b_fail, "stars": 3, "text": "حلوة، كلموني 0551234567"})
    row = con.execute("select clean_text from visitor_reviews where review_id=?", (r.get_json()["review_id"],)).fetchone()
    ok(row[0] and "حلوة" in row[0] and "0551234567" not in row[0], "without a reading the words are shown scrubbed")
finally:
    llm.complete_json, A.active_within_budget = _real_complete, _real_active

# 2) a first name: letters (and their marks) only, no digit of any script
for bad in ("١٢٣٤٥٦٧٨٩٠", "۱۲۳۴۵۶", "1234567890", "سارة٢", "سارة 0555", "٠٥٥١٢٣٤٥٦٧", "سارة؟", "𝟏𝟐𝟑", "①②③", "-سارة", "سا​رة"):
    ok(VR.clean_name(bad) is None, f"AUDFIX 2: «{bad}» is not kept as a name")
for good in ("سارة", "مُحَمَّد", "عبد الله", "Anne-Marie", "O'Neil", "زهراء"):
    ok(VR.clean_name(good) == good, f"AUDFIX 2: «{good}» is kept")
r = visitor.post("/api/reviews", json={"book_id": b_fail, "stars": 5, "text": "كتاب ممتع", "first_name": "١٢٣٤٥٦٧٨٩٠"})
ok(con.execute("select first_name from visitor_reviews where review_id=?", (r.get_json()["review_id"],)).fetchone()[0] is None,
   "AUDFIX 2: Arabic-Indic digits are not saved as a first name")
# a name an older version stored is checked again before it is shown
c4 = models.get_connection()
c4.execute("INSERT INTO visitor_reviews (book_id, book_title, stars, tags, text, clean_text, first_name, created_at, status, handled_at)"
           " VALUES (?,?,?,?,?,?,?,?,?,?)", (b_fail, "x", 5, "[]", "كتاب ممتع", "كتاب ممتع", "٠٥٥١٢٣٤٥٦٧",
                                             "2026-10-27T00:00:00+00:00", "approved", "2026-10-27T09:30:00+00:00"))
c4.commit(); c4.close()
s = visitor.get(f"/api/reviews/book/{b_fail}").get_json()
ok(s["reviews"] and all(q["name"] is None for q in s["reviews"]) and "٠٥٥١٢٣٤٥٦٧" not in json.dumps(s, ensure_ascii=False),
   "AUDFIX 2: a stored «name» of digits is not shown")

# 3) non-text items in the traits: dropped, never a 500
ok(VR.clean_tags(["fun", {}]) == ["fun"] and VR.clean_tags([None, ["x"], "fun", 3, {"a": 1}, "fun", "slow"]) == ["fun", "slow"]
   and VR.clean_tags({"fun": 1}) == [] and VR.clean_tags(None) == [], "AUDFIX 3: clean_tags keeps only known text traits")
n_before = con.execute("select count(*) from visitor_reviews").fetchone()[0]
r = visitor.post("/api/reviews", json={"book_id": b_fail, "stars": 4, "tags": ["fun", {}]})
ok(r.status_code == 201 and json.loads(con.execute("select tags from visitor_reviews where review_id=?", (r.get_json()["review_id"],)).fetchone()[0]) == ["fun"],
   "AUDFIX 3: tags [\"fun\", {}] -> saved with [\"fun\"], not a server error")
r = visitor.post("/api/reviews", json={"book_id": b_fail, "stars": 4, "tags": [[], None, {"k": "v"}]})
ok(r.status_code == 201 and con.execute("select count(*) from visitor_reviews").fetchone()[0] == n_before + 2,
   "AUDFIX 3: only non-text traits -> saved without traits, one row each")

# ---- AGY-20261027 (Antigravity on-demand audit, fixed by MGR at Ibrahim's request «شوف ملاحظات AGY واصلحها»)
_js = open(os.path.join(ROOT, "frontend", "app.js"), encoding="utf-8").read()
ok("if (currentSection !== 'reading') LEVEL.reading = LEVEL[currentSection] + 1;" in _js
   and "if (currentSection === 'reading') { LEVEL.bookDetail = LEVEL.reading + 1; LEVEL.feedback = LEVEL.bookDetail + 1; }" in _js,
   "AGY-01: «قرأته؟ شاركنا رأيك» is one step forward in the history, so Back returns to the book page")
ok("readingFromBook = false;\n    showReadingDone" not in _js, "AGY-01: after sending, Back still returns to the book it was opened from")
c4 = models.get_connection()
inactive = c4.execute("select book_id from books where active=1 and quantity>0 and book_id not in (%s) order by book_id desc limit 1" % ",".join(map(str, ids))).fetchone()[0]
c4.execute("INSERT INTO visitor_reviews (book_id, book_title, stars, tags, clean_text, created_at, status, handled_at) VALUES (?,?,?,?,?,?,?,?)",
           (inactive, "x", 5, "[]", "كلام عن كتاب مخفي", "2026-10-27T00:00:00+00:00", "approved", "2026-10-27T00:00:09+00:00"))
c4.execute("UPDATE books SET active=0 WHERE book_id=?", (inactive,)); c4.commit()
ok(all(r["book_id"] != inactive for rows in VR.by_work().values() for r in rows), "AGY-03: a hidden product's reviews are not used")
c4.execute("UPDATE books SET active=1 WHERE book_id=?", (inactive,)); c4.commit(); c4.close()
rid_h = visitor.post("/api/reviews", json={"book_id": bid, "stars": 3, "text": "نص مخفي للتجربة"}).get_json()["review_id"]
rid_r = visitor.post("/api/reviews", json={"book_id": bid, "stars": 2, "text": "نص مرفوض للتجربة"}).get_json()["review_id"]
m.post(f"/api/admin/inbox/reviews/{rid_h}", json={"status": "approved", "hide_text": True}, headers={"X-CSRF-Token": m.csrf})
m.post(f"/api/admin/inbox/reviews/{rid_r}", json={"status": "rejected"}, headers={"X-CSRF-Token": m.csrf})
body2, _ = inbox_export.build("all", "test")
ok("نص مخفي للتجربة" not in body2 and "نص مرفوض للتجربة" not in body2 and "فيه كلام لا يُصدَّر" in body2,
   "AGY-03: the inbox file carries no words of a hidden or rejected review")
ok(bool(re.search(r'EMPTY_TABLES = \[[^\]]*"outside_books"', src)), "AGY-04: the outside lookups of the Mac never go in a release package")
_vr = open(os.path.join(ROOT, "backend", "visitor_reviews.py"), encoding="utf-8").read()
ok(_vr.count("conn = _conn()") >= 7 and "get_connection()" in _vr.split("def _conn")[1].split("def ")[0], "AGY-03: every read and write makes sure the table exists")

# ---- «اعتماد الكل» (Ibrahim 2026-10-27: «في اعتماد اراء القراء اضف امكانية اعتماد الكل. احيانا يكون في اراء كثيرة»)
con.execute("update visitor_reviews set status='rejected' where status='pending'"); con.commit()
_new = [VR.add({"book_id": ids[5], "title": "t5"}, 5, ["fun"], "", None, "ar") for _ in range(3)]
_bad = VR.add({"book_id": ids[6], "title": "t6"}, 2, [], "نص", None, "ar", {"flags": ["spoiler"], "traits": [], "clean": "نص"})
ok(VR.approvable() == 3 and VR.counts()["pending"] == 4, "approve-all: three without a warning, one flagged")
ok(visitor.post("/api/admin/inbox/reviews/approve-all", json={}).status_code == 401, "approve-all: staff only")
ok(m.post("/api/admin/inbox/reviews/approve-all", json={}, headers={"X-CSRF-Token": "wrong"}).status_code in (400, 403)
   and VR.counts()["pending"] == 4, "approve-all: needs the CSRF token")
_d = m.get("/api/admin/inbox/reviews").get_json()
ok(_d.get("approvable") == 3, "approve-all: the list says how many can be approved at once")
_r = m.post("/api/admin/inbox/reviews/approve-all", json={}, headers={"X-CSRF-Token": m.csrf}).get_json()
ok(_r["approved"] == 3 and _r["left_flagged"] == 1 and _r["counts"]["pending"] == 1, "approve-all: the flagged review stays waiting")
_st = {r[0]: (r[1], r[2], r[3]) for r in con.execute("select review_id, status, hide_text, handled_by from visitor_reviews where review_id in (%s)" % ",".join(str(i) for i in _new + [_bad]))}
ok(all(_st[i][0] == "approved" and _st[i][1] == 0 and _st[i][2] for i in _new) and _st[_bad][0] == "pending",
   "approve-all: approved with their text, by the staff member")
ok(con.execute("select count(*) from audit_log where action='review.approved_all'").fetchone()[0] == 1, "approve-all: one audit row")
ok(m.post("/api/admin/inbox/reviews/approve-all", json={}, headers={"X-CSRF-Token": m.csrf}).get_json()["approved"] == 0,
   "approve-all: nothing left to approve the second time")
_adm = open(os.path.join(ROOT, "admin", "index.html"), encoding="utf-8").read()
ok("reviewsAll" in _adm and "تأكيد: اعتماد" in _adm and "'review.approved_all'" in _adm, "approve-all: the button asks a second tap first")

# ---- AUDFIX 2026-10-27 evening (Codex evening audit, P2): hiding a book took no effect on its reviews held in memory.
# Same process and same warm memory throughout: the test never clears VR._cache before checking.
_hb = ids[9]
_hbook = dict(A.get_book(_hb))
_c0 = visitor.get(f"/api/reviews/book/{_hb}").get_json().get("count", 0)
for _stars, _txt in ((5, "رأي أول عن كتاب سيُخفى"), (4, "رأي ثانٍ عن كتاب سيُخفى")):
    _rid = visitor.post("/api/reviews", json={"book_id": _hb, "stars": _stars, "text": _txt}).get_json()["review_id"]
    m.post(f"/api/admin/inbox/reviews/{_rid}", json={"status": "approved"})
_s = visitor.get(f"/api/reviews/book/{_hb}").get_json()
ok(_s.get("count") == _c0 + 2 and VR.work_key(_hbook) in VR.by_work(), "AUDFIX-E: the two approved reviews shown, memory warm")
_warm = VR._cache["stamp"]
ok(m.patch(f"/api/books/{_hb}", json={"active": 0}).status_code == 200
   and con.execute("select active from books where book_id=?", (_hb,)).fetchone()[0] == 0, "AUDFIX-E: hidden through the staff edit route")
_r = visitor.get(f"/api/reviews/book/{_hb}")
ok(_r.status_code == 404 and "سيُخفى" not in _r.get_data(as_text=True), "AUDFIX-E: the public review link of a hidden book says not found")
ok(VR.summary(_hbook) is None and all(r["book_id"] != _hb for rows in VR.by_work().values() for r in rows)
   and VR._cache["stamp"] != _warm, "AUDFIX-E: the memory follows the hiding at once (no restart, no new approval)")
ok("thalath_readers" not in A.book_fact(_hbook, None, "", "ar"), "AUDFIX-E: nor does the chat quote them")
ok(m.patch(f"/api/books/{_hb}", json={"active": 1}).status_code == 200
   and visitor.get(f"/api/reviews/book/{_hb}").get_json().get("count") == _c0 + 2, "AUDFIX-E: shown again once the book is shown again")
_new_title = (_hbook["title"] or "") + " — طبعة معدلة للتجربة"
ok(m.patch(f"/api/books/{_hb}", json={"title": _new_title}).status_code == 200
   and VR.summary(_hbook) is None and (VR.summary(dict(A.get_book(_hb))) or {}).get("count") == _c0 + 2,
   "AUDFIX-E: a new title regroups the reviews under the new work at once")
m.patch(f"/api/books/{_hb}", json={"title": _hbook["title"]})
ok((VR.summary(_hbook) or {}).get("count") == _c0 + 2, "AUDFIX-E: the old title back, the reviews back with it")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
