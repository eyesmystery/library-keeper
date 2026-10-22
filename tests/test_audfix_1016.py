"""[AUDFIX] Daily Codex audit 2026-10-16, P3: a page limit said as «أكثر من ١٠٠ صفحة» excludes a 100-page book, as
«أقل من ١٠٠» already does (fixed during the audit); «على الأقل ١٠٠» and «ما يتعدى ١٠٠» keep 100. Checked on
chat_rules.page_limit and on the books the chat offers (a book of 99, 100 and 101 pages, every other book outside).
"""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm, chat, chat_rules as R, app as A
from models import get_connection
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# ---------------------------------------------------------------- the words
for text, want in (("رواية أكثر من 100 صفحة", (None, 101)), ("ابي كتب اكثر من ١٠٠ صفحة", (None, 101)),
                   ("شي فوق 300 صفحة", (None, 301)), ("more than 100 pages", (None, 101)),
                   ("أقل من ١٠٠ صفحة", (99, None)), ("less than 100 pages", (99, None)),
                   ("على الأقل 100 صفحة", (None, 100)), ("at least 100 pages", (None, 100)),
                   ("ما يتعدى 100 صفحة", (100, None)), ("ما يتجاوز ٢٠٠ صفحة", (200, None))):
    got = R.page_limit([text])
    ok(got == want, f"«{text}» -> {got}, want {want}")

# ---------------------------------------------------------------- the books offered
seen = {}
def fake_respond(conv, facts_json, guidance, active, *a):
    seen["facts"] = json.loads(facts_json)
    return {"reply": "x", "show_book_ids": [f["id"] for f in seen["facts"]][:4], "suggestions": [], "unanswered": ""}
chat.respond = fake_respond
chat.interpret = lambda conv, active, *a: {"intent": "recommend", "prefs": llm.clean_prefs({}), "mentioned_titles": [],
                                          "title_variants": [], "wants_new_options": False, "references": [], "author_request": {}}
avail = [b for b in (dict(r) for r in A.list_books(only_active=False)) if A.is_available(b)]
b99, b100, b101 = (avail[i]["book_id"] for i in range(3))
def offered(msg, others):
    con = get_connection()
    con.execute("UPDATE books SET page_count=?", (others,))
    for bid, n in ((b99, 99), (b100, 100), (b101, 101)):
        con.execute("UPDATE books SET page_count=? WHERE book_id=?", (n, bid))
    con.commit(); con.close()
    for fn in ("clear_cache", "invalidate_cache"):
        if hasattr(A, fn):
            getattr(A, fn)()
    seen.pop("facts", None)
    A.run_chat([{"role": "user", "content": msg}], active=("fake", "m"), language="ar")
    return {f["id"] for f in seen.get("facts") or []} & {b99, b100, b101}

ok(offered("ابي كتب أكثر من 100 صفحة", 50) == {b101}, "«أكثر من ١٠٠ صفحة»: the 101-page book, not the 100-page one")
ok(offered("ابي كتب أقل من 100 صفحة", 500) == {b99}, "«أقل من ١٠٠ صفحة»: the 99-page book, not the 100-page one")
ok(offered("ابي كتب على الأقل 100 صفحة", 50) == {b100, b101}, "«على الأقل ١٠٠ صفحة»: 100 and 101")
ok(offered("ابي كتب ما تتعدى 100 صفحة", 500) == {b99, b100}, "«ما تتعدى ١٠٠ صفحة»: 99 and 100")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
