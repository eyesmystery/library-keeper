"""[QADFIX11] Fixes for the QAD round 2026-10-21 (Ibrahim 2026-10-21 10:2x «صححهم»). Pure checks (no model, no live database)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat_rules as R
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

_src = open(A.__file__, encoding="utf-8").read()
# ج1 the oldest keeps the origin wish
ok("and origin_allowed(b, prefs)]" in _src, "1.1 «أقدم رواية عربية» — the oldest among Arabic originals, not «دون كيخوته»")
ok(not A.origin_allowed({"language": "ar", "original_language": "es", "translator": "x"}, {"original_languages": {"ar"}}),
   "1.1 a Spanish original is not «عربية»")
# ج2 the series named whole + its parts file
_sp = json.load(open(os.path.join(os.path.dirname(A.__file__), "series_parts.json"), encoding="utf-8"))
_np = next((x for x in _sp["series"] if "رباعية نابولي" in x["names"]), None)
ok(bool(_np) and len(_np["parts"]) == 4 and _np.get("source", "").startswith("http"), "8.1 «رباعية نابولي»: four parts, with a source")
ok("its first part counts too" in _src, "8.1 «عندكم كلها؟» names the series: the anchor part counts")
# ج2 / 8.3 a one-word title is not the start of the title asked for
_bk = {1: {"book_id": 1, "title": "حكاية", "author": "غير معروف", "quantity": 3},
       2: {"book_id": 2, "title": "حكاية الطفلة الضائعة: صديقتي المذهلة 4", "author": "إيلينا فيرانتي", "quantity": 1}}
ok([b["book_id"] for b in A.match_titles(["حكاية الطفلة الضائعة"], _bk)] == [2], "8.3 «حكاية الطفلة الضائعة» is not «حكاية»")
ok("a word of the title asked for is not a child reader" in _src, "8.3 «الطفلة» in a title is not a child reader")
# م1 sleep
ok(bool(A._SLEEP_PROBLEM.search(" عندي ارق وما اعرف انام ")) and not A._BEDTIME.search("عندي ارق"), "7.1 «عندي أرق وما أعرف أنام» → «النوم»")
ok(bool(A._BEDTIME.search("قصه قبل النوم لبنتي")), "«قصة قبل النوم» is a time, not the subject")
# م2 likeness
ok(A.compared_title("أبي شي يشبه موسم الهجرة إلى الشمال بس أحدث", "موسم الهجرة إلى الشمال"), "11.1 «يشبه X»: X is not a card")
ok(A.compared_title("ابي رواية زي الخيميائي", "الخيميائي"), "«زي X»: X is not a card")
# م3 compound subject
ok(A.no_cut("البلوك تشين") and not A.no_cut("غزو الكويت") and not A.no_cut("العملات الرقمية"), "32.1 «البلوك تشين» is not cut to «البلوك»")
ok("core_hits_ok(" in _src, "32.1 a subject word found only as the author's name is no subject")
# م4 women
ok(not A.women_wish("شي ثاني مو لنفس الكاتبة"), "33.2 «مو لنفس الكاتبة» is not a wish for women authors")
ok(A.women_wish("أبي روايات لروائيات سعوديات") and A.women_wish("شعر لشاعرات خليجيات") and A.women_wish("ابي كتاب لكاتبة سعودية"),
   "women authors still asked for")
# م5 «إلا الروايات المترجمة»
ok(R.excluded_genres(["أي شي إلا الروايات المترجمة، أبي عربي أصلي"]) == [], "34.1 «إلا الروايات المترجمة» refuses no kind of book")
ok(R.excluded_genres(["أي شي إلا الروايات"]) == ["رواية"], "«إلا الروايات» still refuses novels")
ok(bool(A._NOT_TRANSLATED_KIND.search(" اي شي الا الروايات المترجمه ")) and 'prefs["translated"] = prefs.get("translated") or "no"' in _src,
   "34.1 → Arabic-original novels")
ok("_mt_shown = bool(it[\"mentioned_titles\"])".replace('\\"','"') in _src, "1.3 «الأقدم منها» over the shown books: still the oldest of the whole request")
ok("and len(f.split()) >= 2) for q in _quoted" in _src, "8.3 «حكاية الطفلة الضائعة» named in the reply is not a card for «حكاية»")
print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
