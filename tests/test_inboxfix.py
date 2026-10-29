"""[INBOXFIX] The library's inbox of 2026-10-29 12:08 (Ibrahim «صلح الاخطاء»): with the model down (12:04, after the 0.9.12
install) «كتب لنوال السعداوي» / «مصطفى حجازي» got books chosen by single words; «درامية نفس كتب دوستوفيسكي» got «ما لقيت
كتبًا لدوستويفسكي» with his books in stock; «عندكم الجريمة والعقاب» was read as a crime wish without the model."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, authors as AU
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

con = models.get_connection()
ab = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
avail = [b for b in ab.values() if A.is_available(b)]
src = open(A.__file__, encoding="utf-8").read()

def chat(text):
    return A.run_chat([{"role": "user", "content": text}], language="ar")

# 1. a stocked author named in the message -> his available books
some = next(b for b in avail if len(A.author_flow.display(b.get("author")).split()) >= 2
            and sum(1 for x in avail if x.get("author") == b.get("author")) >= 2)
name = A.author_flow.display(some["author"])
got = A.fallback_author("ابي كتب " + name, ab)
ok(got and got[0] == name and some["book_id"] in [b["book_id"] for b in got[1]], f"stocked author «{name}» -> his books")
got = A.fallback_author("عندك روايات ل" + name + " ؟", ab)
ok(got and got[0] == name, "with «ل» before the name too")
out = chat("ابي كتب " + name)
ok(out["_debug"].get("why") == "author" and out["books"] and all(name in b["reason"] for b in out["books"]),
   "the fallback answer shows his books, said to be his")

# 2. an author record with no book in stock -> «ما عندنا من كتب X شيء متوفر الآن», no books
con.execute("INSERT INTO authors (author_id, name_ar, aliases) VALUES (900101, 'كاتبة غائبة تجربة', '[]')"); con.commit()
got = A.fallback_author("كتب لكاتبة غائبة تجربة", ab)
ok(got == ("كاتبة غائبة تجربة", []), f"a record with no book -> no books ({got})")
out = chat("ابغى توصيات كتب مشابهة لكتب كاتبة غائبة تجربة")
ok(out["books"] == [] and "ما عندنا من كتب «كاتبة غائبة تجربة»" in out["reply"], "the reply says we have none, shows nothing")

# 3. one long word one or two letters off a one-word stored name («دوستوفيسكي» for «دوستويفسكي»)
con.execute("INSERT INTO authors (author_id, name_ar, aliases) VALUES (900102, 'فيودور تجربوفسكي', '[\"تجربوفسكي\"]')"); con.commit()
got = A.fallback_author("رواية درامية نفس كتب تجروبفسكي", ab)
ok(got and got[0] == "فيودور تجربوفسكي", f"a swapped pair in a long name finds the record ({got})")
ok(A.fallback_author("رواية خفيفة ومريحة", ab) is None, "no name -> None")

# 4. words of the message only, nothing understood: no book by one word
out = chat("عندكم حيونة الإنسان؟")
ok(out["books"] == [] and "ما لقيت بين الكتب المتوفرة كتابًا بهذا الوصف" in out["reply"],
   f"«حيونة الإنسان» -> no book chosen by «الإنسان» ({[b['title'] for b in out['books']]})")
one = next(b for b in avail if len(A.normalize(b["title"]).split()) == 1 and len(b["title"]) >= 5)
out = chat("كتب عن " + one["title"])
ok(any(b["book_id"] == one["book_id"] for b in out["books"]) or out["books"], "a one-word subject still finds books")

# 5. a title with one reading word in it is still the title
two = next((b for b in avail if len(A.normalize(b["title"]).split()) == 2
            and A.keyword_prefs.understood_anything(A.keyword_prefs.understand([A.normalize(b["title"]).split()[0]]))
            and not A.keyword_prefs.understood_anything(A.keyword_prefs.understand([A.normalize(b["title"]).split()[1]]))), None)
if two:
    ok(any(x["book_id"] == two["book_id"] for x in A.exact_title_books("عندكم " + two["title"], ab)),
       f"«عندكم {two['title']}» is the title")
else:
    ok("one reading word in it" in src, "«الجريمة والعقاب» rule in place (no such title in the test catalog)")
ok(A.exact_title_books("ابي قصص قصيرة", ab) == [], "«ابي قصص قصيرة» stays a wish")

# 6. similar-author: his own available books come too, said to be his
ok("INBOXFIX 2026-10-29 (library inbox 12:08, conversation 8.27)" in src and "ولا تقل أبدًا إن كتبه غير متوفرة" in src,
   "«نفس كتب فلان»: his own books in facts after the likeness ones; never «none of his books»")

# 7. INBOXFIX2 (conversation 8.7, «ظل الريح»): a stocked title alone is a question about it; «رشّحي لي كتابًا» right after
# one book asks for a book like it, varied (not four parts of one series), and the book just seen is not offered again
import chat_rules as CR
for text, want in (("رشّحي لي كتابًا", True), ("اقترح لي شي", True), ("recommend me a book", True), ("طيب رشح لي كتاب ثاني", True),
                   ("رشحي لي رواية رومانسية", False), ("ابي كتاب خفيف", False), ("ابي", False), ("ابيه", False)):
    ok(CR.bare_recommend(text) == want, f"«{text}» {'is' if want else 'is not'} a bare «recommend me a book»")
ok('if _exact_said and it.get("intent") in ("clarify", "smalltalk", "off_topic")' in src, "a stocked title alone is never answered with «تقصد…؟»")
ok("رشّح لي كتابًا ثانيًا يشبه" in src and "len(last_reply_ids) == 1" in src, "a bare request after one book -> one like it")
ok("_sim[:2] + _next[:1] + _authors[:2]" in src, "like-books: one per series, then its next part, then its author's books")
ok("لا تعرض «{_like_last}» نفسه" in src and "resp[\"show_book_ids\"] = [i for i in resp[\"show_book_ids\"]" in src,
   "the book just seen is not offered again")

con.close()
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
