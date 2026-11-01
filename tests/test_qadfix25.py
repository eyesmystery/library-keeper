"""[QADFIX25] The QAD round of 2026-10-31 (Ibrahim 2026-10-31 «طيب اصلح جميع الاخطاء»): 4 grave — «الأدب الإنجليزي الفيكتوري»
(49.1) and «كاتبة سعودية أخذت البوكر العربية» (2.1) emptied by a non-topic wish taken as a subject, a 15-year-old astronomy fan
given 3–12 books only (4), the Prophet's life for a boy in the third grade (43); 6 medium — second look narrowing «رواية حرب»
to WWI soldiers and «no war novel» (30), no book of its own for the Clarke prize (27.1), a new manager's gift (32.1), the age
of «الصف الثاني» lost on a new subject (19.2), a dropped part 1 leaving «و…» / «الكتابان» (20.2), «what is the full name?» after
a full name (25.2); minor — a later part first in the words (11.2), «موركامي» (22.1), a title's words setting فصحى (23.1), the
English-copies line twice (7.2), light philosophy (45.1), the Iliad for Greek myths (50.2)."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat_rules as CR, topics as T, author_flow as AF, honors as H, second_look as SL
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()
afsrc = open(AF.__file__, encoding="utf-8").read()

# --- 49.1 (grave): an era is years of first publication
ok(A.era_said("أبي شي من الأدب الإنجليزي الفيكتوري") == (1837, 1901), "49.1: «الفيكتوري» = 1837–1901")
ok(A.era_said("Victorian novels please") == (1837, 1901), "49.1: Victorian = 1837–1901")
ok(A.era_said("رواية من القرن التاسع عشر") == (1801, 1900) and A.era_said("شعر القرن العشرين") == (1901, 2000),
   "49.1: «القرن التاسع عشر» / «القرن العشرين»")
ok(A.era_said("رواية عن الملكة فكتوريا") is None and A.era_said("ابي رواية حلوة") is None, "49.1: no era in other words")
ok(A.years_said("أبي شي من الأدب الإنجليزي الفيكتوري") == (1837, 1901), "49.1: years_said reads the era")
ok('it["first_publication"] = True' in src and "era_said(_ysrc)" in src, "49.1: an era counts the work's first year, not our edition's")
ok("not era_said(u) and not _ERA_WORD.search" in src, "49.1: the era is not a subject searched in summaries")

# --- 2.1 (grave): «كاتبة» is the women-authors wish, not a subject
ok(A.women_wish("كاتبة من السعودية اخذت البوكر العربية، عندكم روايتها؟"), "2.1: «كاتبة …» is a wish for a woman author")
ok(not A.women_wish("شي ثاني مو لنفس الكاتبة"), "2.1 / QADFIX11 kept: «لنفس الكاتبة» is a reference")
ok("unsupported = [u for u in unsupported if not _PEOPLE_WOMEN.search(" in src, "2.1: women words leave the subjects")

# --- 4 (grave): a teenager gets the field's general books
ok("themed = themed + [b for b in pool if b not in themed and (b.get(\"genre\") or \"\").strip() == child_theme]" in src,
   "4: a teenager's pool keeps the field's general books beside the young readers' shelves")

# --- 43 (grave) / 19.2: a school year with «الصف»; «للصغار»; the Prophet's life for a child
ok(CR.grade_ages("كتاب عن السيرة النبوية يناسب ولد بالصف الثالث") == [8], "43.1: «بالصف الثالث» = 8")
ok(CR.grade_ages("بنتي بالصف الثاني تعشق الخيول") == [7], "19.1: «بالصف الثاني» = 7")
ok(CR.grade_ages("ولدي في الصف الأول المتوسط") == [12], "«الصف الأول المتوسط» = 12")
ok(CR.grade_ages("ثاني متوسط") == [13] and CR.grade_ages("بأولى ابتدائي") == [6], "QADFIX24 kept: «ثاني متوسط» 13, «بأولى ابتدائي» 6")
ok(CR.grade_ages("الصف الطويل في المطعم") == [], "no school year in «الصف الطويل»")
ok(A.detect_audience(A.normalize("كتاب يناسب ولد بالصف الثالث")) == "children", "43.1: a school year says the reader is a child")
ok(A.detect_audience(A.normalize("ابي قصص مصورة للصغار مو كتب كبار")) == "children", "43.2: «للصغار» says the reader is a child")
ok(A.child_said("ابي قصص مصورة للصغار مو كتب كبار"), "43.2: no «للطفل ولا للكبار؟» after «للصغار»")
ok('prefs["genres"] = (set(prefs["genres"]) - {"سيرة نبوية"}) | {"دين"}' in src, "43.1: the Prophet's life for a child is the children's religious shelf")
ok(A.audience_age(["كتاب عن السيرة النبوية يناسب ولد بالصف الثالث"]) == 8, "43.1: the age is 8")
ok(A.audience_age(["بنتي بالصف الثاني تعشق الخيول", "طيب وشي عن الفضاء بدال الخيول؟"]) == 7, "19.2: the age is kept on a new subject")

# --- 30 (medium): the second look keeps the visitor's breadth; a common word still finds books carrying it
ok("never narrows what the visitor said" in SL.SYSTEM and "جنود الحرب العالمية الأولى" in SL.SYSTEM, "30.1: second look never narrows «رواية حرب»")
ok("_common25" in src and "عنوانه أو موضوعاته تذكر «" in src, "30.2 / 45.1: «الحرب»، «فلسفة» put books carrying the word first")

# --- 27.1 (medium): the work an author's record names is that book's own prize; lists first when none won itself
ok(H._work_key("رحالة (رواية)") == H._work_key("رحالة"), "27.1: a bare form label in brackets is no part of the work")
ok(H._work_key("أولاد الغيتو (1): اسمي آدم") != H._work_key("أولاد الغيتو (3): رجل يشبهني"), "27.1: part 1 is not part 3")
ok(H._work_key("The Underground Railroad") == H._work_key("the underground railroad"), "27.1: case and punctuation")
ok('derived_from="author_awards"' in open(H.__file__, encoding="utf-8").read(), "27.1: honors.load links an author's prize naming the work")
ok("None of our books won this prize itself" in src and "ما عندنا كتاب فاز بهذه الجائزة هو نفسه" in src, "27.1: lists first; no work named from memory")

# --- 32.1 (medium): a practical wish gets the kind asked for first; leadership is management
ok("_k25" in src and '"practical_learning" in (pprefs.get("use_cases") or ())' in src, "32.1: the kind asked for first for a practical wish")
same = T._SAME_AS.get(A.normalize("الاداره"), set())
ok("leaders" in same and A.normalize("القياده") in same, "32.1: «الإدارة» reaches «القيادة» / Leaders")

# --- 20.2 (medium): an earlier part stays valid; a trimmed reply loses a leading «و»
ok("_vser25" in src and 'series_position"] < _vser25[_k]' in src, "20.2: an earlier part of a valid series stays valid")
ok('_keep[0] = re.sub(r"^\\s*و(?=«|[A-Za-z])", "", _keep[0])' in src, "20.2: no reply opening with «و» after a trimmed sentence")

# --- 25.2 (medium) / 22.1: author names
ok("QADFIX25 (QAD 2026-10-31, case 25.2)" in afsrc and "near = []" in afsrc, "25.2: a full name with a shared family name is not asked again")
ok("QADFIX25 (QAD 2026-10-31, case 22.1)" in afsrc and "_family_spelling(written, [_n0])" in afsrc, "22.1: one letter off one catalog author's family name: his books")
ok(AF._family_spelling("موركامي", ["هاروكي موراكامي"]), "22.1: «موركامي» / «موراكامي» are one family name")

# --- 11.2, 7.2, 23.1, 50.2: minor
ok("_ser25" in src and "جزء سابق من نفس سلسلة" in src, "11.2: the earlier part first in the words")
ok('not any("نسخ إنجليزية من كتبه" in (c.get("content") or "") for c in conv' in src, "7.2: the English-copies line once")
ok(CR.message_register("الرجل الذي كان يحب الكلاب متوفرة؟", ["الرجل الذي كان يحب الكلاب"]) is None,
   "23.1: the words of the title asked about are not the visitor's register")
ok(CR.message_register("هل لديكم كتب عن الفلسفة اليونانية؟") == "fusha", "CHATFIX30b kept: فصحى")
ok(CR.visitor_register(["الرجل الذي كان يحب الكلاب متوفرة؟"], titles=["الرجل الذي كان يحب الكلاب"]) == "dialect", "23.1: default dialect")
for a, b in (("الاساطير", "الالياذه"), ("اليونانيه", "طرواده"), ("اساطير", "هوميروس")):
    ok(A.normalize(b) in T._SAME_AS.get(A.normalize(a), set()), f"50.2: «{a}» reaches «{b}»")

# --- found in the model replays
ok(A.award_results_said("كاتبة من السعودية اخذت البوكر العربية") == ["winner"], "2.1 replay: «أخذت البوكر» = won it")
ok(A.award_results_said("وصلت القائمة القصيرة للبوكر") == ["shortlist", "winner"], "QAD 10-15 kept: «القائمة القصيرة»")
ok("register_msgs or user_messages" in src and "register_msgs=[str(m.get(\"content\")" in src,
   "2.1 replay: a second look's فصحى rewrite does not set the visitor's register")
ok('if child_theme and not any(f["available"] and f["id"] not in theme_score for f in facts)' in src,
   "4.1 replay: «كتب أطفال تتناول الموضوع» only when every book offered is from the young readers' shelves")

ok("arabic (?:novel|novels|story|stories|literature|poetry|fiction|novella)" in src, "14.1 replay: «a short Arabic novel» = written in Arabic")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
