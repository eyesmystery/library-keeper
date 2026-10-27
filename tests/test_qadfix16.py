"""[QADFIX16] Root fixes for the QAD round of 2026-10-24 (Ibrahim 2026-10-24: «اصلح الاخطاء»). Behaviour checks with the
model's calls replaced (no model, no live database):
- 22.3 / 41.2: a visitor reading the book himself is a parent reader only when the book is about raising a child (the
  model reads it: about_raising_child), and then no word list overrides the model.
- 41.1: countries the visitor refuses for the authors (avoid_author_countries); a negated wish is never a subject.
- 25.2 / 24.2: a book the reply names only to rule it out (excluded_book_ids), or one this turn's checks dropped, is no card.
- 8.3: "Do you have any Iranian novelists?" is no name to ask for.
- 20.3: the library's children's shelf counts as such without a reader card or age range.
- 4.2: «الأقدم» among the shown books keeps a book with only our edition's year, said as such.
- 37.1 / 10.1: «فيلم تلفزيوني مو سينما» (adaptation tv), "Booker Prize" in English."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm as L, chat as C, app as A, chat_rules as R, honors as H
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# --- the interpreter and the responder carry the new readings
_req = C.INTERPRET_SCHEMA["required"]
ok("about_raising_child" in _req and "avoid_author_countries" in _req, "the interpreter reads about_raising_child and avoid_author_countries")
ok("excluded_book_ids" in C.RESPOND_SCHEMA["required"], "the responder says which books it names only to rule out")
ok("tv" in L.PREFS_SCHEMA["properties"]["adaptation"]["enum"], "«فيلم تلفزيوني مو سينما» has its own adaptation value (tv)")
ok("Booker Prize" in L.PREFS_RULES and "intl_booker" in L.PREFS_RULES, "the English «Booker Prize» is read as the British prize only")
ok('"tv": ("tv_film", "series")' in open(os.path.join(os.path.dirname(H.__file__), "honors.py"), encoding="utf-8").read(),
   "adaptation tv matches television films and series, not cinema films")

_raw = {"intent": "recommend", "preferences": {}, "mentioned_titles": [], "title_variants": [], "wants_new_options": False,
        "reference_books": [], "author_request": {}, "avoid_topics": [], "order_by": "none", "reader_is_visitor": False,
        "reply_language": "ar", "whole_series": [], "first_publication": False, "publication_years": {"from": 0, "to": 0},
        "reader_age": 0, "physical_wishes": [], "author_countries": [], "about_shown": False, "copy_language": "any",
        "about_raising_child": False, "avoid_author_countries": []}
_orig, _interp, _resp = L.complete_json, C.interpret, C.respond
def interpreted(raw):
    try:
        L.complete_json = lambda *a, **k: json.loads(json.dumps(raw))
        return _interp([{"role": "user", "content": "x"}], active=("fake", "fake"))
    finally:
        L.complete_json = _orig
it = interpreted(dict(_raw, about_raising_child=True, avoid_author_countries=["الولايات المتحدة"]))
ok(it["about_raising_child"] is True and it["avoid_author_countries"] == ["الولايات المتحدة"], "the cleaner keeps both readings")

try:
    L.complete_json = lambda *a, **k: {"reply": "أستبعد «س».", "show_book_ids": [1, 2], "suggestions": [], "unanswered": "",
                                       "excluded_book_ids": [2]}
    r = _resp([{"role": "user", "content": "x"}], "[]", "", ("fake", "fake"))
finally:
    L.complete_json = _orig
ok(r["show_book_ids"] == [1] and r["excluded_book_ids"] == [2], "a book the reply rules out is never among the books to show")

# --- the parent reader (22.3, 41.2)
R.set_model_parent(False, not_parent=True)
ok(not R.parent_reader(["أبي كتاب لي أنا كأم"]), "the model read another subject for the visitor himself: no word list makes him a parent reader")
R.set_model_parent(False)
ok(R.parent_reader(["أبي كتاب لي أنا كأم"]), "without that reading the word lists still work")
R.set_model_parent(True, not_parent=True)
ok(R.parent_reader(["رواية"]), "the model's parent reading wins over not_parent")
R.set_model_parent(False)

# --- the children's shelf without a card (20.3)
ok(A._age_range_ok({"genre": "أطفال", "age_min": None, "age_max": None, "title": "مجموعة التلوين"}, "children"),
   "a book on the children's shelf with no age range suits a child")
ok(not A._age_range_ok({"genre": "رواية", "age_min": None, "age_max": None, "title": "رواية"}, "children"),
   "a novel with no age range is still never offered to a child unchecked")
ok(A._age_range_ok({"genre": "يافعين", "age_min": None, "age_max": None, "title": "س"}, "young_adult"),
   "the young readers' shelf suits a teenager")

# --- the oldest by our edition's year (4.2)
ok(A.oldest_year_label({"original_year": 1987, "publication_year": 2020}) == "1987", "a recorded first year is said as it is")
_l = A.oldest_year_label({"original_year": None, "publication_year": 2000})
ok("طبعتنا 2000" in _l and "غير مسجلة" in _l, "only our edition's year: said as the edition's year")

# --- run_chat on the test copy
con = models.get_connection()
avail = [b for b in (dict(r) for r in A.list_books(only_active=False)) if A.is_available(b)]
ids = [b["book_id"] for b in avail[:6]]
for n, bid in enumerate(ids):
    con.execute("UPDATE books SET title=?, author=?, genre='رواية', quantity=2, language='ar' WHERE book_id=?",
                (f"رواية فحص {n} طويلة العنوان", f"كاتب فحص {n}", bid))
con.commit(); con.close()
for fn in ("clear_cache", "invalidate_cache"):
    if hasattr(A, fn):
        getattr(A, fn)()
seen = {}
def responder(reply, show, excluded=()):
    def fake(conv, facts_json, guidance, active, *a, **k):
        seen["facts"] = json.loads(facts_json); seen["guidance"] = guidance
        return {"reply": reply, "show_book_ids": list(show), "suggestions": [], "unanswered": "", "excluded_book_ids": list(excluded)}
    return fake
def use(raw):
    v = interpreted(raw)
    C.interpret = lambda conv, active, *a, **k: json.loads(json.dumps(v, default=list))

# 25.2: «أستبعد X» with X named in the reply: no card
use(dict(_raw, preferences={"genres": ["رواية"]}))
C.respond = responder("أستبعد «رواية فحص 0 طويلة العنوان» لأن فيها محتوى للكبار. و«رواية فحص 1 طويلة العنوان» تناسبه.", [ids[1]], [ids[0]])
out = A.run_chat([{"role": "user", "content": "رواية بوليسية"}, {"role": "assistant", "content": "…", "book_ids": [ids[0], ids[1]]},
                  {"role": "user", "content": "لا بدون محتوى كبار"}], active=("fake", "m"))
got = [b["book_id"] for b in out["books"]]
ok(ids[0] not in got and ids[1] in got, f"25.2: the book the reply rules out is no card, though named (got {got})")

# 22.3: «وأنا بعد أبي شي عن كرة القدم لي» after a child — no parenting rule
_msgs = [{"role": "user", "content": "ولدي عمره ١٠ يحب الكورة"}, {"role": "assistant", "content": "…", "book_ids": []},
         {"role": "user", "content": "طيب وأنا بعد أبي شي عن كرة القدم لي"}]
use(dict(_raw, reader_is_visitor=True, about_raising_child=False, preferences={"audience": "adult", "unsupported_requests": ["كرة القدم"]}))
C.respond = responder("…", [])
A.run_chat(_msgs, active=("fake", "m"))
ok("القارئ هو الكبير السائل نفسه" not in seen.get("guidance", ""), "22.3: a book for the father about football is not a parenting book")
use(dict(_raw, reader_is_visitor=True, about_raising_child=True, preferences={"audience": "adult", "unsupported_requests": ["التربية"]}))
A.run_chat(_msgs[:2] + [{"role": "user", "content": "أبي كتاب لي أنا عن تربيته"}], active=("fake", "m"))
ok("القارئ هو الكبير السائل نفسه" in seen.get("guidance", ""), "a book for the father about raising his son keeps the parenting rule")

# 41.2: a negated wish is no subject
use(dict(_raw, preferences={"genres": ["تطوير ذاتي"], "unsupported_requests": ["غير أمريكي"]}, avoid_author_countries=["الولايات المتحدة"]))
A.run_chat([{"role": "user", "content": "تطوير ذات بس مو أمريكي"}], active=("fake", "m"))
ok("غير أمريكي" not in seen.get("guidance", ""), "41: «غير أمريكي» is not searched as a subject")

# 8.3: "Do you have any Iranian novelists?" — no «What name?»
use(dict(_raw, reply_language="en", author_countries=["إيران"],
         author_request={"name_as_written": "", "full_name": "", "aliases": [], "mode": "list",
                         "style": {"known": False, "formats": [], "subgenres": [], "moods": [], "pace": "unknown", "length": "unknown", "themes": []}}))
C.respond = responder("Here are novels by Iranian authors.", [])
out = A.run_chat([{"role": "user", "content": "Do you have any Iranian novelists at all?"}], active=("fake", "m"), language="en")
ok(not (out.get("_debug") or {}).get("author_gate"), "8.3: a nationality is no name to ask for")

C.interpret, C.respond = _interp, _resp
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
