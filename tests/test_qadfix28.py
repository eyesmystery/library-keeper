
"""[QADFIX28] The minor errors of the QAD rounds 2026-11-04 / 11-05 and the three items QADFIX27 left (Ibrahim «صلح الاخطاء البسيطة»):
11-04 8.2 the one woman winner said plainly, alone; 11-05 5.1 the war event before the place / time; 11-04 11.3 «أقصر شوي» keeps the
shorter book on the subject shown before; English replies in Latin letters (11-04 14.2, 30.1; 11-05 14.2, 23, 50.1); no poetry of an
author said from his books in stock (11-05 22); «قبل الثلاثينات» (11-05 45.2); the Arabic copy for an Arabic visitor (11-05 13.2, 40.1);
the book about the subject before one that only mentions it (11-05 34.1); «بعيد عن …» is no subject (11-05 36.1); toddlers' books
after a known age (11-05 20.2); no «ما عندنا تصنيف باسم …» (11-04 3.2, 11-05 18.2); «your» is no subject (11-05 30.1)."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()

# --- 1 (11-04 8.2, left by QADFIX27): the one winner by a woman, said plainly and shown alone
ok('out.setdefault("won_ids", []).append(b["book_id"])' in src, "8.2: women_award_counts keeps the ids of the women's own winners")
ok('q7["only_won28"] = _wa27["won_ids"][0]' in src and "هي الوحيدة المتوفرة عندنا لكاتبة وفازت بالجائزة هي نفسها" in src,
   "8.2: «أبي بس اللي فازت فيها امرأة» → «it is the only one we have», no other novel of hers offered")
ok("ids = [q7[\"only_won28\"]]" in src, "8.2: the card of that winner alone")
ok("(?:ثاني|ثانيه|غير|غيرها|غيره|اخري|اخرى|كمان|بعد|another|other|else|more)" in src,
   "8.3 unchanged: «رواية ثانية … غير بريد الليل؟» still answered «لا، هي الوحيدة» with her other books labelled")

# --- 2 (11-05 5.1, left by QADFIX27): the war event before the place / time
wp = A.war_place28("رواية عن عائلة تنزح من بيتها أيام القصف في بيروت الثمانينات", ["النزوح", "بيروت"])
ok(wp and wp["city"] == "بيروت" and wp["country"] == "لبنان", "5.1: «القصف … في بيروت» is a war event in Lebanon")
ok(A.war_place28("رواية عن عائلة في بيروت الثمانينات") is None, "no war word: no event ranking («عائلة في بيروت»)")
ok(A.war_place28("رواية عن الحرب في سوريا")["country"] == "سوريا" and A.war_place28("a novel about the war in Lebanon")["country"] == "لبنان",
   "a country named alone, Arabic or English")
ok(A.war_place28("قصة تدور بين ملاجئ بيروت وقت الاقتتال في السبعينات")["city"] == "بيروت", "11-04 11.1: «ملاجئ … الاقتتال»")
def bk(i, title, summary="", hook="", keywords=""):
    return {"book_id": i, "title": title, "summary": summary, "hook": hook, "keywords": keywords, "subjects": "", "setting": ""}
_wp = dict(wp, decade=1980)
ok(A.war_place_fit28(bk(1, "شريد المنازل", "لا يعرف إلى أي طائفة ينتمي حين تندلع الحرب الأهلية."), _wp, {1: " | لبنان"}) == 2,
   "5.1: «شريد المنازل» (the civil war, a Lebanese author) is on the war there")
ok(A.war_place_fit28(bk(2, "حجر الضحك", "خليل لا يشبه رجال الميليشيات والمدينة تنهار. تتابع الرواية ما تفعله الحرب.", "شاب في بيروت الحرب."),
                     _wp, {}) == 1 and A.war_place_fit28(bk(2, "حجر الضحك", "ما تفعله الحرب بمن يقاومها", "بيروت"), _wp, {2: "لبنان"}) == 2,
   "a war and the city only = 1; with the author's country = 2")
ok(A.war_place_fit28(bk(3, "برتقال مر", "في شقة ببيروت تعدّ شابة أطباقًا ريفية"), _wp, {3: "لبنان"}) == 0,
   "5.1: «برتقال مر» (Beirut, no war) only shares the place")
ok(A.war_place_fit28(bk(4, "بيروت مدينة العالم", "حكاية عائلة بيروتية في القرن التاسع عشر تتوزعهم الحروب في لبنان"), _wp, {}) == 0,
   "a 19th-century family saga is not the war of the 1980s")
ok(A.war_place_fit28(bk(5, "x", "يخوض الأصغر حرب القرم ويشهد جبل لبنان"), dict(wp, decade=None), {}) == 0,
   "«حرب القرم» is another war, not the one asked about")
ok(A.war_place_close28(bk(6, "يا سلام", "شخصيات في بيروت في ظلال الحرب"), wp, "أيام القصف في بيروت") == 2
   and A.war_place_close28(bk(7, "x", "قصف بيروت"), wp, "أيام القصف في بيروت") == 3, "closeness: the city, then the visitor's own war word")
ok('scored = sorted(_sc28, key=lambda x: (_fit28.get(x[2]["book_id"]) == 2, _cl28.get(x[2]["book_id"], 0), x[0]), reverse=True)' in src,
   "5.1: the books on the war there come first, whatever the family / place words the others share")
ok('f"يتناول الحرب في {_wp28[\'place\']}"' in src and "الكتاب اللي يشارك المكان أو الزمن أو العائلة فقط يأتي بعدها" in src,
   "5.1: their reason and the guidance say so")

# --- 3 (11-04 11.3, left by QADFIX27): «أقصر شوي» keeps the shorter book on the subject shown before
ok("_keep_old28 = (_keep_np and chat_rules.shortest_asked(user_messages[-1]) == \"shorter\"" in src
   and "or (_keep_old28 and b[\"book_id\"] not in set(last_batch))]" in src,
   "11.3: the books shown before the last answer stay in the pool for a plain «shorter»")
ok("سبق عرضه للزائر في هذه المحادثة: اعرضه مرة ثانية" in src and "was shown to the visitor earlier in this conversation" in src,
   "11.3: the reply says it was shown before, not the shortest books of the shop («الشاب»)")

# --- 4 (11-04 14.2, 30.1; 11-05 14.2, 23, 50.1): an English reply in Latin letters
ok("def book_author_latin():" in src and 'fact["author_latin"] = _al28' in src, "the author's Latin name from the author records in an English fact")
ok("Write the whole reply in Latin letters, with no Arabic script" in src, "English reply: no Arabic script, the title rendered in English")
ok('_want28 = "en" if language == "en" else "ar"' in src and 'it.setdefault("own_language_copy28", []).append([f["id"], _o28["book_id"]])' in src,
   "30.1: «جنتلمان في موسكو» gives way to «A Gentleman in Moscow» (same author, same year) for an English visitor")
ok('if language == "en" and any(re.search(r"[' in src and "also when he asked for English copies and we have only Arabic ones" in src,
   "23.1 / 50.2 replay: Latin letters also when he asked for English copies and only Arabic ones are here")
_lat = A.book_author_latin()
ok(isinstance(_lat, dict) and all(not any("؀" <= ch <= "ۿ" for ch in v) for v in _lat.values()),
   "book_author_latin: Latin names only")

# --- 5 (11-05 22): no poetry of an author, from all his books in stock
ok("كلها من أنواع أخرى، وما فيها" in src and 'it["kind_none28"] = _kind28' in src
   and "(لا «ما أقدر أؤكد» ولا «اسأل الموظف»)" in src, "22.2: «ما عندكم ولا ديوان له؟» → «ما عندنا ديوان له متوفر الآن»")

# --- 6 (11-05 45.2): «قبل الثلاثينات»
ok(A.years_said("أقدم من كذا، قبل الثلاثينات") == (None, 1929), "45.2: «قبل الثلاثينات» = before 1930")
ok(A.years_said("روايات بعد الستينات") == (1970, None) and A.years_said("novels before the sixties") == (None, 1959),
   "«بعد الستينات» = from 1970; «before the sixties» = up to 1959")
ok(A.years_said("ابي روايات انكتبت في الستينات") == (1960, 1969), "QADFIX5 kept: «انكتبت في الستينات» = 1960–1969")
ok(A.decade_said("بسبب القصف في بيروت بالثمانينات") == (1980, 1989), "5.1 replay: «بالثمانينات» is the decade too")
ok("_yst27) or _WAR28.search(_yst27))):" in src, "5.1 replay: a decade said with a war event is the story's time, not a publication filter")
ok("|than these|than those|than that|than them|a bit|a little|bit)(?: |$)" in src and "|من اللي|شوي|شويه|شوية|شوي بعد" in src,
   "11.3 replay: «أقصر شوي» is a new search, not «which of the shown is shorter»")
ok("if it.get(\"first_publication\") and not q7.get(\"before_none28\"):" in src, "45.2 replay: «none before 1930» also when the model reads first publication")
ok('q7["before_none28"] = (pub_years[1] + 1, _req28[0]["title"], first_published(_req28[0]), [b["book_id"] for b in _req28[:4]])' in src
   and "ما عندنا منها شيء متوفر حسب سجلاتنا" in src, "45.2: «none before 1930; the oldest is «إبراهيم الكاتب» (1931)», the novel wish kept")
ok("ids = [i for i in ids if i in _bi28] or _bi28[:2]" in src, "45.2 replay: the cards are the oldest of his kind only (not «قادة الفكر»)")

# --- 7 (11-05 13.2, 40.1): the Arabic copy for a visitor writing in Arabic
ok("record, same first year) takes the other's place" in src,
   "40.1: «Chronicle of a Death Foretold» in English → «قصة موت معلن» in Arabic (same author, same year)")
ok("_has_ar28(x[2])" in src, "40.3 replay: «أقصر» — not the English copy of a work we have in Arabic")
ok("_arb28 = [x for x in _base if (x[2].get(\"language\") or \"ar\") == \"ar\" and _val(x[2])]" in src,
   "13.2: «أقصر» for an Arabic visitor among the Arabic copies")
ok("_gk28 = [x for x in _base if x[2].get(\"genre\") in _lg28]" in src, "13.2: «أقصر من كذا» after two novels stays a novel")

# --- 8 (11-05 34.1): the book about the subject first
ok('it["about_first28"]' in src and "ابدأ بها. والكتب اللي نبذتها تذكره فقط" in src,
   "34.1: «رؤية فنية معاصرة للخط العربي» before «أحجار شاهدية…» (its summary only mentions the script)")

# --- 9 (11-05 36.1): «بعيد عن الموت والفقد» is what he does not want
ok("(?:بعيد|بعيده|بعيدا|بعيدين|ابعد|بعيدة|بعيدًا) عن " in src, "36.1: «بعيد عن …» never becomes the subject")

# --- 10 (11-05 20.2, 11-04 3.2): toddlers' books after the books for a known age
ok(A._TODDLER28.search(" " + A.normalize("كلماتي الأولى - الحيوانات") + " ") and A._TODDLER28.search(" " + A.normalize("كتاب ألعاب الدمى... العد") + " ")
   and not A._TODDLER28.search(" " + A.normalize("ذئب يوسف عليه السلام") + " "), "20.2: toddlers' formats by title")
ok('it["too_young28"]' in src and "lo <= _age28 - 5 and (hi is None or hi < _age28)" in src,
   "20.2: «من أنا / حيوانات الغابة؟» (from age 1) after the books for a boy of 8")

# --- 11 (11-04 3.2, 11-05 18.2): no «ما عندنا تصنيف باسم العلوم للصغار»
ok("ما عندنا تصنيف بهذا الاسم للصغار، وهذه كتب أطفال" not in src and "ولا تتكلم عن تصنيفاتنا أو أسماء الرفوف" in src,
   "18.2: children's books on the subject, no word about our categories")

# --- 12 (11-05 30.1): «your» is no subject
ok(A.normalize("your") in A._FUNCTION28 and A.normalize("shelves") in A._FUNCTION28 and A.normalize("الحرب") not in A._FUNCTION28,
   "30.1: «your» / «shelves» are function words")
ok("unsupported = [u for u in unsupported if not (normalize(u).split() and all(w in _FUNCTION28 for w in normalize(u).split()))]" in src,
   "30.1: a wish of function words only is dropped (the prize books get their cards)")

# --- replay findings: an answer our records settle gets no second reading
import second_look as SL
ok('"firm28": bool(q7.get("before_none28") or q7.get("only_won28") or it.get("kind_none28")' in src
   and not SL.needed({"understanding": "ai", "reply": "ما عندنا رواية قبل ١٩٣٠", "_debug": {"intent": "recommend", "firm28": True}},
                     [{"role": "user", "content": "أقدم من كذا، قبل الثلاثينات"}]),
   "45.2 / 11.3 replays: «none before 1930», «the shortest on that war» are not read a second time")
import chat_rules as CR
_nf = "ما لقيت هنا كتابًا واضحًا عن تاريخ عمان القديم"
ok(bool(SL.needed({"understanding": "ai", "reply": _nf, "books": [], "_debug": {"intent": "recommend"}},
                  [{"role": "user", "content": "كتب عن تاريخ عمان القديم"}])) == bool(CR.says_not_found(_nf)) and CR.says_not_found(_nf),
   "the second look still runs otherwise")

# AUTHGENDER (2026-11-05): the gender word list knows «رسامة» (typo fixed) and the roles the gender pass found missing
ok([A.note_gender(t) for t in ("رسامة وكاتبة", "عالمة فيزياء", "ناقدة أدبية", "مؤرخة")] == ["f"] * 4
   and [A.note_gender(t) for t in ("أستاذ جامعي", "عالم اجتماع", "ناقد", "مستشرق ألماني")] == ["m"] * 4,
   "note_gender reads رسامة / عالمة / ناقدة / مؤرخة and أستاذ / عالم / ناقد / مستشرق")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
