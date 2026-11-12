"""[QADFIX33q3] Ibrahim 2026-11-12 on QADFIX33's question 3 («اكتفي بترتيب الكتب»): a reader of 16 or 17 — on the young-reader path
since QADFIX33 #2 — keeps QADFIX30's ordering only. When content is refused, adult books with no content record are NOT left out
for him (they come after the young readers' shelf, and the guidance says their content is not known). QADFIX30b's leaving out
stays for 12–15, an unsaid age, and the young readers' shelf named. (Questions 1 and 2 were answered «keep as now»: no change.)"""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()
ok("_rule30b = _yas30 or reader_age is None or reader_age <= 15" in src, "source: the leaving out holds for 12–15, an unsaid age or the shelf named")
ok("if _rule30b and sum(1 for s, _r, b in scored if s > 0 and _ya_shelf30(b)) >= 2:" in src,
   "source: the after-every-path re-check (QADFIX31 #3) follows the same bound")

base = [dict(b) for b in A.list_books(only_active=False)][:8]
A.get_profile_cards = lambda: {}
A.embeddings.embed = lambda *a, **k: None
A.embeddings.search = lambda *a, **k: {}
A.embeddings.similar_to = lambda *a, **k: []
A.llm.complete_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected live-model call"))
CUR = {}
def interp(*a, **k):
    it = {"intent": "recommend", "mentioned_titles": [], "title_variants": [], "wants_new_options": False, "references": [],
          "author_request": {}, "order_by": None, "about_shown": False, "reader_age": None}
    s = dict(CUR)
    it["prefs"] = A.llm.clean_prefs(s.pop("prefs", {}))
    it.update(s)
    return it
A.chat.interpret = interp
captured = []
def respond(conv, facts_json, guidance, *a, **k):
    facts = json.loads(facts_json); captured.append({"facts": facts, "guidance": guidance})
    return {"reply": "ok", "show_book_ids": [f["id"] for f in facts][:2], "suggestions": [], "unanswered": ""}
A.chat.respond = respond
_ids = [b["book_id"] for b in base]
books = []
for b, sp in zip([dict(x) for x in base], [
        dict(title="لغز البيت القديم", genre="يافعين", age_min=12, summary="مغامرة غامضة وحل لغز"),
        dict(title="سر المكتبة", genre="يافعين", age_min=13, summary="لغز غامض في مدرسة"),
        dict(title="جريمة في القطار", genre="رواية", summary="رواية غموض وتحقيق في جريمة"),
        dict(title="القاتل الصامت", genre="رواية", summary="رواية غموض بوليسية عن تحقيق"),
        dict(title="كتاب طبخ", genre="طبخ"), dict(title="ديوان", genre="شعر"),
        dict(title="تاريخ المدن", genre="تاريخ"), dict(title="رواية حب", genre="رواية")]):
    b.update(quantity=3, active=1, author="كاتب " + str(b["book_id"]), original_year=None, summary="", hook="", keywords="", subjects="",
             subgenres=None, series_name=None, series_position=None, goodreads_url=None, language="ar", age_min=None, age_max=None,
             page_count=200)
    b.update(sp)
    books.append(b)
A.list_books = lambda *a, **k: books
A.topics._cache.update(key=None)

def run(msg, age, prefs):
    CUR.clear(); CUR.update(prefs=prefs, reader_age=age)
    captured.clear()
    A._run_chat_once([{"role": "user", "content": msg}], active=("synthetic", "no-network"))
    return ([f["id"] for f in captured[0]["facts"]] if captured else []), (captured[0]["guidance"] if captured else "")

_p = {"genres": ["رواية"], "subgenres": ["غموض"], "audience": "young_adult", "avoid_content": ["violence"]}
_adult = {_ids[2], _ids[3]}
_f, _g = run("بنت أخوي ١٦ سنة تحب روايات الغموض، بس بدون عنف", 16, dict(_p))
ok(_f[:2] == [_ids[0], _ids[1]], f"16: the young readers' shelf mysteries first ({_f})")
ok(_adult & set(_f), f"16: adult mysteries with no content record are kept (ordering only), not left out ({_f})")
ok(all(_f.index(i) > _f.index(_ids[1]) for i in _adult & set(_f)), "16: they come after the shelf books")
ok("ما عندنا سجل محتوى" in _g or "content record" in _g or "not known" in _g, "16: the guidance says their content is not known")
_f, _g = run("ولدي ١٧ يحب القصص البوليسية وما يحب العنف", 17, dict(_p))
ok(_adult & set(_f), f"fresh 17: kept as well ({_f})")
_f, _g = run("بنتي ١٤ سنة تحب روايات الغموض، بس بدون عنف", 14, dict(_p))
ok(not (_adult & set(_f)), f"14: QADFIX30b still leaves them out ({_f})")
_f, _g = run("أبي رواية غموض لمراهق بدون عنف", None, dict(_p))
ok(not (_adult & set(_f)), f"age unsaid: QADFIX30b still leaves them out ({_f})")
_f, _g = run("بنت أخوي ١٦ تبي شي من رف اليافعين، غموض بدون عنف", 16, dict(_p))
ok(not (_adult & set(_f)), f"16 with the young readers' shelf named: left out as before QADFIX33 ({_f})")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
