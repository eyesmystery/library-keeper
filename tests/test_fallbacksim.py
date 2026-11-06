"""[FALLBACKSIM] The library's log of 2026-11-01 13:15, model down («النظام البديل»): «قرأت مؤخرا كل صيف تلاه. ابغاه شيء مقارب له»
got one card, «كل صيف تلاه» (book 2017) itself; «هذا الكتاب اللي قرأته. انا ابغى كتب مشابهه له» after it got «ما لقيت … كتابًا
بهذا الوصف» with no card. Without the model, a wish for books like a book (named, or the one just shown) is answered with books
in stock close to it — its «similar» relations, then the nearest stored meanings — never the book itself, another edition or
another part of its series. No reference -> the keyword answer as before. The tests' catalog has no book 2017: it is added here
with a few neighbours and their vectors (no model, no Ollama)."""
import array, math, os, sqlite3, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, embeddings as E
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()
con = models.get_connection()
COLS = "book_id, title, author, genre, quantity, active, language, summary, hook, series_name, series_position"
BOOKS = [
    (2017, "كل صيف تلاه", "كارلي فورتشن", "رواية", 3, 1, "ar", "قصة حب صيفية بين بيرسي وسام عند بحيرة، تعود بعد سنوات", "صيف، بحيرة، حب أول", None, None),
    (2018, "كل صيف تلاه (ج 2)", "كارلي فورتشن", "رواية", 2, 1, "ar", "تكملة قصة بيرسي وسام عند البحيرة", "الجزء الثاني", "كل صيف تلاه", 2),
    (2019, "قابلني عند البحيرة", "كارلي فورتشن", "رواية", 3, 1, "ar", "لقاء عند بحيرة بعد سنوات وحب قديم", "بحيرة وحب", None, None),
    (2021, "صيف على الشاطئ", "إميلي هنري", "رواية", 4, 1, "ar", "كاتبان يقضيان صيفًا على الشاطئ ويقعان في الحب", "صيف وحب", None, None),
    (2022, "بعدك", "جوجو مويس", "رواية", 2, 1, "ar", "حب وفقد وبداية جديدة بعد سنوات", "حب بعد فقد", None, None),
    (2023, "عظام القلب", "كولين هوفر", "رواية", 2, 1, "ar", "قصة حب صيفية على جزيرة", "صيف وجزيرة", None, None),
    (2024, "صيف غائب", "كاتبة منسية", "رواية", 0, 1, "ar", "حب صيفي قديم", "غير متوفر", None, None),
    (2025, "كيف تحب نفسك في الصيف", "مدرب حياة", "تطوير الذات", 5, 1, "ar", "دليل عملي لحب الذات في الإجازة الصيفية", "تطوير", None, None),
    (2026, "صيف الأرنب الصغير", "كاتب أطفال", "أطفال", 5, 1, "ar", "أرنب يقضي الصيف عند البحيرة", "أطفال", None, None),
    (2030, "The Summer Lake", "Anna North", "رواية", 3, 1, "en", "Two friends, a lake house and a first love that returns every summer", "summer love", None, None),
]
for row in BOOKS:
    con.execute(f"INSERT OR REPLACE INTO books ({COLS}) VALUES ({', '.join('?' * len(row))})", row)
con.commit()
MODEL = E.model_name()
def unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]
VEC = {2017: [1, 0, 0, 0], 2018: [1, 0.02, 0, 0], 2019: [1, 0.3, 0, 0], 2021: [1, 0.35, 0.1, 0], 2022: [1, 0.4, 0.2, 0],
       2023: [1, 0.42, 0.2, 0.1], 2024: [1, 0.1, 0, 0], 2025: [1, 0.05, 0, 0], 2026: [1, 0.05, 0.05, 0], 2030: [1, 0.33, 0.05, 0]}
con.row_factory = sqlite3.Row
E.ensure_table(con)
for bid, v in VEC.items():
    b = dict(con.execute("SELECT * FROM books WHERE book_id=?", (bid,)).fetchone())
    h = E.text_hash(E.book_text(b))
    con.execute("INSERT OR REPLACE INTO book_embeddings VALUES (?,?,?,?,?,?)",
                (bid, MODEL, h, 4, array.array("f", unit(v)).tobytes(), "x"))
# one computed «similar» relation (as relations.rebuild writes from the cards), and one to the next part (still never shown)
try:
    for tgt in (2022, 2018):
        con.execute("""INSERT INTO book_relations (source_book_id, target_book_id, relation_type, reason_ar, confidence, source, created_at)
                       VALUES (2017, ?, 'similar', 'يتقاطعان في: الحب الأول', 0.9, 'computed', 'x')""", (tgt,))
    HAVE_REL = True
except sqlite3.OperationalError:
    HAVE_REL = False
con.commit(); con.close()
E._cache.update(vecs=None, at=0.0)
E.embed = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no embedding call without the model"))   # Ollama down too

NEVER = {2017, 2018, 2024, 2025, 2026}
def ids(r):
    return [b["book_id"] for b in r["books"]]

# --- turn 1: the title named, «مقارب له» -----------------------------------------------------------------------------------
T1 = [{"role": "user", "content": "قرأت مؤخرا كل صيف تلاه. ابغاه شيء مقارب له"}]
r1 = A.run_chat(T1)
got1 = ids(r1)
print("   turn 1:", r1["reply"], [(b["book_id"], b["title"], b.get("reason")) for b in r1["books"]])
ok(r1["reply"].startswith("هذي كتب قريبة من «كل صيف تلاه» ومتوفرة الآن"), f"(1) the reply names the book it is like ({r1['reply']})")
ok(got1 and 2017 not in got1, f"(1) books like it, never «كل صيف تلاه» itself ({got1})")
ok(not (set(got1) & NEVER), "(1) no other part of its series, nothing out of stock, no self-help or children's book")
ok({2019, 2021, 2023} & set(got1) and len(got1) <= 4, "(1) the nearest novels in stock")
ok(not HAVE_REL or got1[0] == 2022, f"(1) its «similar» relation comes first, as on the model path ({got1[:1]})")
ok(r1["_debug"].get("mode") == "keywords" and r1["_debug"].get("why") == "similar to a book" and r1["_debug"].get("reference") == [2017],
   f"(1) debug: keywords, similar to book 2017 ({r1['_debug']})")
ok(all("«كل صيف تلاه»" in (b.get("reason") or "") for b in r1["books"]), "(1) each card says what it is close to")

# --- turn 2: the follow-up pointing at the book just shown ------------------------------------------------------------------
T2 = T1 + [{"role": "assistant", "content": "هذي كتب متوفرة الآن وقريبة من كلامك. اضغط على أي كتاب تشوف تفاصيله.", "book_ids": [2017]},
           {"role": "user", "content": "هذا الكتاب اللي قرأته. انا ابغى كتب مشابهه له"}]
r2 = A.run_chat(T2)
got2 = ids(r2)
print("   turn 2:", r2["reply"], [(b["book_id"], b["title"]) for b in r2["books"]])
ok(r2["reply"].startswith("هذي كتب قريبة من «كل صيف تلاه»") and "ما لقيت" not in r2["reply"], f"(2) books like the one shown ({r2['reply']})")
ok(got2 and not (set(got2) & NEVER), f"(2) never the book shown, its other part or a book out of stock ({got2})")
ok(r2["_debug"].get("how") == "shown" and r2["_debug"].get("reference") == [2017], "(2) the reference is the last turn's book")
r2b = A.run_chat(T1 + [T2[1], {"role": "user", "content": "شي يشبهه"}])
ok(ids(r2b) and 2017 not in ids(r2b), "(2) the quick answer «شي يشبهه» after a book: books like it")
r2c = A.run_chat([T2[1], {"role": "user", "content": "similar to this one please"}], language="en")
ok(r2c["reply"].startswith("These books are close to «كل صيف تلاه»") and ids(r2c) and 2017 not in ids(r2c), f"(2) "
   f"English «similar to this» ({r2c['reply']})")

# --- English: "something like <title>" ----------------------------------------------------------------------------------------
r3 = A.run_chat([{"role": "user", "content": "Can you suggest something like The Summer Lake?"}], language="en")
got3 = ids(r3)
print("   english:", r3["reply"], got3)
ok(r3["reply"] == "These books are close to «The Summer Lake» and available now. Tap a book to see its details.", f"(3) English reply ({r3['reply']})")
ok(got3 and 2030 not in got3 and not (set(got3) & {2024, 2025, 2026}), f"(3) books like it, not itself ({got3})")
ok(any((b.get("reason") or "").startswith("Close to «The Summer Lake»") for b in r3["books"]), "(3) English card reasons")

# --- the parts asked for: the series is no longer kept out ----------------------------------------------------------------------
r4 = A.run_chat([{"role": "user", "content": "شي مثل كل صيف تلاه او باقي الاجزاء"}])
ok(2017 not in ids(r4) and r4["_debug"].get("why") == "similar to a book", "(4) parts asked: still never the book itself")

# --- no reference -> unchanged ------------------------------------------------------------------------------------------------
real = A.fallback_similar_answer
def both(msgs, language="ar"):
    a = A.run_chat(msgs, language=language)
    A.fallback_similar_answer = lambda *x, **k: None
    try:
        b = A.run_chat(msgs, language=language)
    finally:
        A.fallback_similar_answer = real
    return a, b
for msgs, why in (([{"role": "user", "content": "ابغى رواية زي ما قلت لك خفيفة"}], "a likeness word and no title"),
                  ([{"role": "user", "content": "شي يشبهه"}], "«شي يشبهه» with no book shown"),
                  ([{"role": "user", "content": "قرأت مؤخرا كل صيف تلاه"}], "a title read, nothing asked"),
                  ([{"role": "user", "content": "عندكم كل صيف تلاه؟"}], "the title asked about"),
                  ([{"role": "user", "content": "ابغى كتب من نفس مؤلف كل صيف تلاه"}], "the same author asked for"),
                  ([{"role": "user", "content": "رواية رومانسية خفيفة"}], "a plain wish"),
                  ([{"role": "user", "content": "I'd like a light novel"}], "English «I'd like»")):
    a, b = both(msgs, "en" if msgs[-1]["content"].isascii() else "ar")
    # the cards of the general keyword answer may vary from one run to the next; its reply and its route do not
    ok(a["reply"] == b["reply"] and a["_debug"].get("why") == b["_debug"].get("why") != "similar to a book",
       f"(5) unchanged: {why} ({a['reply'][:60]}… {ids(a)} / {ids(b)})")
ok(ids(A.run_chat([{"role": "user", "content": "عندكم كل صيف تلاه؟"}])) == [2017], "(5) the title asked about is still shown")

# --- AUDFIX (audit 2026-11-01 P2): the visitor's conditions are read from the whole conversation --------------------------------
# «ابغى رواية قصيرة» -> a book shown -> «كتب قريبة منه» brought four 350-page books; «… بس قصيرة» then brought none at all
con = models.get_connection()
for bid, pages, price in ((2017, 300, 50), (2019, 350, 25), (2021, 120, 60), (2022, 350, 80), (2023, 150, 28), (2030, 350, 90)):
    con.execute("UPDATE books SET page_count=?, price=? WHERE book_id=?", (pages, price, bid))
con.commit(); con.close()
SHORT = {2021, 2023}
SHOWN = {"role": "assistant", "content": "هذا الكتاب اللي سألت عنه.", "book_ids": [2017]}
def conv(first, last):
    return [{"role": "user", "content": first}, SHOWN, {"role": "user", "content": last}]
def pages_of(r):
    return [(b["book_id"], b.get("page_count")) for b in r["books"]]
r6 = A.run_chat(conv("ابغى رواية قصيرة", "كتب قريبة منه"))
print("   short before:", r6["reply"], pages_of(r6))
ok(r6["_debug"].get("why") == "similar to a book" and ids(r6) and set(ids(r6)) <= SHORT,
   f"(6) «قصيرة» said before the likeness stays: only the short neighbours ({pages_of(r6)})")
r6b = A.run_chat(conv("ابغى رواية قصيرة", "كتب قريبة منه بس قصيرة"))
ok(ids(r6b) and set(ids(r6b)) <= SHORT, f"(6) «قصيرة» said again with the likeness: the short neighbours, not an empty answer ({pages_of(r6b)})")
r6c = A.run_chat(conv("ابغى رواية قصيرة", "عادي طويل، كتب قريبة منه"))
ok(r6c["_debug"].get("why") == "similar to a book" and set(ids(r6c)) - SHORT,
   f"(6) the length lifted in the latest message («عادي طويل»): longer books come back ({pages_of(r6c)})")
r6d = A.run_chat(conv("ابغى رواية ما تتعدى ٣٠ ريال", "كتب قريبة منه"))
ok(ids(r6d) and all(0 < (b.get("price") or 0) <= 30 for b in r6d["books"]),
   f"(6) a price limit said before stays ({[(b['book_id'], b.get('price')) for b in r6d['books']]})")
r6e = A.run_chat(conv("ابغى كتاب تطوير ذات", "كتب قريبة منه"))
ok(r6e["_debug"].get("why") == "similar to a book" and ids(r6e) and 2025 not in ids(r6e),
   f"(6) an earlier kind of book (self-help) gives way to the likeness of the novel shown, nothing emptied ({ids(r6e)})")
r6f = A.run_chat([{"role": "user", "content": "I want a short novel"}, SHOWN, {"role": "user", "content": "similar to this one please"}], language="en")
ok(ids(r6f) and set(ids(r6f)) <= SHORT, f"(6) English: «short» said before stays ({pages_of(r6f)})")
con = models.get_connection()
con.execute("UPDATE books SET page_count=400 WHERE book_id IN (2021, 2023)"); con.commit(); con.close()
r6g = A.run_chat(conv("ابغى رواية قصيرة", "كتب قريبة منه"))
ok(r6g["books"] == [] and "قصير" in r6g["reply"] and "تحب نخفف" in r6g["reply"],
   f"(6) no short book close to it: said plainly with the condition, and the visitor may relax it ({r6g['reply']})")
con = models.get_connection()
con.execute("UPDATE books SET page_count=120 WHERE book_id=2021"); con.execute("UPDATE books SET page_count=150 WHERE book_id=2023")
con.commit(); con.close()

# --- AUDFIX (audit 2026-11-01 P3): a title edit renews the title-reference memory while the number of books stays the same -------
from auth_helper import login
books = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
ok(A.fallback_like_reference("أبغى كتب مثل كل صيف تلاه", books), "(7) the memory is warm: «كل صيف تلاه» is known")
n0 = len(books)
dev = login(A.app.test_client(), "developer")
resp = dev.patch("/api/books/2017", json={"title": "حين يعود الربيع"}, headers={"X-CSRF-Token": dev.csrf})
books = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
ok(resp.status_code == 200 and len(books) == n0 and books[2017]["title"] == "حين يعود الربيع",
   f"(7) the developer renamed book 2017, the number of books unchanged ({resp.status_code}, {n0} -> {len(books)})")
new = A.fallback_like_reference("أبغى كتب مثل حين يعود الربيع", books)
old = A.fallback_like_reference("أبغى كتب مثل كل صيف تلاه", books)
ok(bool(new) and new[0][0]["book_id"] == 2017, f"(7) the new title is known at once, without a restart ({new and [b['book_id'] for b in new[0]]})")
ok(not old or 2017 not in [b["book_id"] for b in old[0]], f"(7) the old title no longer points at book 2017 ({old and [b['book_id'] for b in old[0]]})")
dev.patch("/api/books/2017", json={"title": "كل صيف تلاه"}, headers={"X-CSRF-Token": dev.csrf})
books = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
back = A.fallback_like_reference("أبغى كتب مثل كل صيف تلاه", books)
ok(bool(back) and back[0][0]["book_id"] == 2017, "(7) and back again after the title is restored")

# --- AUDFIX (audit 2026-11-04 P2): a young reader and «مافيها شي للكبار» hold in «books like X» without the model too --------------
# «لبنت أخوي عمرها ١٣ سنة تبي رواية رومانسية مافيها شي للكبار» -> a book shown -> «كتب قريبة منه» brought book 2022, whose record
# carries sexual content (confidence 0.6, severity 1): the strict rule of QADFIX26 #1 (CLEAN_YOUNG) was read on the model path only
import json
con = models.get_connection()
con.execute("INSERT OR REPLACE INTO book_profiles (book_id, format, sensitive_content) VALUES (2022, 'novel', ?)", (json.dumps(["sexual_content"]),))
con.execute("INSERT INTO attribute_evidence (book_id, attribute_name, attribute_value, confidence, source_site, severity) "
            "VALUES (2022, 'sensitive_content', 'sexual_content', 0.6, 'test_fallbacksim', 1)")
con.execute(f"INSERT OR REPLACE INTO books ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (2031, "صيف في المخيم", "كاتبة يافعين", "يافعين", 3, 1, "ar", "مغامرة صيفية لأصدقاء في مخيم عند بحيرة", "صيف ومغامرة", None, None))
con.commit(); con.close()
CLEAN = "لبنت أخوي عمرها ١٣ سنة تبي رواية رومانسية مافيها شي للكبار"
def strict_ok(r, age=13):
    cards = A.get_profile_cards()
    return all(A.age_allowed(cards.get(b["book_id"]), "young_adult", {A.CLEAN_YOUNG}, dict(A.get_book(b["book_id"])), age)
               for b in r["books"])
cards8 = A.get_profile_cards()
ok(not A.age_allowed(cards8.get(2022), "young_adult", {A.CLEAN_YOUNG}, dict(A.get_book(2022)), 13), "(8) set-up: book 2022 fails the strict guard")
r8 = A.run_chat(conv(CLEAN, "كتب قريبة منه"))
print("   clean young:", r8["reply"], ids(r8), r8["_debug"])
ok(r8["_debug"].get("why") == "similar to a book" and r8["_debug"].get("clean_young") is True,
   f"(8) the likeness answer reads the young reader and the clean wish ({r8['_debug']})")
ok(2022 not in ids(r8), f"(8) the book with sexual content in its record is never offered ({ids(r8)})")
ok(strict_ok(r8), f"(8) every book offered passes the strict guard of the model's answer ({ids(r8)})")
_all8 = [b["book_id"] for b in A.clean_young_shelf({b["book_id"]: dict(b) for b in A.list_books(only_active=False)},
                                                  {2017}, cards8, "young_adult", 13)]
_shelf8 = _all8[:4]
ok(2031 in _all8 and 2022 not in _all8, f"(8) set-up: the young readers' shelf has clean books in stock ({_shelf8})")
ok(ids(r8) == r8["_debug"].get("clean_young_shelf") == _shelf8
   and r8["reply"].startswith("ما لقيت الآن بين الكتب المتوفرة كتابًا قريبًا من «كل صيف تلاه» يناسب عمر القارئ وما فيه شي للكبار")
   and all((b.get("reason") or "").startswith("من رف اليافعين") for b in r8["books"]),
   f"(8) nothing close is clean for her: said first, then the young readers' shelf, as the model's answer does ({r8['reply'][:90]}…)")
con = models.get_connection()
con.execute("UPDATE books SET age_min=12, age_max=16 WHERE book_id=2023"); con.commit(); con.close()
r8b = A.run_chat(conv(CLEAN, "كتب قريبة منه"))
ok(2023 in ids(r8b) and 2022 not in ids(r8b) and strict_ok(r8b) and r8b["reply"].startswith("هذي كتب قريبة من «كل صيف تلاه»"),
   f"(8) a neighbour whose recorded age range fits her and with a clean record is still offered as close ({ids(r8b)})")
r8e = A.run_chat([{"role": "user", "content": "For my 13 year old niece, a clean romance novel, nothing adult"}, SHOWN,
                  {"role": "user", "content": "similar to this one please"}], language="en")
ok(r8e["_debug"].get("clean_young") is True and 2022 not in ids(r8e) and strict_ok(r8e),
   f"(8) English: the same rule ({ids(r8e)}, {r8e['reply'][:70]}…)")
r8c = A.run_chat(conv("لبنت أخوي عمرها ١٣ سنة تبي رواية رومانسية", "كتب قريبة منه"))
ok(not r8c["_debug"].get("clean_young") and 2022 in ids(r8c),
   f"(8) no clean wish: unchanged (Ibrahim: «لا تمنع. فلتر اذا الزائر طلب») — the teenager gets the books close to it ({ids(r8c)})")
r8k = A.run_chat([{"role": "user", "content": CLEAN}])
ok(r8k["_debug"].get("why") != "similar to a book" and 2022 not in ids(r8k) and strict_ok(r8k),
   f"(8) the general keyword answer (no reference) keeps the same rule ({ids(r8k)})")
con = models.get_connection()
con.execute("UPDATE books SET age_min=NULL, age_max=NULL WHERE book_id=2023"); con.commit(); con.close()
ok(src.count("clean_young_wish(") == 4 and src.count("clean_young_shelf(") == 3,
   "(8) one rule (clean_young_wish) for the model's answer, the keyword answer and the likeness answer; one shelf (clean_young_shelf)")

# --- the helpers and the paths ----------------------------------------------------------------------------------------------
ok(E.similar_to_book([2017], [2019, 2025, 9999])[:1] == [(2025, E.similar_to_book([2017], [2025])[0][1])]
   and E.similar_to_book([424242], [2019]) == [], "embeddings.similar_to_book: stored vectors only, [] without a vector")
ok(src.count("fallback_similar_answer(") == 2 and src.count("last_ids=last_reply_ids") == 2,
   "only the keyword fallback (both of its calls) answers this; the model path is untouched")
print(f"\nRESULT pass={P} fail={F}")
sys.exit(1 if F else 0)
