"""[AUDFIX 2026-11-06] Codex audit P2: the language-copy swap (QADFIX28 / AUDFIX 2026-11-05) put another copy of the same work in
facts without the visitor's limits: «a novel under 150 pages and under 30 SAR» kept an Arabic copy of 100 pages / 20 SAR in the pool,
then the English copy of 500 pages / 100 SAR took its place, with the old reason («priced 20») and the guidance «every book in facts
is within it». The other copy now has to pass every hard filter the pool passed (price, pages, publisher …), and its reason quotes
its own price and pages. The audit's reproducer runs here on the real _run_chat_once with synthetic books (two copies of one work,
proven by the same Open Library work), a simulated interpreter and reply. No model, no network."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

base = [dict(b) for b in A.list_books(only_active=False)][:2]
I, J = base[0]["book_id"], base[1]["book_id"]
A.get_profile_cards = lambda: {}
A.book_author_map = lambda *a, **k: {I: [99999], J: [99999]}
A.embeddings.embed = lambda *a, **k: None
A.embeddings.search = lambda *a, **k: {}
A.embeddings.similar_to = lambda *a, **k: []
A.llm.complete_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected live-model call"))
A.chat.interpret = lambda *a, **k: {"intent": "recommend", "prefs": A.llm.clean_prefs({"genres": ["رواية"]}), "mentioned_titles": [],
                                    "title_variants": [], "wants_new_options": False, "references": [], "author_request": {}}
captured = []
def respond(conv, facts_json, guidance, *a, **k):
    facts = json.loads(facts_json); captured.append({"facts": facts, "guidance": guidance})
    return {"reply": "ok", "show_book_ids": [f["id"] for f in facts], "suggestions": [], "unanswered": ""}
A.chat.respond = respond

def run(en_pages, en_price, msg="Please recommend a novel under 150 pages and under 30 SAR."):
    books = [dict(b) for b in base]
    for b in books:
        b.update(quantity=3, active=1, genre="رواية", author="كاتب صناعي", original_year=2000, original_language="en",
                 original_year_ref="https://openlibrary.org/works/OL999999999W", summary="A synthetic novel for audit testing",
                 hook="Synthetic novel", keywords="", subjects="", series_name=None, series_position=None, goodreads_url=None)
    books[0].update(title="نسخة عربية صناعية قصيرة", language="ar", page_count=100, price=20)
    books[1].update(title="Synthetic Same Work English Copy", language="en", page_count=en_pages, price=en_price)
    A.list_books = lambda *a, **k: books
    captured.clear()
    res = A._run_chat_once([{"role": "user", "content": msg}], active=("synthetic", "no-network"), language="en")
    return res, (captured[0] if captured else {"facts": [], "guidance": ""})

# 1 the audit's case: the English copy breaks both limits → the Arabic copy stays (its copy language said)
res, cap = run(500, 100)
ids = [f["id"] for f in cap["facts"]]
ok(ids == [I], f"audit P2: the 500-page / 100 SAR English copy does not replace the 100-page / 20 SAR Arabic one (facts {ids})")
ok([b["book_id"] for b in res["books"]] == [I] and res["books"][0]["page_count"] == 100 and res["books"][0]["price"] == 20,
   "audit P2: the card under the reply is the copy within 150 pages and 30 SAR")
ok("own_language_copy28" not in (res.get("_debug") or {}), "no swap recorded")
ok("copy_language is Arabic" in cap["guidance"], "the English visitor is told our copy is in Arabic")

# 2 only the price is broken (120 pages, 100 SAR) → no swap either
res, cap = run(120, 100)
ok([f["id"] for f in cap["facts"]] == [I], "the price limit alone keeps the Arabic copy")
# 3 only the pages are broken (500 pages, 25 SAR) → no swap
res, cap = run(500, 25)
ok([f["id"] for f in cap["facts"]] == [I], "the page limit alone keeps the Arabic copy")

# 4 the English copy is within both (120 pages, 25 SAR) → it takes the place, with its own figures
res, cap = run(120, 25)
ok([f["id"] for f in cap["facts"]] == [J], "an English copy within both limits takes the Arabic copy's place")
ok([b["book_id"] for b in res["books"]] == [J], "its card is shown")
_why = " ".join(str(f.get("why_it_matches") or "") for f in cap["facts"])
ok("20 SAR" not in _why and "سعره 20 ريال" not in _why and "100 pages" not in _why and "عدد صفحاته ١٠٠" not in _why,
   f"the reason does not quote the Arabic copy's price or pages ({_why!r})")

# 5 without limits the QADFIX28 swap still happens (500 pages, 100 SAR)
res, cap = run(500, 100, "Please recommend a novel.")
ok([f["id"] for f in cap["facts"]] == [J], "no limit said: the copy in the visitor's language still takes the place (QADFIX28)")

# 6 _copy_reason1106
old, new = {"price": 20, "page_count": 100}, {"price": 25, "page_count": 120}
ok(A._copy_reason1106("اخترناه لأنه سعره 20 ريال", old, new) == "اخترناه لأنه سعره 25 ريال", "reason: Arabic price follows the copy")
ok(A._copy_reason1106(f"عدد صفحاته {A.fmt_num(100)}", old, new) == f"عدد صفحاته {A.fmt_num(120)}", "reason: Arabic pages follow the copy")
ok(A._copy_reason1106("100 pages", old, new) == "120 pages", "reason: English pages follow the copy")
ok(A._copy_reason1106("عن الحب كما طلبت", old, new) == "عن الحب كما طلبت", "reason without figures unchanged")
ok(A._copy_reason1106("", old, new) == "", "empty reason")
ok("all(h(o) for h in hard)" in open(A.__file__, encoding="utf-8").read(), "the swap checks the hard filters")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
