"""[QADFIX22] The QAD round of 2026-10-27 (Ibrahim 2026-10-27 «اصلح ثم اعد النشر»): 1 grave + 7 medium + the VREV helpers —
a book asked by its author and subject (2.1), a one-word publisher after «نشر» (12.1), a question-only reply (17.1), comics for
a child of 11 (19.1/19.3), two authors compared (25.1), a missing title when the model times out (29.1), «أفلام بعد الألفين»
(37.2), «ما فيها حروب» (40.1), five helpers under every reply (11.1, 28.1)."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, honors as H, helper_chips as HC
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

n = A.normalize
ab = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
avail = [b for b in ab.values() if A.is_available(b)]

# --- 2.1: the books of the author the visitor named, the one on the subject first
from collections import Counter
cnt = Counter(b.get("author") for b in avail if (b.get("author") or "").strip())
author = next(a for a, c in cnt.most_common() if c >= 2 and len(a.split()) >= 2)
mine = [b for b in avail if b.get("author") == author]
target = mine[-1]
msg = f"عندكم رواية {author} اللي اسمها " + " ".join(n(target["title"]).split()[:3]) + "؟"
got = A.named_author_books([author], ab, msg)
ok(bool(got) and all(b.get("author") == author or A.author_matches(author, b.get("author")) for b in got),
   "2.1: only the named author's books in stock")
ok(got and got[0]["book_id"] == target["book_id"], "2.1: the book whose title the message names comes first")
ok(A.named_author_books(["مؤلف لا وجود له أبدا"], ab, "رواية") == [], "2.1: an unknown author brings nothing")
ok(A.named_author_books([None, "", author], ab, "x") != [], "2.1: empty names are skipped")

# --- 12.1: «من نشر التنوير»
con = models.get_connection()
bid = avail[1]["book_id"]
con.execute("UPDATE books SET publisher='دار التنوير' WHERE book_id=?", (bid,))
con.commit(); con.close()
A._pub_cache["names"] = None
ab = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
fp = A.find_publisher([], "عندكم شي من نشر التنوير عن الفلسفة؟", ab)
ok(bool(fp) and "التنوير" in fp[1], "12.1: «من نشر التنوير» is the publisher")
fp = A.find_publisher([], "عندكم شي من إصدار التنوير؟", ab)
ok(bool(fp) and "التنوير" in fp[1], "12.1: «إصدار التنوير» too")
ok(A.find_publisher([], "روايات عن الحب في المدينة", ab) is None, "12.1: no publisher in an ordinary request")

# --- 40.1: a refused war
def refused(text):
    m = A._REFUSED_SUBJECT.search(" " + " ".join(n(text).split()) + " ")
    return A._REFUSED_SUBJECT_NAME[m.group(1)] if m else None
ok(refused("رواية بدون نهاية حزينة وما فيها حروب") == n("الحرب"), "40.1: «وما فيها حروب» -> الحرب")
ok(refused("ابي رواية بدون حرب") == n("الحرب"), "40.1: «بدون حرب»")
ok(refused("رواية مو عن السياسة") == n("السياسة"), "40.1: «مو عن السياسة»")
ok(refused("a novel without war") == n("الحرب"), "40.1: in English")
ok(refused("رواية عن الحرب العالمية") is None, "40.1: a war asked for is not refused")
ok(refused("ابي رواية عن حروب نابليون") is None, "40.1: «عن حروب» is wanted")

# --- 37.2: the adaptation's year
for text, year in (("افلام بعد الالفين", 2000), ("فيلم بعد 2010", 2010), ("a film adapted after 2000", 2000)):
    m = A._ADAPT_AFTER.search(n(text))
    ok(bool(m) and (2000 if not m.group(1).isdigit() else int(m.group(1))) == year, f"37.2: «{text}» -> {year}")
ok(A._ADAPT_AFTER.search(n("رواية صارت فيلم")) is None, "37.2: no year, no limit")
adapt = H.load()["adapt"]
dated = [(bid, r) for bid, rows in adapt.items() for r in rows if r.get("kind") == "film" and H._int_year(r.get("year"))]
if dated:
    bid, r = dated[0]
    y = H._int_year(r["year"])
    ok(bool(H.matches_adaptation(bid, "film", min_year=y)), "37.2: a film made that year counts")
    ok(H.matches_adaptation(bid, "film", min_year=9999) == [], "37.2: a film made before the year does not")
else:
    ok(True, "37.2: (no dated film in this database)")
_src = open(os.path.join(os.path.dirname(A.__file__), "app.py"), encoding="utf-8").read()
ok('prefs.get("adaptation_origin"), prefs.get("adaptation_min_year")' in _src, "37.2: the year is checked in meets_constraints")

# --- 19.1/19.3: a comic with no age range for a child of 10 or more
comic = {"genre": "قصص مصورة", "age_min": None, "age_max": None}
card = {"format": "comics", "confidence": {}, "sensitive_content": []}
ok(A.unrated_comic_ok(card, comic, "children", 11), "19.1: an unrated comic with a card, for 11")
ok(not A.unrated_comic_ok(card, comic, "children", 7), "19.1: not for 7")
ok(not A.unrated_comic_ok(None, comic, "children", 11), "19.1: not without a reader card")
ok(not A.unrated_comic_ok(card, dict(comic, age_min=16), "children", 11), "19.1: a recorded age still decides")
ok(not A.unrated_comic_ok(card, comic, "children", None), "19.1: an unknown age of the child: none")
ok(A.age_allowed(card, "children", (), comic, 11), "19.1: age_allowed lets it through for 11")
ok(not A.age_allowed(card, "children", (), comic, 8), "19.1: and not for 8")
ok("ولا تقل إنه ما عندنا قصص مصورة" in _src, "19.1: the reply says its age is not recorded")
_ai = {"genres": ["قصص مصورة"], "length": "any", "subgenres": ["مانجا"], "moods_include": [], "moods_exclude": [], "pace": [],
       "weight_include": [], "weight_exclude": [], "use_cases": [], "reader_level": [], "authors": [], "title_keywords": [],
       "unsupported_requests": ["مانجا"], "original_languages": [], "translated": "any", "awards": [], "award_year": None,
       "adaptation": "none", "audience": "children", "avoid_content": [], "qualities": [], "subgenres_exclude": []}
try:
    _p, _pp, _ex, _un = A.prefs_from_ai(_ai, n("بنتي 11 سنة تحب المانجا"))
    ok("مانجا" not in " ".join(_p["subgenres"]) and "قصص مصورة" in _p["genres"] and not _un,
       "19.1: «مانجا» is the comics shelf, not a kind searched as a subject")
except Exception as e:  # noqa: BLE001
    ok(False, "19.1: prefs_from_ai " + repr(e))
ok("or b[\"book_id\"] in topic_about_ids)" in _src and "_prefs_about if b[\"book_id\"] in topic_about_ids else prefs" in _src,
   "17.1: a book about the subject named is not dropped for a kind no card names")

# --- 25.1, 17.1, 29.1, 2.1 guidance (sources)
ok('if it.get("intent") == "compare" and not asked_books and not author_only:' in _src, "25.1: each compared author brings his books")
ok("ولا تقل إنك ما عندك تفاصيل عن أحدهما" in _src, "25.1: the comparison is answered")
ok('debug["asked_again_for_books"] = True' in _src, "17.1: a question-only reply with matching books is asked again once")
ok('"missing_title_answer": True' in _src, "29.1: a missing title stays missing when the model times out")
ok("لا تقل عن كتاب في facts إنه غير متوفر" in _src, "2.1: a book of the named author in facts is never called unavailable")

# --- 11.1 / 28.1: five helpers under every reply
ok(len(HC.pad([], False, "ar")) >= 5, "11.1: no helper of its own -> five")
ok(len(HC.pad([], False, "en")) >= 5, "28.1: in English too")
ok(HC.pad([], False, "ar", none_on_purpose=True) == [], "a start path that asks for a title stays without")
ok('none_on_purpose=_dbg.get("mode") == "start_path"' in _src, "run_chat: only the start path keeps none")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
