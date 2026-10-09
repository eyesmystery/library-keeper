import os, sys, sqlite3
sys.path.insert(0, os.getcwd())
import models, app as A
from auth_helper import login
c = login(A.app.test_client())
ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg)
con = sqlite3.connect(models.DB_PATH)
def ev(bid, attr, val):
    return con.execute("select max(confidence), max(conflict_seen), max(human_verified) from attribute_evidence where book_id=? and attribute_name=? and attribute_value=? and conflict_seen=0", (bid, attr, val)).fetchone()
queries = ["أبغى رواية حزينة مؤثرة", "أبغى شي مريح ودافئ", "رواية مشوقة وغامضة", "كتاب ملهم يغير حياتي",
           "أبغى رواية رومانسية", "شي خفيف مع القهوة", "رواية مظلمة ثقيلة", "أبغى شي مضحك"]
checked = 0
for q in queries:
    r = c.post("/api/recommend", json={"query": q}).get_json()
    res = r["results"]
    ok(3 <= len(res) <= 4, f"3-4 results for «{q}»")
    print("   ", [(b["title"], b["reason"]) for b in res][:4])
    for b in res:
        for mood, label in A.MOOD_LABELS.items():
            if f"أجواؤه {A.MOOD_LABELS_FEM.get(mood, label)} كما طلبت" in b["reason"]:
                m = ev(b["book_id"], "moods", mood); checked += 1
                ok(m[0] is not None and (m[0] >= 0.85 or m[2]), f"quoted mood {mood} for {b['title']} is High & non-conflict")
# conflict rows are ignored by get_profile_cards
cards = models.get_profile_cards()
bad = 0
for bid, attr, val in con.execute("select book_id, attribute_name, attribute_value from attribute_evidence where conflict_seen=1 and human_verified=0"):
    other = con.execute("select 1 from attribute_evidence where book_id=? and attribute_name=? and attribute_value=? and conflict_seen=0", (bid, attr, val)).fetchone()
    if not other and (attr, val) in cards.get(bid, {}).get("confidence", {}):
        bad += 1
ok(bad == 0, "conflict-only values absent from recommender cards")
import json
ok(len(cards) == len(json.load(open("../data/seed_books.json"))), "every book has a card")
print("quoted-mood checks:", checked)
for q in queries:
    for b in c.post("/api/recommend", json={"query": q}).get_json()["results"]:
        assert b["reason"].strip() not in ("اخترناه لأنه .", ""), (q, b["title"])
        assert "أجواؤه حزين " not in b["reason"] and "أجواؤه حزين،" not in b["reason"]
print("PASS no empty reasons; feminine mood grammar")
