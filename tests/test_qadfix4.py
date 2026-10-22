"""Fixes after the daily chat test round of 2026-10-16 (QADFIX4; Ibrahim «اصلح كل الاخطاء المتوسطة والبسيطة», 2026-10-16 21:4x)
and the admin «التنبيهات» / «ملاحظات من المطور».

ج1–ج4: a second look before «غير متوفر» (backend/second_look.py: the request read again, each reading looked up in
the catalog, the first that finds books answered as a rewritten request), «عن» is a subject and «من» an author's
country, a family name alone after an author we lack asks «تقصد ديفيد معلوف؟». م3 classics like an author, م4 pages
as a number, م5 an English copy about the subject, م6 an author's own book shown, م7/م8 cards agree with the text,
م9 the same author in another script; ب2 «لغز» titles, ب3 Hollywood films, ب4 an edition with no publisher, ب5
«اكتب لي قصيدة».
"""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm, chat, chat_rules as R, honors, topics, second_look, dev_notes, authors, author_flow, app as A
from models import get_connection
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

seen = {"calls": []}
REPLY = {"text": "x"}
def fake_respond(conv, facts_json, guidance, active, *a):
    seen["facts"] = json.loads(facts_json); seen["guidance"] = guidance; seen["conv"] = conv
    seen["calls"].append(guidance)
    ids = seen["show"] if seen.get("show") is not None else [f["id"] for f in seen["facts"]][:4]
    return {"reply": REPLY["text"] if not callable(REPLY["text"]) else REPLY["text"](seen["facts"]),
            "show_book_ids": ids, "suggestions": [], "unanswered": ""}
chat.respond = fake_respond
def interp(prefs=None, intent="recommend", titles=(), author=None):
    chat.interpret = lambda conv, active, *a: {"intent": intent, "prefs": llm.clean_prefs(prefs or {}), "mentioned_titles": list(titles),
                                              "title_variants": [], "wants_new_options": False, "references": [],
                                              "author_request": author or {}}
def say(*msgs, language="ar"):
    conv = [{"role": "user" if i % 2 == 0 else "assistant", "content": m} for i, m in enumerate(msgs)]
    seen.pop("facts", None); seen.pop("guidance", None)
    return A.run_chat(conv, active=("fake", "m"), language=language)
books = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
avail = [b for b in books.values() if A.is_available(b)]
facts_ids = lambda: [f["id"] for f in seen.get("facts", [])]

# ---------------------------------------------------------------- plain text rules
ok(R.page_limit(["عندكم كتب أقل من ١٠٠ صفحة؟ أي شي"]) == (99, None), "«أقل من ١٠٠ صفحة» = at most 99 pages")
ok(R.page_limit(["ابي رواية ما تتعدى 200 صفحة"]) == (200, None) and R.page_limit(["رواية أكثر من 400 صفحة"]) == (None, 401),
   "«ما تتعدى ٢٠٠» and «أكثر من ٤٠٠ صفحة»")
ok(R.page_limit(["ابي رواية قصيرة"]) == (None, None) and R.page_limit(["أقل من 100 صفحة", "مو مهم عدد الصفحات"]) == (None, None),
   "no number, no limit; «مو مهم عدد الصفحات» lifts it")
ok(R.write_asked("اكتب لي قصيدة عن المكتبة") == "poem" and R.write_asked("ابي كتاب يكتب عن الحب") is None
   and R.write_asked("write me a story") == "story", "«اكتب لي قصيدة» is a request to write, «كتاب يكتب عن» is not")
for txt in ("البؤساء غير متوفر عندنا بالإنجليزية", "سلسلة «أغنية الجليد والنار» مو متوفرة عندنا", "ما لقيت ضمن الكتب المتاحة كتابًا عن تاريخ عُمان",
            "ما عندي تفاصيل عن الكتب اللي تحمل اسم معلوف", "العندليب، مع إنها ليست عن الحروب الصليبية", "Sapiens isn’t available here",
            "I found no clearly beginner-focused book"):
    ok(R.says_not_found(txt), f"not found: «{txt[:40]}»")
ok(not R.says_not_found("عندنا ثلاث روايات تناسبك") and not R.says_not_found("هذي"), "a plain recommendation is not «not found»")

# ---------------------------------------------------------------- ج3: «عن» is a subject, «من» an author's country
ok(A.wished_country("ابغى كتاب عن تاريخ عمان والبحارة العمانيين") is None, "«كتاب عن تاريخ عمان والبحارة العمانيين» asks no author's country")
ok(A.wished_country("ابي روايات لكاتب عماني") is not None and A.wished_country("ابي روايات عمانية") is not None, "«لكاتب عماني» / «روايات عمانية» still do")
ok(A.wished_country("ابي كتب عن الحب لكاتبة سعودية") is not None, "a writer word after «عن» keeps the country")

# ---------------------------------------------------------------- ج4: a family name alone after an author we lack
con = get_connection()
con.execute("UPDATE books SET author='ديفيد معلوفتجربة' WHERE book_id=?", (avail[0]["book_id"],))
con.commit(); authors.sync(con)
con.execute("INSERT INTO authors (name_ar, name_latin, aliases, research_status, created_at, updated_at) VALUES ('أمين معلوفتجربة', 'Amin Test', '[]', 'researched', 'x', 'x')")
con.commit(); con.close()
cat = [dict(b) for b in A.list_books(only_active=False)]
req = {"name_as_written": "معلوفتجربة", "full_name": "أمين معلوفتجربة", "aliases": [], "mode": "similar"}
conv = [{"role": "user", "content": "ابي كتب أمين معلوفتجربة"}, {"role": "assistant", "content": "ما عندنا"},
        {"role": "user", "content": "طيب أي شي باسم معلوفتجربة عندكم؟"}]
q, names, only = author_flow.resolve(dict(req), conv, cat)
ok(q and "ديفيد معلوفتجربة" in q and "تقصده" in q, "«أي شي باسم معلوف» after Amin Maalouf asks «عندنا ديفيد معلوف … تقصده؟»")
conv2 = conv + [{"role": "assistant", "content": q}, {"role": "user", "content": "ايه"}]
q2, names2, only2 = author_flow.resolve(dict(req), conv2, cat)
ok(q2 is None and only2 and names2 and any("ديفيد معلوفتجربة" in n for n in names2), "«ايه» gives David Maalouf's books")
q4, _, _ = author_flow.resolve({"name_as_written": "أمين معلوفتجربة", "full_name": "أمين معلوفتجربة", "aliases": [], "mode": "similar"}, conv, cat)
ok(q4 and "ديفيد معلوفتجربة" in q4, "the same when the interpreter carries the full name on: «باسم معلوف» in the message is the family name")
q3, _, _ = author_flow.resolve({"name_as_written": "أمين معلوفتجربة", "full_name": "أمين معلوفتجربة", "aliases": [], "mode": "similar"},
                               conv[:1], cat)
ok(not (q3 and "ديفيد" in q3), "«كتب أمين معلوف» (the full name) is not asked about David")

# ---------------------------------------------------------------- the second look (ج1، ج2، م1، م2)
target = next(b for b in avail if b.get("title") and len(b["title"]) > 4)
REPLY["text"] = lambda facts: "غير متوفر عندنا"
import re as _re
chat.interpret = lambda conv, active, *a: {"intent": "about_book", "prefs": llm.clean_prefs({}),
    "mentioned_titles": _re.findall(r"«([^»]+)»", conv[-1]["content"]) or ["كتاب ما عندنا ابدا"],
    "title_variants": [], "wants_new_options": False, "references": [], "author_request": {}}
_orig_readings = second_look.readings
second_look.readings = lambda conv, first_reply, hints, active, language="ar": [
    {"kind": "title", "query": target["title"], "rewrite": f"عندكم «{target['title']}»؟", "clarify": f"تقصد «{target['title']}»؟"}]
seen["calls"].clear()
out = say("عندكم كتاب ما عندنا ابدا؟")
ok(len(seen["calls"]) == 2 and "نظرة ثانية" in seen["calls"][-1] and target["title"] in seen["conv"][-1]["content"],
   "a «غير متوفر» answer is looked at again: the request is answered a second time as the reading that finds a book")
ok(out.get("_debug", {}).get("second_look", {}).get("used", {}).get("query") == target["title"], "the second look is in the debug")
second_look.readings = lambda *a, **k: [{"kind": "title", "query": "عنوان لا يوجد أبدًا قطعًا", "rewrite": "x x x", "clarify": "x"}]
seen["calls"].clear()
out = say("عندكم كتاب ما عندنا ابدا؟")
ok(len(seen["calls"]) == 1 and out["reply"] == "غير متوفر عندنا", "a reading that finds nothing changes nothing")
REPLY["text"] = "هذي"
seen["calls"].clear()
out = say("عندكم كتاب ما عندنا ابدا؟")
ok(len(seen["calls"]) == 1 and "second_look" not in out.get("_debug", {}), "an answer that found books gets no second look")
second_look.readings = _orig_readings
ok(second_look.needed({"understanding": "keywords", "reply": "غير متوفر"}, [{"role": "user", "content": "x"}]) is False,
   "the keyword fallback is never looked at again")
ok(A._sl_find({"kind": "title", "query": target["title"]}, books)[:1] == [target["book_id"]], "a title reading is looked up in the catalog")

# ---------------------------------------------------------------- م4: pages as a number
interp({})
out = say("عندكم كتب أقل من ١٠٠ صفحة؟ أي شي")
under = [b for b in avail if b.get("page_count") and b["page_count"] <= 99]
if under:
    ok(facts_ids() and all(0 < (books[i].get("page_count") or 0) <= 99 for i in facts_ids()) and "بعدد الصفحات" in seen["guidance"],
       f"«أقل من ١٠٠ صفحة»: only books of 99 pages or fewer ({len(under)} in stock), and the model is told the count")

# ---------------------------------------------------------------- م7/م8: the cards agree with the text
interp({"genres": ["رواية"]})
novels = [b for b in avail if b.get("genre") == "رواية"]
REPLY["text"] = lambda facts: f"ما عندنا «سلسلة {facts[0]['title']} الكبرى»." if facts else "x"
seen["show"] = []
out = say("ابي رواية")
if seen.get("facts"):
    ok(seen["facts"][0]["id"] not in [b["book_id"] for b in out["books"]],
       "a title inside a longer quoted name («الجليد» in «أغنية الجليد والنار») is not a book the reply names")
REPLY["text"] = lambda facts: f"عندي لك «{facts[0]['title']}»." if facts else "x"
out = say("ابي رواية")
if seen.get("facts"):
    ok([b["book_id"] for b in out["books"]][:1] == [seen["facts"][0]["id"]], "a book the reply names from facts gets its card")
seen["show"] = None
REPLY["text"] = "هذي"

# ---------------------------------------------------------------- ب5: «اكتب لي قصيدة»
interp({}, intent="off_topic")
out = say("اكتب لي قصيدة عن المكتبة")
if any(b.get("genre") == "شعر" for b in avail):
    ok(facts_ids() and all(books[i].get("genre") == "شعر" for i in facts_ids()) and "لا تكتبين نصوصًا" in seen["guidance"],
       "«اكتب لي قصيدة»: a kind no, and poetry books")

# ---------------------------------------------------------------- ب1: «كتب أنشطة وتلوين» even when the interpreter names no subject
interp({"genres": ["أطفال"], "audience": "children"})
out = say("ابي كتب أنشطة وتلوين للأطفال")
ok("التلوين" in (seen.get("guidance") or "") or any("تلوين" in (books[i].get("title") or "") for i in facts_ids()),
   "«تلوين» is searched as the subject")

# ---------------------------------------------------------------- ب3: Hollywood films
honors.clear_cache()
con = get_connection()
b1, b2 = avail[1]["book_id"], avail[2]["book_id"]
for bid, title, country in ((b1, "A Hollywood Test Film", "الولايات المتحدة"), (b2, "فيلم عربي تجربة", "مصر")):
    con.execute("INSERT INTO book_adaptations (book_id, kind, title, year, country, source, created_by, created_at) VALUES (?, 'film', ?, 1990, ?, 'test', 't', 'x')", (bid, title, country))
con.commit(); con.close(); honors.clear_cache()
ok(honors.matches_adaptation(b1, "film", None, "foreign") and not honors.matches_adaptation(b2, "film", None, "foreign"),
   "«فيلم هوليوودي»: a film under a Latin title, not «عائد إلى حيفا»")
ok(honors.matches_adaptation(b2, "film", None, "arab") and not honors.matches_adaptation(b1, "film", None, "arab"), "«فيلم عربي»: the other way")
p, _, _, _ = A.prefs_from_ai(llm.clean_prefs({"adaptation": "film"}), A.normalize("رواية صارت فيلم هوليوودي"))
ok(p.get("adaptation_origin") == "american", "the wish is read from the visitor's words (Hollywood = US, AUDFIX 2026-10-16)")

# ---------------------------------------------------------------- ب2: «لغز» in a title for a mystery wish
_t = A._MYSTERY_TITLE.search(" " + A.normalize("لغز المدرسة") + " ")
ok(bool(_t) and not A._MYSTERY_TITLE.search(" " + A.normalize("كتاب الغابة") + " "), "«لغز المدرسة» reads as a mystery title")

# ---------------------------------------------------------------- ب1: activity books
idx = topics.index(books)
story = next((b for b in avail if b.get("summary")), None)
if story:
    con = get_connection()
    con.execute("UPDATE books SET summary=?, title=? WHERE book_id=?", ("قصة عن طفلة وأسرة تحب الأنشطة الجماعية", "قصة تجربة الأسرة", story["book_id"]))
    con.commit(); con.close()
    bb = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
    ok(topics.match(["الأنشطة"], story["book_id"], topics.index(bb))[0] == 0, "a story whose summary mentions «الأنشطة» is not an activity book")

# ---------------------------------------------------------------- developer notes and alerts
con = get_connection()
con.execute("UPDATE books SET publisher='دار اسفار' WHERE book_id IN (?, ?)", (avail[3]["book_id"], avail[4]["book_id"]))
con.commit(); con.close()
rows = dev_notes.list_rows("all")
asf = next((n for n in rows if n["ref"] == "DN-2026-10-16-asfar"), None)
ok(asf and asf["status"] == "open" and asf["title"] == "أرجو مراجعة كل كتب دار أسفار" and asf["action"]["type"] == "books",
   "the first developer note is there, open, with its list of books")
note, lst = dev_notes.books_for(asf["note_id"])
ok({avail[3]["book_id"], avail[4]["book_id"]} <= {b["book_id"] for b in lst}, "a click on it lists the books under «دار اسفار»")
m = login(A.app.test_client())
s = login(A.app.test_client(), "staff")
r = s.get("/api/admin/inbox/alerts").get_json()
ok(any(i["key"] == "dev" and i["open"] >= 1 for i in r["items"]) and r["attention"] >= 1, "«التنبيهات» counts the open developer note (staff account)")
r = s.post(f"/api/admin/inbox/dev-notes/{asf['note_id']}", json={"status": "done", "comment": "راجعنا ١٢ كتابًا"})
ok(r.status_code == 200 and r.get_json()["note"]["status"] == "done" and r.get_json()["note"]["handled_note"] == "راجعنا ١٢ كتابًا",
   "staff mark it done with what they did")
con = get_connection()
ok(con.execute("SELECT COUNT(*) FROM audit_log WHERE action='dev_note.done' AND target=?", (str(asf["note_id"]),)).fetchone()[0] == 1,
   "and it is written in the audit log")
con.close()
dev_notes.counts()   # a later sync (as after an update) never re-opens it
ok(next(n for n in dev_notes.list_rows("all") if n["ref"] == "DN-2026-10-16-asfar")["status"] == "done", "an update never re-opens a done note")
text = A.inbox_export.build("all")[0] if hasattr(A.inbox_export, "build") else None
if text:
    ok("ملاحظات من المطور" in text and "راجعنا ١٢ كتابًا" in text, "the export carries the developer notes back with the staff comment")
ok(s.get(f"/api/admin/inbox/dev-notes/{asf['note_id']}/books").status_code == 200 and A.app.test_client().get("/api/admin/inbox/alerts").status_code == 401,
   "the books list opens for staff; nothing without a sign-in")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
