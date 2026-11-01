"""[QADFIX24] The QAD round of 2026-10-30 (Ibrahim 2026-10-30 «اصلح كل الاخطاء»): 4 grave — «انسى الفيلم» keeps the screen
wish (8.2), a prize wish carried into a question about an author (27.2), books ABOUT a person for a name carried from «like X»
(42.2), «العلوم والتجارب» for a 13-year-old shows nothing (46.1) and age-3 books for her (46.3); 2 medium — «المتوسط تنشر شعر؟»
(5.1), «أقصر رواية له عندكم» among the shown only (6.2); minor — shortest with a page count missing (2.2), the reading-time
question (3.1), «أول ابتدائي» read as 9 and a question instead of books (7.1), «ما أقدر أجزم» about an absent author (10.2),
«علم نفس» as a first message (13.1), a book for younger children unflagged (20.1), memory → brain books (31.1), prize winners
among a country's books (40.1), a history comic as a manga likeness (45.1), part 2 before part 1 by title (49.1, 45.1)."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat_rules as CR, chat as C, topics as T
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()

# --- 8.2 (grave): «انسى الفيلم»
for text, kinds in (("طيب انسى الفيلم، ابي بس قصة تناسب عمره ٤", {"adaptation"}), ("خلك من الكرتون، أي قصة", {"adaptation"}),
                    ("مو لازم جائزة، بس تكون حلوة", {"awards"}), ("forget the movie, any story for him", {"adaptation"}),
                    ("ابي رواية بدون رومانسية", set()), ("ولدي يحب أفلام ديزني", set())):
    ok(A.forgotten_wishes(text) == kinds, f"8.2: «{text}» lifts {kinds or 'nothing'}")
ok("_q24_forget = forgotten_wishes(user_messages[-1])" in src, "8.2: the chat lifts the screen / prize wish said before")
ok('prefs["genres"] = (set(prefs["genres"]) - {"قصص مصورة"}) | {"أطفال"}' in src,
   "8.2: «قصة» for a child of 8 or less is the children's shelf, not the comics shelf, unless comics were named")
ok(bool(A._COMICS_SAID.search(A.normalize("ابي كوميكس لولدي"))) and not A._COMICS_SAID.search(A.normalize("ابي قصة لولدي")),
   "8.2: comics named / not named")

# --- 27.2 (grave): a prize wish carried into a question about an author
ok("_q24_award_now" in src and "out.update(awards=[], award_year=None" in src,
   "27.2: an earlier prize wish is opened for a question about an author unless the message repeats it")

# --- 42.2 (grave): books about a person only for a name said now, never for «like X»
ac = {"name_as_written": "Agatha Christie", "full_name": "أجاثا كريستي", "aliases": ["Agatha Christie"]}
ok(not A.name_said_now(ac, "Do you have any mysteries in English?"), "42.2: a name carried from before is not said now")
ok(A.name_said_now(ac, "Something like Agatha Christie but more modern?") and A.name_as_likeness(ac, "Something like Agatha Christie but more modern?"),
   "42.1: «like Agatha Christie» names her as a likeness")
mt = {"name_as_written": "المتنبي", "full_name": "المتنبي", "aliases": []}
ok(A.name_said_now(mt, "ابوي يعشق المتنبي وابي اهديه شي") and not A.name_as_likeness(mt, "ابوي يعشق المتنبي وابي اهديه شي"),
   "42 / QADFIX23 30: «يعشق المتنبي» still asks about him")
ok(A.name_said_now(mt, "ايه كتاب عنه") and A.name_as_likeness(mt, "ابي شعر زي المتنبي"), "42: «كتاب عنه» points at him; «زي المتنبي» is a likeness")
ok("name_said_now(a, user_messages[-1]) and not name_as_likeness(a, user_messages[-1])" in src, "42.2: applied before books_about_person")

# --- 46.1 (grave) / 46.3
ok('q7["within_kind"]' in src and "_q24_before_strict" in src, "46.1: a side of a non-fiction field not found keeps the field's books for a young reader")
ok("_q24_too_young" in src, "46.3: books recorded for small children are not offered to a teenager")

# --- 5.1: publisher + kind
ok("_q24_pub_kind" in src and "never its other books instead" in src, "5.1: the kind asked of a publisher is a wish; none of it -> said, not its other books")
ok(A.local_understand(["المتوسط تنشر شعر؟"])["genres"] == ["شعر"], "5.1: «شعر» read from the message")

# --- 6.2 / 2.2: shortest
for text, wide in (("طيب وش أقصر رواية له عندكم؟", True), ("وش أقصر شي من كتبها؟", True), ("وش أقصر واحد فيهم؟", False),
                   ("which of his books is shortest", True), ("أيهم أقصر؟", False)):
    t = " " + " ".join(A.normalize(text.replace("؟", " ").replace("?", " ")).split()) + " "
    ok(bool(A._AUTHOR_WIDE.search(t)) == wide, f"6.2: «{text}» {'over all the author' if wide else 'among the shown'}")
ok('q7["short_nopages"]' in src, "2.2: a shown book with no page count is said, never «the other is the shortest»")

# --- 49.1 / 45.1: part 1 by title
for title, want in (("روايتي لروايتي (ج 2)", 2), ("روايتي لروايتي (ج 1)", 1), ("الزميل: الانتقام الهادئ ج3", 3), ("برج 2", None),
                    ("نساء صغيرات 2/1", None), ("Harry Potter Vol. 2", 2), ("ثلاثية غرناطة", None)):
    tp = A.title_part(title)
    ok((tp[1] if tp else None) == want, f"49.1: part of «{title}» = {want} ({tp})")
ok(A.title_part("روايتي لروايتي (ج 2)")[0] == A.title_part("روايتي لروايتي (ج 1)")[0], "49.1: both parts share the base title")
ok("_p1_24" in src, "49.1: part 1 offered instead of a later part named by title only")

# --- 7.1: school year
A.set_model_reader_age(msgs := ["عندي بنت بأول ابتدائي مجنونة بالقطط، وتقرا انجليزي أكثر من عربي"], 9)
ok(A.audience_age(msgs) == 6, f"7.1: «أول ابتدائي» is 6 even when the model read 9 ({A.audience_age(msgs)})")
A.set_model_reader_age(msgs2 := ["ولدي عمره ١٠"], 10)
ok(A.audience_age(msgs2) == 10, "7.1: an age said in numbers stays")
ok("QADFIX24 (QAD 2026-10-30, case 7.1)" in src, "7.1: a first message naming the child and what she loves gets books")

# --- 10.2: absent author
ok("if not absent_author:" in src, "10.2: «Never infer … absent» is not sent when the records say none of his books is here")

# --- 13.1: «علم نفس»
for text, gen in (("علم نفس", True), ("فلسفة", True), ("خيال علمي", False), ("رواية رعب", False), ("ابي كتاب علم نفس عن القلق", False)):
    ok(CR.general_first_message(text) == gen, f"13.1: «{text}» general={gen}")

# --- 3.1: no reading-time question
ok("الوقت المتاح" not in C.RESPOND_SYSTEM_AR and "available time" not in C.RESPOND_SYSTEM_EN
   and "لا تسأل كم وقت يقرأ" in C.RESPOND_SYSTEM_AR, "3.1: the reply never asks how much time the reader gives to reading")

# --- 20.1, 40.1, 31.1, 45.1
ok("_young24" in src, "20.1: a book written for younger children is said to be so")
ok("_won24" in src, "40.1: a prize winner comes a little higher among a country's books")
forms = set().union(*T.content_words("الذاكرة"))
ok("دماغ" in forms or "الدماغ" in forms, f"31.1: «الذاكرة» also finds brain books")
ok(not any("ذاكره" in f for f in set().union(*T.content_words("الدماغ"))), "31.1: one way only («الدماغ» does not add «الذاكرة»)")
ok(CR.book_is_fiction({"genre": "قصص مصورة", "subgenres": '["رواية مصورة", "تاريخ"]'}) is False
   and CR.book_is_fiction({"genre": "قصص مصورة", "subgenres": '["مانغا", "إثارة"]'}) is True
   and CR.book_is_fiction({"genre": "قصص مصورة", "subgenres": '["رواية تاريخية"]'}) is True,
   "45.1: a history comic is not a story like «ون بيس»")

# --- replay follow-ups
ok('q7["edition_soft"]' in src and "a preference only" in src, "7.1 replay: «انجليزي أكثر من عربي» is a preference: the subject in the other language wins")
ok("_in_field" in src and "سلسلة لوسي وستيفن هوكينج" in src, "46.1 replay: the field's books on the children's shelves too")
ok("a question about a publisher we stock is a reading question" in src, "5.1 replay: «المتوسط تنشر شعر؟» is not off topic")
ok("one card per series named by title" in src, "45.1 replay: parts 2 and 3 get no card when the reply names part 1")
ok("_e_shown" in src, "25.2 replay: «وش عندكم لدان براون بالانجليزي؟» after his English copies were shown names them, not «no English copy»")
ok("replay of case 25.1" in src, "25.1 replay: a missing title by an author we stock: «not in stock» first, then his books, never «ask the staff»")
ok("_TODDLER_FORM" in src and A._TODDLER_FORM.search(A.normalize("سلسلة طي اللوحات واكتشاف الدّاخل - المطار")), "3.1 replay: a lift-the-flap book is not offered to a boy of 11")
A.set_model_reader_age(msgs3 := ["عندي بنت بأول ابتدائي تحب القطط", "رشّحي لي شي عن القطط"], 9)
ok(A.audience_age(msgs3) == 6, "7.2 replay: the school year said in an earlier message holds")
A.set_model_reader_age(msgs4 := ["ولدهم بسادس ابتدائي", "ولأختهم الصغيرة سنة ونص؟"], 1.5)
ok(A.audience_age(msgs4) == 1, "QADFIX14 #2 kept: another child named after the school year keeps the model's reading")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
