"""[AUDFIX 2026-11-05] Codex audit P2: the language-copy swap of QADFIX28 took two works by the same author record with the same
first year as one work in two languages (a synthetic Arabic farming guide gave way to an unrelated English murder novel and kept the
old reason «عن الزراعة كما طلبت»). The swap now needs proof of the same work (app.same_work_copy). The audit's reproducer runs here on
the real replacement block (taken from the source by AST, as the audit did), with synthetic books on the prototype catalog; the
QADFIX28 pairs (A Gentleman in Moscow / «جنتلمان في موسكو», Chronicle of a Death Foretold / «قصة موت معلن») are rebuilt with the
fields of our records. No model, no network."""
import ast, json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()
tree = ast.parse(src)
fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "_run_chat_once")
node = next(x for x in ast.walk(fn) if isinstance(x, ast.If) and any(isinstance(y, ast.Name) and y.id == "_want28" for y in ast.walk(x))
            and isinstance(x.test, ast.BoolOp) and ast.unparse(x.test).startswith("not edition"))
block = compile(ast.Module(body=[node], type_ignores=[]), "language_copy_block", "exec")

base = [dict(b) for b in A.list_books(only_active=False)][:2]
I, J = base[0]["book_id"], base[1]["book_id"]
AUTH = {I: [99999], J: [99999]}
A.book_author_map = lambda *a, **k: AUTH
A._WORK_LINKS["titles"] = None

def pair(a, b):
    x, y = dict(base[0]), dict(base[1])
    for d in (x, y):
        d.update(quantity=3, active=1, original_year_ref=None, goodreads_url=None, original_language=None, original_year=2000)
    x.update(a); y.update(b)
    return x, y

def swap(x, y, language, reason=("عن الزراعة كما طلبت", "direct")):
    all_books = {x["book_id"]: x, y["book_id"]: y}
    ns = {"edition": None, "it": {}, "order_by": None, "language": language, "book_author_map": A.book_author_map,
          "facts": [{"id": x["book_id"], "title": x["title"]}], "all_books": all_books, "reason_by_id": {x["book_id"]: reason},
          "cards": {}, "pprefs": {}, "work_copies_other_titles": A.work_copies_other_titles, "is_available": A.is_available,
          "age_allowed": A.age_allowed, "book_fact": A.book_fact, "_cartoon_id": lambda x: False, "same_work_copy": A.same_work_copy}
    exec(block, ns)
    return ns

# --- 1 the audit's reproducer: two different works, same author record, same year 2000
g, m = pair(dict(title="دليل زراعة صناعي", author="كاتب صناعي", genre="علوم", language="ar",
                 summary="دليل علمي في الزراعة لا علاقة له بالروايات"),
            dict(title="An Unrelated Murder Mystery", author="Synthetic Author", genre="رواية", language="en",
                 summary="A fictional murder mystery unrelated to agriculture"))
ns = swap(g, m, "en")
ok([f["id"] for f in ns["facts"]] == [I] and "own_language_copy28" not in ns["it"],
   "audit P2: the Arabic farming guide stays for an English visitor; the English murder novel of the same author / year does not take its place")
ok(ns["reason_by_id"] == {I: ("عن الزراعة كما طلبت", "direct")} and J not in ns["reason_by_id"],
   "audit P2: the reason «عن الزراعة كما طلبت» stays with the guide, never given to the novel")
ok(not A.same_work_copy(g, m, AUTH) and not A.same_work_copy(m, g, AUTH), "same_work_copy: the same author and year alone is no proof")
ns = swap(m, g, "ar", ("A murder mystery as asked", "direct"))
ok([f["id"] for f in ns["facts"]] == [J] and "own_language_copy28" not in ns["it"], "the other way: an Arabic visitor keeps the English novel")
# two Wikidata works of the author in the same year are still two works
g2, m2 = pair(dict(g, original_year_ref="https://www.wikidata.org/wiki/Q111"), dict(m, original_year_ref="https://www.wikidata.org/wiki/Q222"))
ok("own_language_copy28" not in swap(g2, m2, "en")["it"], "different work records (Q111 / Q222): no swap")
# an edition-level reference shared by mistake (a library record, an ISBN page) is no work record
g3, m3 = pair(dict(g, original_year_ref="https://lccn.loc.gov/2013324443"), dict(m, original_year_ref="https://lccn.loc.gov/2013324443"))
ok("own_language_copy28" not in swap(g3, m3, "en")["it"] and A.work_records(g3) == set(),
   "a shared LCCN record («أثقل من رضوى» / «الصرخة» share one in our data) is no proof of one work")

# --- 2 QADFIX28's own cases, rebuilt with the fields of our records
gm, ga = pair(dict(title="A Gentleman in Moscow", author="Amor Towles", genre="رواية", language="en", original_year=2016, original_language="en",
                   original_year_ref="https://www.wikidata.org/wiki/Q65048798", goodreads_url="https://www.goodreads.com/book/show/62329671"),
              dict(title="جنتلمان في موسكو", author="أمور تاولز", genre="رواية", language="ar", original_year=2016, original_language="en",
                   original_year_ref="https://www.wikidata.org/wiki/Q65048798", goodreads_url="https://www.goodreads.com/book/show/52789006"))
ns = swap(ga, gm, "en", ("رواية كما طلبت", "direct"))
ok([f["id"] for f in ns["facts"]] == [I] and ns["it"].get("own_language_copy28") == [[J, I]],
   "QADFIX28 30.1 kept: «جنتلمان في موسكو» gives way to «A Gentleman in Moscow» for an English visitor (one Wikidata work)")
ok(ns["reason_by_id"].get(I) == ("رواية كما طلبت", "direct"), "the same work keeps its reason")
ch, cq = pair(dict(title="Chronicle of a Death Foretold", author="Gabriel García Márquez", genre="رواية", language="en", original_year=1981,
                   original_language="es", original_year_ref="https://www.wikidata.org/wiki/Q935935", goodreads_url="https://www.goodreads.com/book/show/23878"),
              dict(title="قصة موت معلن", author="غابرييل غارسيا ماركيز", genre="رواية", language="ar", original_year=1981, original_language="es",
                   original_year_ref="https://www.goodreads.com/book/isbn/9786144721506", goodreads_url="https://www.goodreads.com/book/show/57319419"))
ns = swap(ch, cq, "ar", ("رواية قصيرة كما طلبت", "direct"))
ok([f["id"] for f in ns["facts"]] == [J] and ns["it"].get("own_language_copy28") == [[I, J]],
   "QADFIX28 40.1 kept: «Chronicle of a Death Foretold» → «قصة موت معلن» for an Arabic visitor (backend/work_links.json, Wikidata Q935935)")
ok(A.work_records(cq) == {"wikidata.org/wiki/q935935"} and A.work_records(ch) == A.work_records(cq),
   "work_links.json: «قصة موت معلن» is the copy of the Wikidata work Q935935")
# Frankenstein: the English copy's first year comes from an Open Library work, the Arabic one from Wikidata; the link joins them
fe, fa = pair(dict(title="Frankenstein", language="en", original_year=1818, original_language="en",
                   original_year_ref="https://openlibrary.org/works/OL450063W"),
              dict(title="فرانكنشتاين", language="ar", original_year=1818, original_language="en",
                   original_year_ref="https://www.wikidata.org/wiki/Q150827"))
ok(A.same_work_copy(fe, fa, AUTH) and "own_language_copy28" in swap(fe, fa, "ar")["it"], "Frankenstein → «فرانكنشتاين» (Wikidata Q150827 via work_links)")
# a real false pair of the old test in our catalog: Camus, 1942, original language French — «The Stranger» and «أسطورة سيزيف»
cs, cm = pair(dict(title="The Stranger", author="Albert Camus", genre="رواية", language="en", original_year=1942, original_language="fr",
                   original_year_ref="https://www.wikidata.org/wiki/Q163297"),
              dict(title="أسطورة سيزيف", author="ألبير كامو", genre="فلسفة", language="ar", original_year=1942, original_language="fr",
                   original_year_ref="https://www.wikidata.org/wiki/Q1125903"))
ns = swap(cs, cm, "ar", ("رواية قصيرة كما طلبت", "direct"))
ok([f["id"] for f in ns["facts"]] == [I] and "own_language_copy28" not in ns["it"],
   "«The Stranger» stays for an Arabic visitor: «أسطورة سيزيف» (same author, 1942) is another work (Q163297 / Q1125903)")
cg = dict(cm, title="الغريب", genre="رواية", original_year_ref="https://www.wikidata.org/wiki/Q163297")
ok(swap(cs, cg, "ar")["it"].get("own_language_copy28") == [[I, J]], "… while «الغريب» (the same Wikidata work Q163297) does take its place")
_wl = json.load(open(os.path.join(os.path.dirname(A.__file__), "work_links.json"), encoding="utf-8"))
ok(_wl.get("format") == "work-links-1" and all(w.get("work") and w.get("source") and len(w.get("titles") or []) >= 2 for w in _wl["works"]),
   "every work link names its work record and its source")

# --- 3 same_work_copy: the other firm signs and the clashes
ok(A.same_work_copy(*pair(dict(title="X", language="en", goodreads_url="https://www.goodreads.com/book/show/77"),
                          dict(title="Y", language="ar", goodreads_url="https://www.goodreads.com/book/show/77/")), AUTH), "the same Goodreads book")
ok(A.same_work_copy(*pair(dict(title="الغريب", language="ar"), dict(title="الغريب", language="ar")), AUTH), "the same title (another edition)")
ok(A.same_work_copy(*pair(dict(title="X", original_year_ref="https://openlibrary.org/works/OL627081W"),
                          dict(title="Y", original_year_ref="https://openlibrary.org/works/OL627081W")), AUTH), "the same Open Library work")
ok(not A.same_work_copy(*pair(dict(title="تأملات حول المنفى (1)", original_year_ref="https://openlibrary.org/works/OL27388W"),
                              dict(title="تأملات حول المنفى (2)", original_year_ref="https://openlibrary.org/works/OL27388W")), AUTH),
   "parts 1 and 2 under one work record are two books")
ok(not A.same_work_copy(*pair(dict(title="X", original_year_ref="https://www.wikidata.org/wiki/Q5", original_language="en"),
                              dict(title="Y", original_year_ref="https://www.wikidata.org/wiki/Q5", original_language="tr")), AUTH),
   "a clash of original language: not one work")
ok(not A.same_work_copy(*pair(dict(title="الغريب"), dict(title="الغريب")), {I: [1], J: [2]}), "no shared author record: not one work")

# --- 4 the same proof where QADFIX28 drops an English copy of a work we have in Arabic («أقصر», 40.3 replay)
ok("and same_work_copy(b, o, _bao28) for o in work_copies_other_titles(b, all_books))" in src and "_has_ar28(x[2])" in src,
   "40.3: an English copy is dropped only for its proven Arabic copy, not for another Arabic book of the author that year")
ok("set(_ba28.get(o[\"book_id\"], [])) & set(_ba28.get(f[\"id\"], []))" not in src, "the author + year test is gone from the swap")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
