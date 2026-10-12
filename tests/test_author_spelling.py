"""Authors written in another spelling or in English; questions we hold no data for (2026-10-09)."""
import os, sys, sqlite3
for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LIBRARY_KEEPER_PROVIDER", "LIBRARY_KEEPER_MODEL", "LIBRARY_KEEPER_LLM"]:
    os.environ.pop(k, None)
sys.path.insert(0, os.getcwd())
import models, authors
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

models.init_db()
con = sqlite3.connect(models.DB_PATH)
def person(name, latin=None, aliases="[]"):
    aid = con.execute("insert into authors (name_ar, name_latin, aliases, research_status, created_at) values (?,?,?,'pending','x')",
                      (name, latin, aliases)).lastrowid
    con.commit()
    return aid
s = person("سايمون سينكاتست", "Simon Sinektest")
m = person("هاروكي موراكاميتست")
a1 = person("محمد العليتست")
x1 = person("فلان باترسونتست")
x2 = person("فلان باتيرسونتست")
authors._SPELL_CACHE["rows"] = None

def found(name):
    r = authors.lookup(name)
    return r and r["author_id"]

ok(found("سايمون سينكاتست") == s, "exact Arabic name")
ok(found("Simon Sinektest") == s, "English name finds the Arabic record")
ok(found("سيمون سينكاتست") == s, "a vowel letter less: same person («سيمون» / «سايمون»)")
ok(found("هاروكي موركاميتست") == m, "a vowel letter less in the family name")
ok(found("محمد الوليتست") is None, "a different consonant is another person, never matched silently")
ok(found("سيمون") is None, "one word alone is never matched by spelling")
ok(found("فلان باترسونتست") == x1 and found("فلان باتريسونتست") is None,
   "two people fit the spelling -> no match (the near-name question decides)")
ok(authors.lookup("سيمون سينكاتست", spelling=False) is None, "spelling=False keeps the exact behaviour")

# merging two records moves the dropped one's prizes (and drops exact duplicates)
import honors
k1 = person("مؤلف دمج أول")
k2 = person("مؤلف دمج ثاني")
U = "https://example.org/x"
honors.add("author_awards", k1, {"award_key": "nobel", "result": "winner", "year": 1976, "source_url": U}, "test")
honors.add("author_awards", k2, {"award_key": "nobel", "result": "winner", "year": 1976, "source_url": U}, "test")
honors.add("author_awards", k2, {"award_key": "pulitzer", "result": "winner", "year": 1976, "source_url": U}, "test")
authors.merge_authors(k1, k2)
rows = con.execute("select award_key from author_awards where author_id=? order by award_key", (k1,)).fetchall()
ok([r[0] for r in rows] == ["nobel", "pulitzer"], "merge moves prizes, duplicate dropped")
ok(con.execute("select count(*) from author_awards where author_id=?", (k2,)).fetchone()[0] == 0, "nothing left on the removed record")

# ---------- questions we hold no data for
import no_data, app as A
ok(no_data.asked("وش اكثر كتاب مبيعا في المكتبة") and no_data.asked("what is your best seller")
   and no_data.asked("وش الترند هالايام"), "sales / popularity questions are recognised")
ok(not no_data.asked("ابي كتاب عن المبيعات والتسويق") and not no_data.asked("أكثر رواية فازت بجائزة")
   and not no_data.asked("أبي كتاب فكري"), "a book about sales, a prize question, an ordinary wish are not")
r = A.run_chat([{"role": "user", "content": "وش اكثر رواية مبيعا عندكم"}], active=False)
ok(r["books"] == [] and "ما عندنا بيانات" in r["reply"] and r["reply"].endswith("فازت بجوائز مؤخرًا؟")
   and r["suggestions"][0] == "روايات فازت بجوائز مؤخرًا", "no data -> said once, a question, no books, matching chip")
conv = [{"role": "user", "content": "وش اكثر رواية مبيعا عندكم"}, {"role": "assistant", "content": r["reply"]},
        {"role": "user", "content": "ايه"}]
ok(no_data.followup(conv) == "روايات فازت بجوائز مؤخرًا", "«ايه» to the offer = the offer")
conv[-1]["content"] = "لا، ابي رواية قصيرة"
ok(no_data.followup(conv) is None, "«لا …» is a new wish, not the offer")
e = A.run_chat([{"role": "user", "content": "what are your best sellers?"}], active=False, language="en")
ok(e["books"] == [] and "sales figures" in e["reply"], "the same in English")

# ---------- subject words written another way: «تاريخ اللغة» finds «حول أصل اللغة» (staff chat 2026-10-11)
import topics
forms = topics.content_words("تاريخ اللغة")
ok(any("اصل" in f for f in forms) and any("نشاه" in f for f in forms), "«تاريخ» also means «أصل / نشأة» in a subject")
ok(any("تاريخ" in f for f in topics.content_words("أصل الإنسان")), "and the other way round")

print(f"\nTOTAL pass={P} fail={F}")
sys.exit(1 if F else 0)
