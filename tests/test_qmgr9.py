"""[MGR Q-MGR-9] The approved shelf genre is not undone by stale format evidence (Ibrahim 2026-10-18: «اعتمد تصحيح الكل»).
Run through tests/run_all.py (cwd is a throwaway copy of backend/ with its own data/)."""
import os, sys, json, sqlite3
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, card_review as CR
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)
con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
b = con.execute("""SELECT p.book_id FROM book_profiles p JOIN attribute_evidence e ON e.book_id = p.book_id
                   AND e.attribute_name = 'format' AND COALESCE(e.rejected, 0) = 0 LIMIT 1""").fetchone()[0]
# 1 staff edit of the shelf genre moves the format evidence with it, so a recompute keeps it
st = login(A.app.test_client(), role="staff")
r = st.patch(f"/api/books/{b}", json={"genre": "مقالات ونصوص"})
ok(r.status_code == 200, "staff edit of the genre accepted")
con.commit()
vals = {x[0] for x in con.execute("SELECT attribute_value FROM attribute_evidence WHERE book_id=? AND attribute_name='format' AND COALESCE(rejected,0)=0", (b,))}
ok(vals == {"essays"}, "#1 the format evidence follows the staff genre edit")
CR.recompute_card(con, b); con.commit()
f, g = con.execute("SELECT format, genres FROM book_profiles WHERE book_id=?", (b,)).fetchone()
ok(f == "essays", "#1 a later card recompute keeps the staff format (not the old evidence)")
ok(json.loads(g) == ["essays"], "#2 genres follow the format instead of being wiped to []")
# 3 no format evidence left: the profile keeps its format
con.execute("UPDATE attribute_evidence SET rejected = 1 WHERE book_id=? AND attribute_name='format'", (b,))
CR.recompute_card(con, b); con.commit()
f, g = con.execute("SELECT format, genres FROM book_profiles WHERE book_id=?", (b,)).fetchone()
ok(f == "essays" and json.loads(g) == ["essays"], "#3 with no format evidence the profile keeps its format (was set to None)")
con.close()
print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
