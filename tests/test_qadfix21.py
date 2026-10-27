"""[QADFIX21] The QAD round of 2026-10-26 (Ibrahim 2026-10-26 «اصلح الاخطاء»): 3 grave + 7 medium — an author named with a series
wish (7.1), a person the novel is about is not a second author (16.3), «الأعمال الروائية» is not a woman's book (45.2), a subject
beside «like his books» (11.2), «القطط» ~ «القط» (9.1), page count from the same author's copy of the work under another title
(10.1), a greeting is not a title (14.1), a publisher without «دار» (25), the prize's books when the asked one is out (22.1), a
parenting book's teenagers are not the reader (50.1)."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat as C, topics as T, author_flow as AF
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# --- 45.2: an adjective in a title is not «by a woman»
n = A.normalize
ok(not A._TITLE_WOMEN.search(n("الأعمال الروائية الكاملة")), "45.2: «الأعمال الروائية» is not a woman's book")
ok(not A._TITLE_WOMEN.search(n("الآلة الكاتبة")), "45.2: «الآلة الكاتبة» either")
ok(bool(A._TITLE_WOMEN.search(n("قصص لكاتبات سعوديات"))), "45.2: «كاتبات» in a title still is")
ok(bool(A._TITLE_WOMEN.search(n("مقامات النساء"))), "45.2: «النساء» still is (the author record decides against it)")

# --- 9.1: plural of a doubled root meets the singular with «ال»
ok(bool(T.stems("القطط") & T.stems("القط")), "9.1: «القطط» ~ «القط»")
ok(bool(T.stems("قطط") & T.stems("القط")), "9.1: «قطط» ~ «القط»")
ok(not T.stems("حب"), "9.1: a bare two-letter word stays out")

# --- 25: a publisher named without «دار»
con = models.get_connection()
avail = [dict(r) for r in A.list_books(only_active=False) if A.is_available(dict(r))]
bid = avail[0]["book_id"]
con.execute("UPDATE books SET publisher='المركز القومي للترجمة' WHERE book_id=?", (bid,))
con.commit(); con.close()
for fn in ("clear_cache", "invalidate_cache"):
    if hasattr(A, fn):
        getattr(A, fn)()
A._pub_cache["names"] = None
ab = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
fp = A.find_publisher([], "أقصد الكتب اللي نشرها المركز القومي للترجمة، مو عن تاريخه", ab)
ok(bool(fp) and fp[1] == "المركز القومي للترجمة", "25: «المركز القومي للترجمة» found in the message")
fp = A.find_publisher(["المركز القومي للترجمة"], "كتب المركز القومي للترجمة عن التاريخ", ab)
ok(bool(fp) and fp[1] == "المركز القومي للترجمة", "25: and among the wishes")
ok(A.find_publisher([], "روايات عن الحب في المدينة", ab) is None, "25: no publisher in an ordinary request")

# --- sources
_src = open(os.path.join(os.path.dirname(A.__file__), "app.py"), encoding="utf-8").read()
_af = open(os.path.join(os.path.dirname(A.__file__), "author_flow.py"), encoding="utf-8").read()
ok('author_request = dict(author_request, mode="works")' in _src and '(_pr.get("adaptation") or _pr.get("awards"))' in _src,
   "7.1: an author named with a series or prize wish is the book's author")
ok("(?:عن|حول|بخصوص|about|on)" in _af, "16.3: a person the book is about is not a second author")
ok("key(p) == key(written or '')" in _af, "16.3: a family name alone is looked up under the full name")
ok('q7["women"] = [x[2]["title"] for x in _wf[:4] if _g.get(x[2]["book_id"]) == "f"]' in _src, "45.2: «author records say» only of records")
ok("QADFIX21 (QAD 2026-10-26, case 11.2)" in _src and "topics.match(topic_phrases, b[\"book_id\"], topic_index)[0], b) for b in pool_before" in _src,
   "11.2: a subject beside a likeness is searched")
ok("_no_pages = " in _src, "10.1: the same author's copies under another title when the page count is missing")
ok("_greeting_only = bool(_gw and _gw[0] in keyword_prefs.GREETING_WORDS" in _src and "A greeting or pleasantry" in C.INTERPRET_SYSTEM, "14.1: a greeting is not a title")
ok("QADFIX21 (QAD 2026-10-26, case 22.1)" in _src, "22.1: the prize's books and the author's when the asked book is out")
ok('_aud_prefs = dict(it["prefs"], audience=None) if it.get("about_raising_child") is True' in _src, "50.1: a parenting book's teenagers are not the reader")

ok("_whole = chat_rules.title_key(nm)" in _src, "16.3: a whole title naming a person is matched whole")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
