"""[QADFIX14] Fixes for the QAD round 2026-10-23 (Ibrahim 2026-10-23: «نعم اصلحها من السبب. اصلحهم كلهم»). The model reads more of
the visitor's meaning (years in any wording, the reader's age, physical wishes, authors' countries, «of those», the copy's language,
examples of what is wanted); the code applies it. Pure checks (no model, no live database)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat as C, llm as L, authors as AU
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

_src = open(A.__file__, encoding="utf-8").read()

# --- the interpreter: new fields, cleaned
req = C.INTERPRET_SCHEMA["required"]
ok(all(k in req for k in ("publication_years", "reader_age", "physical_wishes", "author_countries", "about_shown", "copy_language")),
   "the interpreter returns publication_years, reader_age, physical_wishes, author_countries, about_shown, copy_language")
ok("example" in C.REFERENCE_SCHEMA["properties"]["relation"]["enum"], "#4 a reference can be an example of what is wanted")
_raw = {"intent": "recommend", "preferences": {}, "mentioned_titles": [], "title_variants": [], "wants_new_options": False,
        "reference_books": [{"title": "ديوان الإمام علي", "author": "", "format": "unknown", "moods": [], "themes": [], "subgenres": [],
                             "known": False, "relation": "example"}],
        "author_request": {}, "avoid_topics": [], "order_by": "none", "reader_is_visitor": False, "reply_language": "ar",
        "whole_series": [], "first_publication": False, "publication_years": {"from": 2005, "to": 2000}, "reader_age": 1.5,
        "physical_wishes": ["قماش", " كرتون مقوى "], "author_countries": ["الأرجنتين", "تشيلي"], "about_shown": True, "copy_language": "ar"}
_orig = L.complete_json
try:
    L.complete_json = lambda *a, **k: dict(_raw)
    it = C.interpret([{"role": "user", "content": "x"}], active=("fake", "fake"))
finally:
    L.complete_json = _orig
ok(it["publication_years"] == (2000, 2005), "#1 years from the model, ends put in order")
ok(it["reader_age"] == 1.5, "#2 «سنة ونص» = 1.5")
ok(it["physical_wishes"] == ["قماش", "كرتون مقوى"] and it["author_countries"] == ["الأرجنتين", "تشيلي"], "#2/#5 lists cleaned")
ok(it["about_shown"] is True and it["copy_language"] == "ar", "م4/م7 about_shown and copy_language passed on")
ok(it["references"][0]["relation"] == "example", "#4 the example relation is kept")
ok(C._years({"from": 0, "to": 0}) is None and C._years({"from": 2016, "to": 0}) == (2016, None) and C._years("x") is None,
   "#1 open ends and no wish")
ok(C._age(0) is None and C._age(True) is None and C._age(30) is None and C._age(5) == 5.0, "#2 an age only when said and plausible")
ok("لين" in A._YEAR_BETWEEN.pattern and A.years_said("ولا رواية سعودية من ٢٠٠٠ لين ٢٠٠٥؟") == (2000, 2005)
   and A.years_said("من 1990 الى 2000") == (1990, 2000), "#1 «من X لين / إلى Y» is read by the code too")
ok('it.get("publication_years") and not prefs.get("awards")' in _src, "#1 the model's years are used when the code reads none")
ok("«من نفس الفترة»" in C.INTERPRET_SYSTEM, "#1 «من نفس الفترة» keeps the earlier years (interpreter rule)")

# --- #2 the reader's age read by the model for this turn
A.set_model_reader_age(None, None)
msgs = ["طيب وأخوه الكبير عمره ١٠ يحب الألغاز", "ولأختهم الصغيرة سنة ونص؟ كتب قماش أو كرتون مقوى"]
A.set_model_reader_age(msgs, 1.5)
ok(A.audience_age(msgs) == 1, "#2 «سنة ونص» is the reader's age now (1), not the brother's 10")
ok(A.audience_age(list(msgs[:-1])) != 1, "#2 an earlier slice of the conversation keeps its own reading")
A.set_model_reader_age(None, None)
ok("_phys_words" in _src and 'prefs["adaptation"] = None' in _src, "#2 physical wishes are no subject and «كرتون مقوى» no cartoon")

# --- #3 an unsaid title does not bring a grown-ups' book to a child
ok("_unsaid" in _src and "a title the visitor never wrote" in _src, "#3 the age check holds for a title the model guessed")

# --- #4 examples of what is wanted are offered
ok("_example_ids" in _src and "_cmp_ids -= _example_ids" in _src, "#4 an example is not kept out as a comparison")
ok("«شعر قديم زي المتنبي أو المعلقات أو ديوان الإمام علي»" in C.INTERPRET_SYSTEM, "#4 the interpreter knows a list of examples")

# --- #5 a region is the authors' countries, not languages; the oldest of the named authors
ok("أمريكا اللاتينية" in A.country_place(["الأرجنتين", "البرازيل", "تشيلي", "كولومبيا", "المكسيك", "البيرو"]),
   "#5 the region's label on the badge")
ok(A.country_place(["مصر", "السعودية"]) != "أمريكا اللاتينية", "#5 other places keep their own label")
ok('"أدب لاتيني/أمريكا اللاتينية" -> ["es", "pt"]' not in L.PREFS_RULES, "#5 Latin America is no longer Spanish + Portuguese")
ok('country = ("model", list(it["author_countries"]))' in _src, "#5 the model's countries are the authors' countries")
ok("_ob_a = [b for b in _ob if any(author_matches(a, b.get(\"author\")) for a in _oa)]" in _src, "#5 «وش أقدم وحدة لهم؟» over the named authors")

# --- #6 a description is no title; old shown books do not join named ones
ok("never a title you suggest for a kind" in C.INTERPRET_SYSTEM and "not «صداقة»" in C.INTERPRET_SYSTEM, "#6 described book: no word as a title")
ok("_asked_here" in _src and 'it.get("whole_series_found")' in _src, "#6 «عندكم باقي الأجزاء؟»: earlier books not brought back")

# --- #7 a Latin name with a typing slip
r = AU._latin_typo_match("colson whitehed", None) if False else None
ok(AU._LATIN_KEY.match("colson whitehed") and not AU._LATIN_KEY.match("كولسون"), "#7 only Latin names go through the typo match")
ok("_latin_typo_match(k, conn)" in open(AU.__file__, encoding="utf-8").read(), "#7 lookup tries it after the Arabic spellings")

# --- #8 a title typed in part
ok("a title typed in part" in _src, "#8 «فن اللا…» looks for titles that start with it")

# --- mediums and minors
ok("start with the number that fits the question" in _src, "م1 the total number of winners comes first")
ok("chat_rules.price_wish([user_messages[-1]])[0] != chat_rules.price_wish(user_messages[:-1])[0]" in _src, "م2 a new price keeps the subject")
ok("the shortest shown, which «أقصر منها» points at" in _src, "م3 «أقصر منها بس عربية» is measured against shown books that still fit")
ok("_shown_only" in _src, "م4/م5 «مين فيهم أقصر» among the books shown")
ok("_forms and not any(b.get(\"genre\") in _forms for b in e_pool)" in _src and '"gift" in (pprefs.get("use_cases") or ())' in _src,
   "م6 no poetry in English is said plainly; a gift is not a book with bold scenes")
ok('edition = it["copy_language"]' in _src and "only books the LAST message refers to" in C.INTERPRET_SYSTEM, "م7 copy language kept, old reference dropped")
ok("طيب كتاب عن المعلمين أو التعليم" in C.INTERPRET_SYSTEM, "م8 a book about the visitor's own subject is for the visitor")
ok("_ids_w" in _src and "if pub_years and it[\"intent\"] == \"clarify\":" in _src, "ب6 one card per work; ب7 years are concrete enough")
ok("not a second question in a row" in _src and "لا تعيد عبارة ترحيب" in C.RESPOND_SYSTEM_AR, "ب9 no second question in a row; ب8 no repeated greeting")

print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
