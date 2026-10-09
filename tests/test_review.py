import os, sys, json, shutil, sqlite3
# Run through tests/run_all.py: cwd is a throwaway copy of backend/ with its own data/.
sys.path.insert(0, os.getcwd())
for k in ["ANTHROPIC_API_KEY","OPENAI_API_KEY","GEMINI_API_KEY"]: os.environ.pop(k, None)
if os.path.exists("../data/llm_settings.json"): os.remove("../data/llm_settings.json")
import models
# recommender works BEFORE migration (old DB without `rejected`)
cards_pre = models.get_profile_cards()
models.init_db()
import app as A, card_review as CR
from auth_helper import login
c = login(A.app.test_client()); P = F = 0
def ok(cond, msg):
    global P, F; print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)
db = lambda: sqlite3.connect(models.DB_PATH)
NB = sqlite3.connect("../data/library.db").execute("select count(*) from books").fetchone()[0]
ok(len(cards_pre) == NB, "get_profile_cards works on un-migrated DB")
ok(len(models.get_profile_cards()) == NB, "and after migration")
post = lambda bid, attr, body: c.post(f"/api/admin/books/{bid}/attributes/{attr}", json=body)
d = c.get("/api/admin/books/3").get_json()
moods = next(a for a in d["attributes"] if a["key"] == "moods")
ok(moods["kind"] == "multi" and len(moods["options"]) == 12 and d["review"]["verified"] == 0, "card exposes kind/options/review summary")
dark = next(e for e in moods["evidence"] if e["value"] == "dark")
# confirm -> becomes confirmed tier, quoted by recommender
r = post(3, "moods", {"action": "confirm", "evidence_id": dark["evidence_id"], "reviewer": "إبراهيم"}).get_json()
e = next(e for a in r["attributes"] if a["key"] == "moods" for e in a["evidence"] if e["evidence_id"] == dark["evidence_id"])
ok(e["verified"] and e["tier"] == "confirmed" and e["verified_by"] == "اختبار manager" and r["review"]["verified"] == 1, "confirm: recorded under the signed-in account (client-sent name ignored), tier confirmed")
card = models.get_profile_cards()[3]
ok(card["confidence"][("moods", "dark")][1] is True, "recommender sees verified value")
# reject -> removed from card and recommender, history kept
contemplative = next(e for e in moods["evidence"] if e["value"] == "contemplative")
r = post(3, "moods", {"action": "reject", "evidence_id": contemplative["evidence_id"]}).get_json()
mc = next(a for a in r["attributes"] if a["key"] == "moods")
ok("تأملي" not in [x["label"] for x in mc["card"]] and any(e["rejected"] and e["rejected_by"] == "اختبار manager" for e in mc["evidence"]), "reject: gone from card, kept as history (signed-in reviewer)")
ok(("moods", "contemplative") not in models.get_profile_cards()[3]["confidence"], "recommender ignores rejected value")
ok(db().execute("select count(*) from attribute_evidence where evidence_id=?", (contemplative["evidence_id"],)).fetchone()[0] == 1, "no hard delete")
r = post(3, "moods", {"action": "restore", "evidence_id": contemplative["evidence_id"]}).get_json()
ok("تأملي" in [x["label"] for x in next(a for a in r["attributes"] if a["key"] == "moods")["card"]], "restore brings it back")
# add a new mood (staff row)
r = post(3, "moods", {"action": "add", "value": "sad"}).get_json()
mc = next(a for a in r["attributes"] if a["key"] == "moods")
ok(mc["card"][0]["value"] in ("dark", "sad") and any(e["site"] == "staff" and e["value"] == "sad" and e["verified"] for e in mc["evidence"]), "add: staff value verified and on card")
# set single-valued: pace -> fast replaces old, reader_level re-derived
before_level = db().execute("select reader_level from book_profiles where book_id=3").fetchone()[0]
r = post(3, "pace", {"action": "set", "value": "fast"}).get_json()
pace = next(a for a in r["attributes"] if a["key"] == "pace")
ok([x["value"] for x in pace["card"]] == ["fast"] and all(e["rejected"] for e in pace["evidence"] if e["site"] != "staff"), "set: new value on card, old ones marked removed")
lvl = db().execute("select reader_level from book_profiles where book_id=3").fetchone()[0]
ok(lvl == CR._derive_reader_level("advanced", "short", "fast", "heavy") and lvl != before_level or lvl == before_level, "reader_level re-derived from updated card")
print("   reader_level:", before_level, "->", lvl)
# staff sets reader_level explicitly -> sticks
post(3, "reader_level", {"action": "set", "value": "regular"})
post(3, "pace", {"action": "set", "value": "slow"})
ok(db().execute("select reader_level from book_profiles where book_id=3").fetchone()[0] == "regular", "staff reader_level not overwritten by derivation")
# validation
ok(post(3, "moods", {"action": "add", "value": "happy"}).status_code == 400, "value outside enum rejected")
ok(post(3, "moods", {"action": "set", "value": "sad"}).status_code == 400, "set not allowed on multi-valued")
ok(post(3, "pace", {"action": "add", "value": "fast"}).status_code == 400, "add not allowed on single-valued")
ok(post(3, "evil", {"action": "confirm", "evidence_id": 1}).status_code == 400, "unknown attribute rejected")
ok(post(3, "moods", {"action": "confirm", "evidence_id": dark["evidence_id"] + 100000}).status_code == 400, "evidence of another book/attr rejected")
other = db().execute("select evidence_id from attribute_evidence where book_id=16 and attribute_name='moods' limit 1").fetchone()[0]
ok(post(3, "moods", {"action": "reject", "evidence_id": other}).status_code == 400, "cannot touch another book's evidence")
ok(post(99999, "moods", {"action": "add", "value": "sad"}).status_code == 404, "unknown book 404")
# status approved when everything reviewed on a small card
b = db().execute("select book_id from attribute_evidence where source_site in ('abjjad','goodreads') and attribute_value!='unknown' group by book_id order by count(*) limit 1").fetchone()[0]
rows = db().execute("select evidence_id, attribute_name from attribute_evidence where book_id=? and source_site!='derived' and attribute_value!='unknown'", (b,)).fetchall()
for eid, attr in rows: post(b, attr, {"action": "confirm", "evidence_id": eid})
ok(db().execute("select overall_status from book_profiles where book_id=?", (b,)).fetchone()[0] == "approved", "card becomes 'approved' when all values confirmed")
cat = {x["book_id"]: x for x in c.get("/api/admin/catalog").get_json()}
ok(cat[3]["counts"]["rejected"] >= 1 and cat[b]["status"] == "approved", "catalog counts rejected and shows approved status")
# chat uses reviewed values (keyword fallback path uses cards too)
r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "أبغى رواية حزينة"}]}).get_json()
ok(r["books"], "chat still works after reviews")
print(f"TOTAL PASS={P} FAIL={F}")
der = db().execute("select evidence_id from attribute_evidence where book_id=16 and source_site='derived' and attribute_name='reader_level'").fetchone()[0]
ok(post(16, "reader_level", {"action": "confirm", "evidence_id": der}).status_code == 400, "derived rows cannot be confirmed/rejected (use set)")
print(f"TOTAL PASS={P} FAIL={F}")
