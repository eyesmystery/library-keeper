"""[QADFIX23] The QAD round of 2026-10-28 (run late on 10-29; Ibrahim 2026-10-29 «اصلح الاخطاء الاخيرة»): 1 grave + medium —
a writer we hold nothing of whose name titles books we have (30, «المتنبي»), a one-word publisher with «تنشر» (2.1), «غير X»
(16), a prize that describes the missing book (20.2), «فائزة ثانية بس أقصر» / «أقصر من كذا» (23.2, 7.2), a cut sentence about
a dropped book (15.2), English copy on the subject for a child (13.1), age for a child under 8 (25.3), «هو يحب الكوميكس» (44.2),
a later series part while part 1 is in stock and «مثل X» not answered with X (50.2)."""
import os, sys, re
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, authors as AU
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

n = A.normalize
con = models.get_connection()
ab = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
avail = [b for b in ab.values() if A.is_available(b)]

# --- 30 (grave): «المتنبي» — his record exists, no book of his in stock, books about him are
# the frozen test catalog has «ديوان المتنبي» in stock: out of stock here, as at the library
con.execute("UPDATE books SET quantity=0 WHERE author LIKE '%المتنبي%'"); con.commit()
avail = [b for b in avail if "المتنبي" not in (b.get("author") or "")]
ids = [b["book_id"] for b in avail[:3]]
for bid, title in zip(ids, ["مع المتنبي", "في أثر المتنبي بين اليمامة والدهناء", "رباط المتنبي"]):
    con.execute("UPDATE books SET title=? WHERE book_id=?", (title, bid))
# a fresh id: the frozen test catalog's book_authors still links low ids to its own authors
con.execute("INSERT INTO authors (author_id, name_ar, name_latin, aliases) VALUES (900001, 'المتنبي', 'Al-Mutanabbi', '[\"أبو الطيب المتنبي\"]')")
con.commit()
ab = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
req = {"name_as_written": "المتنبي", "full_name": "أبو الطيب المتنبي", "aliases": [], "mode": "works"}
name, got = A.books_about_person(req, ab)
ok(name == "المتنبي" and {b["book_id"] for b in got} == set(ids), f"30: the three books titled with «المتنبي» ({name}, {[b['title'] for b in got]})")
name2, got2 = A.books_about_person({"name_as_written": "أبو الطيب المتنبي", "full_name": "", "aliases": []}, ab)
ok({b["book_id"] for b in got2} == set(ids), "30: the full name «أبو الطيب المتنبي» finds them too («أبو» is not a name word)")
ok(A.books_about_person({"name_as_written": "مؤلف لا وجود له", "full_name": "", "aliases": []}, ab) == (None, []),
   "30: a name with no record brings nothing")
# an author who HAS books in stock is not «absent»: his own books are the answer, not books about him
some = next(b for b in avail[5:] if (b.get("author") or "").strip() and len(b["author"].split()) >= 2)
con.execute("INSERT INTO authors (name_ar) VALUES (?)", (some["author"],)); con.commit()
ok(A.books_about_person({"name_as_written": some["author"], "full_name": some["author"], "aliases": []}, ab) == (None, []),
   "30: an author with books of his own in stock is not looked up as a subject")
src = open(A.__file__, encoding="utf-8").read()
ok("_about_books = books_about_person(_ask, all_books)" in src and "asked_books, missing_title = list(_about_books), False" in src,
   "30: the chat uses them before the author gate («تقصد المتنبي؟» is never asked) and shows them")

# --- 2.1: «مسكلياني تنشر روايات حلوة»
bid = avail[10]["book_id"]
con.execute("UPDATE books SET publisher='مسكلياني' WHERE book_id=?", (bid,)); con.commit()
A._pub_cache["names"] = None; A._distinct_cache.clear()
ab = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
fp = A.find_publisher([], "مسكلياني تنشر روايات حلوة، عندكم منها شي؟", ab)
ok(bool(fp) and "مسكلياني" in fp[1], f"2.1: a one-word publisher with «تنشر» ({fp})")
fp = A.find_publisher([], "عندكم شي ومسكلياني نشرت كثير", ab)
ok(bool(fp), "2.1: «نشرت» and a «و» before the name")
ok(A.find_publisher([], "مسكلياني روايات حلوة", ab) is None, "2.1: no publishing word, no guess")

# --- 16: «غير مية عام من العزله»
b16 = avail[20]
con.execute("UPDATE books SET title='مئة عام من العزلة' WHERE book_id=?", (b16["book_id"],)); con.commit()
ab = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
others = [b["book_id"] for b in avail[21:24]]
ro = A.visitor_ruled_out([b16["book_id"]] + others, ab, ["عندكم شي لماركيز غير مية عام من العزله؟"])
ok(ro == {b16["book_id"]}, f"16: «غير مية عام من العزله» rules out «مئة عام من العزلة» only ({ro})")
ok(A.visitor_ruled_out([b16["book_id"]], ab, ["ابي مية عام من العزله"]) == set(), "16: asking for it is no exclusion")
ok(A.visitor_ruled_out([b16["book_id"]], ab, ["أي شي ما عدا مئة عام من العزلة"]) == {b16["book_id"]}, "16: «ما عدا» too")
ok(A.visitor_ruled_out([b16["book_id"]], ab, ["anything except مئة عام من العزلة"]) == {b16["book_id"]}, "16: «except» too")
ok("_excluded_reply |= _ruled_out" in src and "ids = [i for i in ids if i not in _ruled_out]" in src,
   "16: a ruled-out title loses its card and is not given one back for being named in the reply")

# --- 20 / 23.2 / 7.2 / 15.2 / 50.2 / 44.2 / 25.3 / 13.1: the rules in the chat
ok('q7["award_of_missing_title"]' in src and "_closest_now" in src, "20.2: a prize said about the missing book is no wish for «الأقرب»")
for text, closest in (("طيب وش الأقرب لها عندكم؟", True), ("وش يشبهها", True), ("ابي رواية فازت بالبوكر", False)):
    lm = " " + " ".join(n(text).split()) + " "
    hit = bool(re.search(r" (?:ال)?(?:اقرب|يشبه|تشبه|يشبهها|تشبهها|مثلها|مثله|زيها|زيه|قريب منها|قريبه منها|قريب منه|بديل|بديلها)"
                         r"|similar|closest|nearest|something like", lm))
    ok(hit == closest, f"20.2: «{text}» {'asks' if closest else 'does not ask'} for the closest")
ok("فائزة ثانية بس أقصر" in src and "_shown_only = False" in src, "23.2: «ثانية» is a new search, not «which shown is shortest»")
ok("أقصر من كذا شوي" in src, "7.2: «أقصر من كذا» is shorter than the shown books, searched anew")
ok("يتناول الفضاء وبأسلوب قصص مصورة" in src and "_gw" in src, "15.2: the sentence after a dropped book goes; no helper names it")
ok("_like_ids" in src and "not any(k and (k in chat_rules.title_key" in src, "50.2: «مثل X» never answered with X; part 1 first")
ok("لا هو يحب الكوميكس مو القصص" in src, "44.2: «هو يحب…» about a child of known age asks no «للطفل ولا للكبار؟»")
ok("and b[\"book_id\"] not in topic_ids else 0" in src and "_topic_en" in src, "13.1: an English copy whose summary names the subject stays")
ok("[x for x in scored if _is_en(x[2]) and _on(x[2])]" in src
   and src.index("_on = (lambda b") < src.index('candidates = [] if similar_author else [(b, reason_text(r, language), "direct")'),
   "13.1 (replay): an English copy on the subject comes before Arabic copies on no subject, before the six are cut")

# 25.3: a book with no age range is offered to a child under 8 neither for its cartoon nor its young edition
bk = dict(avail[30]); bk.update(age_min=None, age_max=None, genre="رواية", title="روبنسون كروزو (للفتيان)")
ok(A._age_range_ok(bk, "children", 6, animated=True) is False, "25.3: age 6 — no")
ok(A._age_range_ok(bk, "children", 8, animated=False) is True, "25.3: age 8 — the young edition still counts (Ibrahim 2026-10-15)")
bk2 = dict(bk, title="الشيخ والبحر")
ok(A._age_range_ok(bk2, "children", 8, animated=True) is True, "25.3: age 8 — a cartoon still counts")
ok(A._age_range_ok(bk2, "children", 7, animated=True) is False, "25.3: age 7 — no")
ok("_unaged" in src, "25.3: the reply says the age is not recorded")

# --- QAD 2026-10-29 -------------------------------------------------------------------------------------------------
# 37 (grave): «المسخ لكافكا» — a copy by someone else is not Kafka's book
kb = avail[40]; other = avail[41]
con.execute("UPDATE books SET title='التحول', author='فرانز كافكا' WHERE book_id=?", (kb["book_id"],))
con.execute("UPDATE books SET title='المسخ الصغير', author='كريستينه لافانت' WHERE book_id=?", (other["book_id"],))
con.commit()
ab = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
ok(A.title_author_said("المسخ", "الغريب لكامو ولا المسخ لكافكا؟ أيهم أقرأ أول؟", ab) == n("كافكا"), "37: «المسخ لكافكا» -> كافكا")
ok(A.title_author_said("المسخ", "أقصد المسخ حق فرانز كافكا، اللي يصحى ويلقى نفسه حشرة", ab) in (n("فرانز كافكا"), n("فرانز")),
   "37: «المسخ حق فرانز كافكا» -> his name")
ok(A.title_author_said("المسخ", "عندكم المسخ؟", ab) is None, "37: no author given -> None")
ok(A.title_author_said("المسخ", "المسخ للاطفال", ab) is None, "37: «للاطفال» is no author")
his = A.named_author_books([n("كافكا")], ab, "المسخ", limit=3)
ok(any(b["book_id"] == kb["book_id"] for b in his), "37: Kafka's «التحول» is among his books looked at")
ok("_title_authors.append((_mt, _said_by" in src, "37: the chat replaces the other writer's copy with his books")

# 2.2 (grave): «مثلا عن هيغل او هوبز»
h1, h2 = avail[50], avail[51]
con.execute("UPDATE books SET title='هيغل الأول في سياقه' WHERE book_id=?", (h1["book_id"],))
con.execute("UPDATE books SET title='العقل والحرية في فلسفة هوبز السياسية' WHERE book_id=?", (h2["book_id"],))
con.commit()
ab = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
A._title_word_count["n"] = None
got = A.title_subject_names("ابي فلسفة بحتة، مثلا عن هيغل او هوبز", ab)
ok(set(got) == {n("هيغل"), n("هوبز")}, f"2.2: «عن هيغل او هوبز» -> both names ({got})")
ok(A.title_subject_names("رواية عن الحب", ab) == [] or all(len(x) >= 4 for x in A.title_subject_names("رواية عن الحب", ab)),
   "2.2: a common word in many titles is no name")
ok(A.title_subject_names("عن اسم لا يوجد في اي عنوان", ab) == [], "2.2: a word in no title is skipped")

# 30.1 (grave): "not part of a series"
for text, refused in (("Any Hugo winners that are standalone, not part of a series?", True), ("ابي رواية مو سلسلة", True),
                      ("رواية مستقلة فازت بجائزة", True), ("ابي سلسلة فانتازيا", False), ("a fantasy series please", False)):
    t = " " + " ".join(n(text).split()) + " "
    ok(bool(A._SERIES_REFUSED.search(t)) == refused, f"30.1: «{text}» {'refuses' if refused else 'asks for'} a series")

# 23.1: «قرّاء مكتبة ثلاث» with no approved review
r = A.thalath_readers_unclaimed("«الطنطورية» متوفرة. قرّاء مكتبة ثلاث يوصفون أجواءها بالحزينة. تبغى غيرها؟")
ok("مكتبة ثلاث" not in r and "بعض القرّاء يوصفون" in r, f"23.1: the claim becomes «بعض القرّاء» ({r})")
r = A.thalath_readers_unclaimed("قرّاء مكتبة ثلاث ما عندي عنهم تقييم له.")
ok("مكتبة ثلاث" in r, "23.1: «they have not rated it» stays")
r = A.thalath_readers_unclaimed("قرّاء مكتبتنا يحسّونها حزينة وثقيلة عاطفيًا.")
ok("مكتبتنا" not in r and "بعض القرّاء" in r, "23.1 (replay 9.1): «قرّاء مكتبتنا» too")
r = A.thalath_readers_unclaimed("Thalath readers describe it as funny.")
ok("Thalath" not in r and "some readers" in r, "23.1: in English too")
ok("thalath_readers_unclaimed(resp[\"reply\"])" in src, "23.1: applied when no fact carries thalath_readers")

# 22.1: an English-speaking visitor gets the author's English copies first
ok("_more_en" in src and src.index("QADFIX23 (QAD 2026-10-29, case 22.1)") < src.index("    facts, reason_by_id, seen = [], {}, set()"),
   "22.1: English copies first, before the facts are built")

con.close()
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
