"""[QADFIX26] The QAD rounds of 2026-11-01 / 11-02 / 11-03 (Ibrahim 2026-11-03 «اصلحها»): content safety for a young reader who
wants a clean book (11-03 17.1–17.2: «هيبتا» for a girl in «ثاني متوسط» with «ما أقدر أؤكد خلوّها»; 10-25 3.2); «للطفل ولا
للكبار؟» with the child's age known (11-01 4.2 / 3.1, 11-02 2.2 / 22.2); a named title's copy under its other-language title in a
comparison (11-01 23.2 War and Peace, 11-02 3.1 Moby-Dick); «تقييم عندكم» for an author is not our readers' favourites (11-01 15.1);
«سير الخلفاء» (11-03 36.1) and «سقوط غرناطة» (11-01 32) reach their books; «أقصر منها» keeps the subject (11-03 6.3)."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat_rules as CR, topics as T
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()
N = A.normalize

def card(fmt="novel", flags=None, sev=None):
    return {"format": fmt, "confidence": {("sensitive_content", k): (v, False) for k, v in (flags or {}).items()},
            "severity": dict(sev or {})}

def book(genre="رواية", lo=None, hi=None):
    return {"book_id": 1, "genre": genre, "age_min": lo, "age_max": hi, "title": "x"}

# --- 1 (grave, 11-03 17.1–17.2; 10-25 3.2): a young reader + a clean wish is strict
for t in ("بنت أخوي في ثاني متوسط، تبي قصة حب خفيفة مافيها شي للكبار", "لا أبي شي مضمون يناسب عمرها",
          "بدون أي مشاهد للكبار، تكون نظيفة", "ما فيها مشاهد جريئة", "a clean romance for my 13 year old, nothing adult"):
    ok(A.clean_wish(t), f"17 / 3.2: «{t}» is a clean-book wish")
for t in ("ابي رواية للكبار", "رواية طويلة", "طيب بالإنجليزي فيه شي لليافعين يناسبها؟", "رواية بدون نهاية حزينة"):
    ok(not A.clean_wish(t), f"17: «{t}» is no clean-book wish")
ya = "young_adult"
strict = {A.CLEAN_YOUNG}
ok(not A.age_allowed(card(flags={"sexual_content": 0.6, "self_harm": 0.6}, sev={"sexual_content": 1}), ya, strict, book(), 13),
   "17.1: «هيبتا» (sexual_content 0.6, passing) is not shown for «مافيها شي للكبار»")
ok(not A.age_allowed(card(), ya, strict, book(), 13), "17.1: an adult-shelf novel with no flag («قابلني عند البحيرة») does not positively fit a 13-year-old")
ok(not A.age_allowed(card(flags={"violence": 0.65, "religious_controversy": 0.75}), ya, strict, book(), 13),
   "17.2: «في قلبي أنثى عبرية» (adult shelf) is not shown")
ok(A.age_allowed(card("young_adult"), ya, strict, book("يافعين", 12), 13), "17: a young readers' book with no flag (Everything, Everything 12+) is shown")
ok(A.age_allowed(card("children"), ya, strict, book("أطفال"), 13), "17: a children's book with no flag fits too")
ok(not A.age_allowed(card("young_adult", {"sexual_content": 0.3}), ya, strict, book("يافعين", 12), 13),
   "17: any sexual-content evidence, even low confidence, keeps a YA book out")
ok(not A.age_allowed(card("young_adult", {"self_harm": 0.65}, {"self_harm": 2}), ya, strict, book("يافعين", 12), 13),
   "17: a clear self-harm flag keeps a YA book out of a clean request")
ok(A.age_allowed(card("novel"), ya, strict, book("رواية", 12, 16), 13), "17: an adult-shelf book whose recorded age range covers 13 fits")
ok(not A.age_allowed(card("young_adult"), ya, strict, book("يافعين", 16), 13), "17: a 16+ YA book is not for 13")
ok(not A.age_allowed(card("children", {"sexual_content": 0.2}), "children", strict, book("أطفال"), 8), "children: any sexual evidence out")
# without a clean wish: QADFIX12 #7 kept (Ibrahim 2026-10-22: a teenager gets every book that fits, the reply warns)
ok(A.age_allowed(card(flags={"sexual_content": 0.65}), ya, set(), book(), 14), "teen, no clean wish: QADFIX12 #7 unchanged (warned, not hidden)")
ok(A.age_allowed(card(flags={"self_harm": 0.75, "violence": 0.8}), ya, set(), book(), 14), "teen: other flags stay warnings (QADFIX12 #7)")
ok(not A.age_allowed(card(flags={"sexual_content": 0.65}), ya, {"sexual_content"}, book(), 14), "teen: avoid sexual_content still keeps it out")
ok(A.age_allowed(card(flags={"sexual_content": 0.9}), None, set(), book(), None), "adult reader: unchanged")
ok("pprefs[\"avoid\"] = set(pprefs.get(\"avoid\") or ()) | {CLEAN_YOUNG}" in src and 'it["clean_young"] = True' in src,
   "17: the strict wish is set for a young reader (any message) or the interpreter's avoid sexual_content")
ok("cannot confirm it is clean, and never offer" in src and "ولا تقل أبدًا إنك ما تقدر تؤكد خلوّه" in src,
   "17: the reply never shows a book with «ما أقدر أؤكد خلوّه»; none left → said, young readers' shelf offered")
# AUDFIX (audit 2026-11-04 P2): the shelf is one helper, shared with the keyword fallback's «books like X»
ok('it["clean_young_shelf26"]' in src
   and 'clean_young_shelf(all_books, shown, cards, pprefs["audience"], pprefs.get("reader_age"), language)' in src
   and 'and clean_young_ok(cards.get(b["book_id"]), b, audience, age)' in src,
   "17: nothing fits → said plainly, and the young readers' shelf (same strict check) offered instead")
ok(A.SENSITIVE_LABELS[A.CLEAN_YOUNG] == "محتوى للكبار" and A.SENSITIVE_LABELS_EN[A.CLEAN_YOUNG] == "adult content", "17: the label «بدون محتوى للكبار»")

# --- 2 (grave, 11-01 4.2 / 3.1; 11-02 2.2 / 22.2): the child's age known → no «للطفل ولا للكبار؟»
ok(CR.grade_ages("بنت عمي بالمتوسط، شاطرة بالفيزياء") == [13], "3.1: «بالمتوسط» = 13")
ok(CR.grade_ages("ولدي بالثانوي") == [16] and CR.grade_ages("في المرحلة المتوسطة") == [13], "«بالثانوي» 16, «المرحلة المتوسطة» 13")
ok(CR.grade_ages("رواية بالمتوسط الطول") == [] and CR.grade_ages("حجم متوسط") == [], "no school stage in «متوسط الطول»")
ok(CR.grade_ages("أول متوسط") == [12] and CR.grade_ages("ثالث متوسط") == [14], "QADFIX24/25 kept: «أول متوسط» 12, «ثالث متوسط» 14")
ok(A.detect_audience(N("بنت عمي بالمتوسط، شاطرة بالفيزياء ونفسها تفهم الثقوب السوداء")) == "young_adult", "3.1: the reader is a teenager")
ok(A.audience_age(["ولدي في الصف الأول ابتدائي يبي قصص الأنبياء", "فيه قصة عن سيدنا يوسف بالذات؟"]) == 6, "4.2: the age 6 is carried")
ok(A.audience_age(["عندي بنت عمرها ثمان سنين تحب الحكايات قبل النوم", "وعن النبي موسى عليه السلام فيه شي يناسبها؟"]) == 8, "2.2: 8 carried")
ok('it["audience_carried26"] = _age26' in src and "_age26 <= 17" in src, "2.2 / 22.2 / 4.2: a known age settles the reader")
ok('not re.search(r" (?:انا|لي|لنفسي|نفسي|me|myself|for me) ", _lm44) and not said_for_adult(user_messages)' in src,
   "«لي / أنا / للكبار» in the latest message still makes the visitor the reader")

# --- 3 (grave, 11-01 23.2 / 11-02 3.1): the other-language copy of a named title in a comparison
ok("_unmatched26" in src and "not any(is_available(b) for b in asked_books) or _unmatched26" in src,
   "23.2 / 3.1: one title in stock no longer hides the other title's copy under another name")
ok("_variants26 = [_variants26[k] for k in _unmatched26]" in src, "3.1: one variant per title when given in order (no English copy of the found one)")

# --- 4 (grave): «تقييم عندكم» for an author (11-01 15.1); «سير الخلفاء» (11-03 36.1); «غرناطة» (11-01 32); «أقصر منها» (11-03 6.3)
ok('it["favorites_bypassed26"] = True' in src and "_named26 or (not _readers26" in src,
   "15.1: an author named, or «تقييم» with no word for our readers, is the ordinary rating path")
grp = T._SAME_AS.get(N("الخلفاء"), set())
ok(N("الخليفه") in grp and N("خلافته") in grp, "36.1: «الخلفاء» reaches «الخليفة» / «خلافته» (2586, 2572 summaries)")
ok(N("الاندلس") in T._SAME_AS.get(N("غرناطه"), set()), "32: «غرناطة» reaches «الأندلس»")
ok(N("غرناطه") not in T._SAME_AS.get(N("الاندلس"), set()) and N("قرطبه") not in T._SAME_AS.get(N("الاندلس"), set()),
   "32: one way — «الأندلس» is not narrowed to one city")
ok(any(N("الاندلس") in f for f in T.content_words("سقوط غرناطة")[-1:]), "32: «سقوط غرناطة» carries «الأندلس» among its forms")
ok("_keep26" in src and "if words and _keep26 and all(f & _keep26 for f in words):" in src,
   "6.3: the subject of the last real message is not stale after «شي ثاني» → «أقصر منها»")
ok(A.follow_up_only("شي ثاني") and A.follow_up_only("أقصر منها") and not A.follow_up_only("روايات تدور في الأندلس"),
   "6.3: «شي ثاني» / «أقصر منها» are follow-ups, «روايات تدور في الأندلس» names the subject")

ok("(_ref26 or _theme26)" in src and "يناسبها|يناسبه" in src,
   "2.2 / 4.2: only when the latest message points at the child or goes on with the child's kind of book")
ok("الانبياء" in A.CHILD_THEMES["دين"].split(), "4.2: «قصص الأنبياء» said for the child is the religious kind")
ok('it["hobby_dropped26"]' in src and "_gnow26" in src, "22.2: «طيب شي علمي» after «ما يحب إلا الكورة» drops the hobby as the subject")
ok("_vh = [b for b in match_titles([v], all_books, max_rank=1)" in src, "3.1: «Moby-Dick, or, The whale» is the title before its subtitle")

# --- medium
ok(A.note_gender("حائزة البوكر العربية؛ لغة كثيفة") == "f" and A.note_gender("حائز البوكر العربية 2024") == "m",
   "11-03 4 / 11-02 13.1: «حائزة البوكر» in the author record is a woman («حائز» a man)")
ok(A.note_gender("كاتبة سعودية") == "f" and A.note_gender("روائي عراقي") == "m", "QADFIX8 kept: note_gender")
ok(A.award_results_said("أبي رواية كتبتها امرأة وفازت هي نفسها بالبوكر العربية") == ["winner"], "13.1: «وفازت هي نفسها» = winner")
ok(A.award_results_said("وصلت القائمة القصيرة") == ["shortlist", "winner"], "QAD 10-15 kept: shortlist")
ok(A._genre_in_title({"genre": "فكر", "title": "النظريات المعاصرة في علم الاجتماع"}, {"علم اجتماع"}),
   "44 / 14.1: a «فكر» book titled «… علم الاجتماع» is a sociology book")
ok(not A._genre_in_title({"genre": "رواية", "title": "علم الاجتماع"}, {"علم اجتماع"})
   and not A._genre_in_title({"genre": "فكر", "title": "الاجتماع البشري"}, {"علم اجتماع"}), "44: not a novel, not «الاجتماع» alone")
ok(N("افلاطون") in T._SAME_AS.get(N("اليونانيه"), set()) and N("سقراط") in T._SAME_AS.get(N("اليونانيه"), set()),
   "11-02 10.1: «الفلسفة اليونانية» reaches the histories that speak of Socrates / Plato")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
