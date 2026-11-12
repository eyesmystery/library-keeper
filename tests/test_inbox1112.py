"""[INBOX1112] Library inbox 2026-11-11 (20261111-210144-daily), §3 «أسئلة بلا جواب», replayed with the model on 2026-11-12:
item 24 «علي عزت بيغوفيتش» (test conversation 8.13: «بيجوفيتش» → «تقصد «دافيد ب»؟», then «هروبي إلى الحرية» → the same question again)
and item 11 «Wings of Fire» (read as «أجنحة النار», taken for the one-word title «النار» and answered «not available» with nothing
else). Each check uses the inbox wording and a fresh one, with negatives."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
import author_flow as AF
import authors as AR
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src_app = open(A.__file__, encoding="utf-8").read()
src_af = open(AF.__file__, encoding="utf-8").read()

# ---------------------------------------------------------------- item 24: an initial is no family name
by_display = {"دافيد ب": {"جان بيير فيليو ودافيد ب"}, "جان بيير فيليو": {"جان بيير فيليو ودافيد ب"},
              "كارلوس زافون": {"كارلوس زافون"}, "بيتر بوتين": {"بيتر بوتين"}}
books = [{"book_id": 1, "title": "أعداء حميمون", "author": "جان بيير فيليو ودافيد ب", "active": 1, "quantity": 3},
         {"book_id": 2, "title": "ظل الريح", "author": "كارلوس زافون", "active": 1, "quantity": 3},
         {"book_id": 3, "title": "كتاب ما", "author": "بيتر بوتين", "active": 1, "quantity": 3}]
ok(AR._same_word("بيجوفيتش", "ب"), "cause: _same_word lets the one-letter «ب» (an initial) stand for «بيجوفيتش»")
ok(AF.near_authors("بيجوفيتش", by_display, books) == [], "24: «بيجوفيتش» is not offered «دافيد ب»")
ok(AF.near_authors("بيرلمان", by_display, books) == [], "fresh: «بيرلمان» is not offered «دافيد ب» either")
ok(AF.near_authors("زافوون", by_display, books) == ["كارلوس زافون"], "a misspelt family name of a catalog author is still offered")
ok(AF.near_authors("بوتين", by_display, books) == ["بيتر بوتين"], "a whole family name starting with «ب» still finds its author")

# ---------------------------------------------------------------- item 24: «تقصد …؟» is not asked again for the same person
REQ = lambda full="علي عزت بيجوفيتش", mode="works": {"mode": mode, "name_as_written": "بيجوفيتش", "full_name": full,
                                                    "aliases": ["علي عزت بيغوفيتش", "Alija Izetbegović"]}
U = lambda t: {"role": "user", "content": t}
M = lambda t: {"role": "assistant", "content": t}
r1 = AF.resolve(REQ(), [U("بيجوفيتش")], books)
ok(r1[0] == "تقصد «علي عزت بيجوفيتش»؟", f"24 turn 1: the model's full name is proposed, not «دافيد ب» ({r1[0]!r})")
Q1 = r1[0]
r2 = AF.resolve(REQ("علي عزت بيغوفيتش"), [U("بيجوفيتش"), M(Q1), U("هروبي إلى الحرية")], books)
ok(r2[0] is None and r2[2] is True, f"24 turn 2: «هروبي إلى الحرية» (his book) after the question is taken as yes ({r2[0]!r})")
NONE = "ما عندنا الآن كتب لـعلي عزت بيغوفيتش. تبغى أقترح لك كتبًا قريبة من أسلوب كتاباته؟"
r3 = AF.resolve(REQ("علي عزت بيغوفيتش", "similar"), [U("بيجوفيتش"), M(Q1), U("هروبي إلى الحرية"), M(NONE), U("نعم")], books)
ok(r3[0] is None, f"24 turn 3: «نعم» to «تبغى أقترح …؟» does not bring the question back ({r3[0]!r})")
r3w = AF.resolve(REQ("علي عزت بيغوفيتش"), [U("بيجوفيتش"), M(Q1), U("هروبي إلى الحرية"), M(NONE), U("نعم")], books)
ok(r3w[0] is None, "… also when the model reads «works» again")
r2f = AF.resolve(REQ(), [U("بيجوفيتش"), M(Q1), U("صاحب كتاب الإسلام بين الشرق والغرب")], books)
ok(r2f[0] is None, "fresh: «صاحب كتاب الإسلام بين الشرق والغرب» answers the question too")
ok(AF.resolve(REQ(), [U("بيجوفيتش"), M(Q1), U("ايه")], books)[0] is None, "«ايه» still confirms")
ok(AF.resolve(REQ(), [U("بيجوفيتش"), M(Q1), U("لا")], books)[0] == "وش اسم المؤلف كامل؟", "«لا» still asks for the full name")
ok(AF.resolve(REQ(), [U("بيجوفيتش"), M(Q1), U("لا مو هو")], books)[0] is not None, "«لا مو هو» is no yes")
ok(AF.resolve(REQ(), [U("بيجوفيتش"), M(Q1), U("no, someone else")], books)[0] is not None, "\"no, someone else\" is no yes")
other = {"mode": "works", "name_as_written": "زرقاوي", "full_name": "سمير زرقاوي", "aliases": []}
ok(AF.resolve(other, [U("بيجوفيتش"), M(Q1), U("زرقاوي")], books)[0] == "تقصد «سمير زرقاوي»؟",
   "another person after the question is still asked about")
ok(AF.resolve(REQ(), [U("بيجوفيتش"), M("تقصد «سمير زرقاوي»؟"), U("هروبي إلى الحرية")], books)[0] == Q1,
   "a question about another person earlier confirms nothing")

# ---------------------------------------------------------------- item 11: a one-word title inside the title asked is not that book
AB = {i: dict(b, book_id=i, active=1, quantity=q, author=a) for i, (b, q, a) in enumerate([
    ({"title": "النار"}, 3, "غير معروف"), ({"title": "نار شاحبة"}, 3, "فلاديمير نابوكوف"),
    ({"title": "مدن الملح"}, 3, "عبد الرحمن منيف"), ({"title": "رجال في الشمس"}, 3, "غسان كنفاني"),
    ({"title": "الحرب"}, 3, "مؤلف ما")], start=1)}
ids = lambda m: [b["book_id"] for b in A.match_titles([m], AB)]
ok(ids("أجنحة النار") == [], f"11: «أجنحة النار» (Wings of Fire) is not «النار» ({ids('أجنحة النار')})")
ok(ids("ليالي الحرب الطويلة") == [], "fresh: «ليالي الحرب الطويلة» is not «الحرب»")
ok(ids("النار") == [1], "«النار» asked alone is still found")
ok(ids("خماسية مدن الملح") == [3], "a title of two words inside the mention still matches («مدن الملح»)")
ok(ids("في الشمس") == [4], "the visitor's words inside a longer title still match («رجال في الشمس»)")
ok("INBOX1112 (inbox 2026-11-11 §3, item 11 «Wings of Fire»)" in src_app, "app.py carries the item 11 comment")
ok(src_af.count("INBOX1112 (inbox 2026-11-11 §3, item 24 «علي عزت بيغوفيتش»") == 2, "author_flow.py carries the item 24 comments")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
