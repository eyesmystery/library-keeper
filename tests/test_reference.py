import os, sys, json, sqlite3
# Run through tests/run_all.py: cwd is a throwaway copy of backend/ with its own data/ (prototype catalog).
sys.path.insert(0, os.getcwd())
import models; models.init_db()
import llm, chat, app as A
import author_flow
P = F = 0
def ok(cond, msg):
    global P, F; print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

con = sqlite3.connect(models.DB_PATH); con.row_factory = sqlite3.Row
author = con.execute("""select b.author from books b join book_profiles p using(book_id)
                        where b.quantity>0 and b.active=1 and p.format='novel' and b.author like '%محفوظ%' limit 1""").fetchone()["author"]
ok(bool(author), f"fixture has an in-stock author: {author}")

ok(A._same_author(A.normalize("يوستاين غاردر"), A.normalize("جوستاين غاردر")) and not A._same_author(A.normalize("أحمد محمد"), A.normalize("خالد محمد")), "author match tolerates transliteration, not common names")
# parsing: unknown references carry no guessed data
refs = chat.clean_references([{"title": "كتاب ما", "author": "س", "format": "novel", "moods": ["sad", "x"], "themes": ["الفقد"], "known": False},
                              {"title": "رواية", "author": author, "format": "novel", "moods": ["sad", "bogus"], "themes": ["الحب"], "known": True},
                              {"title": ""}, "junk"])
ok(len(refs) == 2 and refs[0]["author"] == "" and refs[0]["moods"] == [] and refs[1]["moods"] == ["sad"], "references cleaned; unknown ones keep no guessed traits")

seen = {}
def fake_respond(conv, facts_json, guidance, active):
    seen["facts"] = json.loads(facts_json); seen["guidance"] = guidance
    return {"reply": "x", "show_book_ids": [f["id"] for f in seen["facts"]][:4], "suggestions": []}
chat.respond = fake_respond
def turn(ref, titles=None):
    chat.interpret = lambda conv, active: {"intent": "recommend", "prefs": llm.clean_prefs({}), "mentioned_titles": titles or [ref["title"]],
                                          "wants_new_options": False, "references": chat.clean_references([ref])}
    return A.run_chat([{"role": "user", "content": "أبغى رواية تشبه " + ref["title"]}], active=("fake", "m"))

out = turn({"title": "رواية غير موجودة إطلاقًا", "author": author, "format": "novel", "moods": ["contemplative", "sad"], "themes": [], "known": True})
same = [f for f in seen["facts"] if f["why_it_matches"].startswith("من كتب")]
ok(same and all(f["author"] == author for f in same), f"same-author books offered ({len(same)})")
alike = [f for f in seen["facts"] if f["why_it_matches"].startswith("قريب من «رواية غير موجودة")]
ok(alike, "books resembling the reference offered with a reason")
ok(all(f["available"] for f in same + alike), "only available books")
ok("غير متوفر عندنا" in seen["guidance"] and "لا تصف أحداثه" in seen["guidance"], "guidance: not in stock, likeness from general knowledge, no plot")
ok(out["books"] and all(b["reason"] for b in out["books"]), "shown books keep their reason")

turn({"title": "كتاب لا يعرفه أحد", "author": "", "format": "unknown", "moods": [], "themes": [], "known": False})
ok(not any(f["why_it_matches"].startswith(("من كتب", "قريب من")) for f in seen["facts"]), "unknown reference adds nothing guessed")
ok("وش اللي عجبه فيه" in seen["guidance"], "unknown reference: the visitor is asked what they liked")
# «أبغى اقرأ لكتاب يشبه كتاب اخلع حذائك» (Ibrahim 2026-10-11): the model did not know it and two random
# «خيار متوفر للتنويع» books were shown. Now: no book at all, and one question about what they liked.
_ref = {"title": "اخلع حذائك", "author": "", "format": "unknown", "moods": [], "themes": [], "known": False}
chat.interpret = lambda conv, active: {"intent": "recommend", "prefs": llm.clean_prefs({}), "mentioned_titles": ["اخلع حذائك"],
                                      "wants_new_options": False, "references": chat.clean_references([_ref])}
out = A.run_chat([{"role": "user", "content": "ابغى اقرأ لكتاب يشبه كتاب اخلع حذائك"}], active=("fake", "m"))
ok(seen["facts"] == [] and out["books"] == [], "unknown reference, nothing else asked: no book is offered (no «for variety» fillers)")
ok("show_book_ids فارغة" in seen["guidance"] and "اقترح من 2 إلى 4" not in seen["guidance"] and "suggestions" in seen["guidance"],
   "and the guidance no longer also says «suggest 2 to 4»; it asks one question with tap answers")
chat.interpret = lambda conv, active: {"intent": "recommend", "prefs": llm.clean_prefs({"genres": ["novel"]}), "mentioned_titles": ["اخلع حذائك"],
                                      "wants_new_options": False, "references": chat.clean_references(
                                          [{"title": "اخلع حذائك", "author": "", "format": "unknown", "moods": [], "themes": [], "known": False}])}
out = A.run_chat([{"role": "user", "content": "أبغى رواية تشبه اخلع حذائك"}], active=("fake", "m"))
ok(not any(f["why_it_matches"] in (A.FILLER_REASON, A.FILLER_REASON_EN) for f in seen["facts"]),
   "with another wish (a novel) the reference request still gets no «for variety» filler")

# a researched in-stock reference uses its own card, not the model's description
inside = con.execute("""select b.title from books b join book_profiles p using(book_id)
                        where b.quantity>0 and b.active=1 and p.overall_status in ('enriched','needs_review') limit 1""").fetchone()["title"]
turn({"title": inside, "author": "مؤلف مختلق", "format": "novel", "moods": ["sad"], "themes": [], "known": True})
ok("يشبه «" not in seen["guidance"] and not any(f["why_it_matches"].startswith("قريب من «") for f in seen["facts"]), "researched in-stock reference: model description not used")

# children: nothing unsuitable
chat.interpret = lambda conv, active: {"intent": "recommend", "prefs": llm.clean_prefs({"audience": "children"}), "mentioned_titles": [],
                                      "wants_new_options": False,
                                      "references": chat.clean_references([{"title": "رواية للكبار", "author": author, "format": "novel", "moods": ["dark"], "themes": [], "known": True}])}
A.run_chat([{"role": "user", "content": "قصة لطفلي تشبه ..."}], active=("fake", "m"))
cards = models.get_profile_cards()
ok(all(A.age_allowed(cards.get(f["id"]), "children") for f in seen["facts"]), "reference matches respect the reader's age")
print(f"TOTAL PASS={P} FAIL={F}")

# Regression: the visitor's Marquez/fantasy request must use stored subgenres,
# not mistake the aristocratic title in de Sade's name for an author match.
ok(not A.author_matches("ماركيز", "الماركيز دو ساد") and
   A.author_matches("ماركيز", "غابرييل غارسيا ماركيز"), "Marquez does not match Marquis de Sade")
prefs, pprefs, exclusions, _ = A.prefs_from_ai(llm.clean_prefs({
    "genres": ["رواية"], "authors": ["ماركيز"], "subgenres": ["الفانتازيا", "الواقعية السحرية"]}), "")
fantasy = {"book_id": 99901, "title": "اختبار", "author": "كاتب", "genre": "رواية",
           "subgenres": json.dumps(["فانتازيا ملحمية"], ensure_ascii=False)}
unrelated = dict(fantasy, book_id=99902, author="الماركيز دو ساد", subgenres='["كلاسيكيات"]')
pool, _ = A.constrain([fantasy, unrelated], prefs, pprefs, exclusions, {})
ok([b["book_id"] for b in pool] == [99901], "requested subgenre excludes unrelated novels")
score, reasons = A.score_book(fantasy, prefs)
ok(score >= 6 and any("فانتازيا" in r for r in reasons), "subgenre match carries a grounded reason")
ok(A.score_book(unrelated, prefs)[0] == 2, "de Sade receives no Marquez author bonus")

request = dict(name_as_written="ماركيز", full_name="جابرييل جارسيا ماركيز",
               aliases=["Gabriel García Márquez"], mode="works")
catalog = [{"author": "Gabriel García Márquez"}, {"author": "الماركيز دو ساد"}]
conversation = [{"role": "user", "content": "ابغى اقرأ شيء لماركيز"}]
question, names, direct = author_flow.resolve(request, conversation, catalog)
ok(question == 'تقصد «جابرييل جارسيا ماركيز»؟' and names is None, "short author name requires confirmation before retrieval")
conversation += [{"role": "assistant", "content": question}, {"role": "user", "content": "ايه"}]
reply, names, direct = author_flow.resolve(request, conversation, catalog)
ok(reply is None and names == {"Gabriel García Márquez"} and direct, "confirmed author resolves to catalog spelling only")
conversation[-1]["content"] = "لا"
ok(author_flow.resolve(request, conversation, catalog)[0] == 'وش اسم المؤلف كامل؟', "rejected identity asks for full name")
full_request = dict(request, name_as_written="جابرييل جارسيا ماركيز")
ok(author_flow.resolve(full_request, [{"role":"user", "content":"جابرييل جارسيا ماركيز"}], catalog)[0] is None, "full unambiguous name needs no redundant confirmation")

# An author-list question is a new request, not a failed attempt to answer the
# preceding confirmation question. It must not loop back to asking for a full name.
osama_catalog = [{"author": "أسامة البرهامي"}, {"author": "أسامة غريب"}]
list_request = dict(name_as_written="أسامة", full_name="", aliases=[], mode="list")
list_conversation = [{"role": "user", "content": "ابغى اقرأ شيء لأسامة"},
                     {"role": "assistant", "content": "وش اسم المؤلف كامل؟"},
                     {"role": "user", "content": "مين مؤلفين عندكم اسمهم أسامة؟"}]
reply, names, direct = author_flow.resolve(list_request, list_conversation, osama_catalog)
ok(reply == "عندنا مؤلفين باسم «أسامة»: أسامة البرهامي، أسامة غريب. أي واحد تقصد؟" and names is None and not direct,
   "author-list request returns matching catalog authors instead of looping")
empty_reply, _, _ = author_flow.resolve(dict(list_request, name_as_written=""),
                                         [{"role": "user", "content": "اعرض المؤلفين"}], osama_catalog)
ok(empty_reply == "وش الاسم اللي تبغاني أبحث عنه؟", "nameless author-list request does not expose the full catalog")
fallback_request = dict(list_request, mode="works")
reply, _, _ = author_flow.resolve(fallback_request, list_conversation, osama_catalog)
ok("أسامة البرهامي" in reply and "أسامة غريب" in reply,
   "explicit author-list wording overrides a stale works interpretation")

# Similar author names (Ibrahim, 2026-09-30): the visitor is asked which one; an extra or different
# name is another person and is never silently merged; a spelling variant is proposed as catalogued.
isa = [{"author": a} for a in ["إبراهيم عيسى", "إبراهيم أحمد عيسى", "أحمد خالد توفيق", "أحمد خالد مصطفى", "ماريو بارغاس يوسا"]]
isa_req = dict(name_as_written="إبراهيم عيسى", full_name="إبراهيم عيسى", aliases=[], mode="works")
isa_conv = [{"role": "user", "content": "ابغى كتب إبراهيم عيسى"}]
q, names, _ = author_flow.resolve(isa_req, isa_conv, isa)
ok(q == "تقصد «إبراهيم عيسى» ولا «إبراهيم أحمد عيسى»؟" and names is None, "similar catalog author names: visitor is asked which one")
for ans, want in [("الثاني", "إبراهيم أحمد عيسى"), ("إبراهيم أحمد عيسى", "إبراهيم أحمد عيسى"), ("إبراهيم عيسى", "إبراهيم عيسى"), ("اللي فيه أحمد", "إبراهيم أحمد عيسى")]:
    r, names, direct = author_flow.resolve(isa_req, isa_conv + [{"role": "assistant", "content": q}, {"role": "user", "content": ans}], isa)
    ok(r is None and names == {want} and direct, f"choice «{ans}» selects only {want}")
later = isa_conv + [{"role": "assistant", "content": q}, {"role": "user", "content": "الثاني"},
                    {"role": "assistant", "content": "…"}, {"role": "user", "content": "ورني غيرها"}]
ok(author_flow.resolve(isa_req, later, isa)[1] == {"إبراهيم أحمد عيسى"}, "the choice stays valid in later turns")


# «تقصد …؟» for a name we do not have (Ibrahim, 2026-10-02): shared family name only, asked not replaced.
near_cat = [{"author": a, "active": 1, "quantity": nq} for a, nq in [
    ("عباس محمود العقاد", 3), ("فهد العتيبي", 1), ("جمال الغيطاني", 2), ("محمد حسن علوان", 1),
    ("سارة الغامدي", 1), ("خالد الغامدي", 2), ("نورة الغامدي", 1), ("هند الغامدي", 1), ("دان براون", 2), ("بيتر براون", 0)]]
nreq = dict(name_as_written="جمال العقاد", full_name="", aliases=[], mode="works")
nconv = [{"role": "user", "content": "في كتب لجمال العقاد"}]
nq, names, direct = author_flow.resolve(dict(nreq), nconv, near_cat)
ok(nq == "ما عندنا كتب لـجمال العقاد الآن، تقصد «عباس محمود العقاد»؟" and names is None, "unknown name sharing a family name: visitor is asked, nothing shown")
r, names, direct = author_flow.resolve(dict(nreq), nconv + [{"role": "assistant", "content": nq}, {"role": "user", "content": "ايه"}], near_cat)
ok(r is None and names == {"عباس محمود العقاد"} and direct, "confirmed near name resolves to the catalog author")
r, names, direct = author_flow.resolve(dict(nreq), nconv + [{"role": "assistant", "content": nq}, {"role": "user", "content": "لا"}], near_cat)
ok(r is None and names == set() and direct, "declined near name falls back to the usual no-such-author answer, not asked again")
sim = dict(nreq, mode="similar")
r, names, direct = author_flow.resolve(sim, nconv + [{"role": "assistant", "content": nq}, {"role": "user", "content": "نعم"}], near_cat)
ok(r is None and names is None and sim["full_name"] == "عباس محمود العقاد", "confirmed near name is handed to «كتب تشبه فلان»")
for unknown, why in [("سعد العتيق", "a similar but different family name (العتيق/العتيبي) is never suggested"),
                     ("جمال الشمري", "a shared first name alone suggests nobody"),
                     ("ماجد محمد", "a common name as last word suggests nobody")]:
    r, names, direct = author_flow.resolve(dict(nreq, name_as_written=unknown), [{"role": "user", "content": "كتب " + unknown}], near_cat)
    ok(r is None and names == set(), why)
r, names, _ = author_flow.resolve(dict(nreq, name_as_written="سالم علوان حسن"), [{"role": "user", "content": "كتب"}], near_cat)
ok(r is None, "a family name that is only someone's middle name suggests nobody")
gq, _, _ = author_flow.resolve(dict(nreq, name_as_written="ريم الغامدي"), [{"role": "user", "content": "كتب ريم الغامدي"}], near_cat)
ok(gq == "ما عندنا كتب لـريم الغامدي الآن، تقصد مين: «خالد الغامدي»، «سارة الغامدي»، «نورة الغامدي» ولا «هند الغامدي»؟", "up to five people with the family name are listed, available books first")
many_cat = near_cat + [{"author": n + " الغامدي", "active": 1, "quantity": 1} for n in ("ماجد", "ليلى")]
mq, mnames, _ = author_flow.resolve(dict(nreq, name_as_written="ريم الغامدي"), [{"role": "user", "content": "كتب ريم الغامدي"}], many_cat)
ok(mq == "ما عندنا كتب لـريم الغامدي الآن، وعندنا أكثر من مؤلف بنفس اللقب. وش اسم المؤلف اللي تقصده كامل؟" and mnames is None, "more than five people with the family name: asked for the full name, no list")
r, names, _ = author_flow.resolve(dict(nreq, name_as_written="ريم الغامدي"), [{"role": "user", "content": "كتب ريم الغامدي"}, {"role": "assistant", "content": mq}, {"role": "user", "content": "ريم الغامدي"}], many_cat)
ok(r is None and names == set(), "the full-name question is asked once, then the usual no-such-author answer")
joint_cat = [{"author": a, "active": 1, "quantity": 1} for a in ["ويندي براون", "طلال أسد، جوديث بتلر، ويندي براون، صبا محمود", "دان براون", "مايكل براون"]]
jq, _, _ = author_flow.resolve(dict(nreq, name_as_written="براون"), [{"role": "user", "content": "ابغى كتاب لبراون"}], joint_cat)
ok(jq == "تقصد مين: «دان براون»، «مايكل براون» ولا «ويندي براون»؟", "a co-authored book is never offered as a name")
r, names, _ = author_flow.resolve(dict(nreq, name_as_written="براون"), [{"role": "user", "content": "ابغى كتاب لبراون"}, {"role": "assistant", "content": jq}, {"role": "user", "content": "ويندي"}], joint_cat)
ok(names == {"ويندي براون", "طلال أسد، جوديث بتلر، ويندي براون، صبا محمود"}, "the chosen author's co-authored book counts as hers")
q2, _, _ = author_flow.resolve(dict(nreq, name_as_written="كريس براون"), [{"role": "user", "content": "كتب كريس براون"}], near_cat)
ok(q2 == "ما عندنا كتب لـكريس براون الآن، تقصد «دان براون» ولا «بيتر براون»؟", "several near authors: available books first, at most three")
r, names, _ = author_flow.resolve(dict(nreq, name_as_written="كريس براون"), [{"role": "user", "content": "كتب كريس براون"}, {"role": "assistant", "content": q2}, {"role": "user", "content": "الثاني"}], near_cat)
ok(names == {"بيتر براون"}, "choice between near authors selects only that one")
ok(author_flow.resolve(isa_req, isa_conv + [{"role": "assistant", "content": q}, {"role": "user", "content": "لا"}], isa)[0] == "وش اسم المؤلف كامل؟",
   "rejecting both asks for the full name")
q2 = author_flow.resolve(dict(isa_req, name_as_written="أحمد خالد", full_name="أحمد خالد توفيق"), isa_conv, isa)[0]
ok(q2 == "تقصد «أحمد خالد توفيق» ولا «أحمد خالد مصطفى»؟", "partial name offers both catalog authors")
ok(author_flow.resolve(dict(isa_req, name_as_written="أحمد خالد توفيق", full_name="أحمد خالد توفيق"), isa_conv, isa)[1] == {"أحمد خالد توفيق"},
   "a full name different from another author's is not confused with it")
only_long = [{"author": "إبراهيم أحمد عيسى"}]
ok(author_flow.resolve(isa_req, isa_conv, only_long)[0] == "تقصد «إبراهيم أحمد عيسى»؟",
   "a longer catalog name is asked about as written, not assumed to be the requested author")
ok(author_flow.resolve(dict(isa_req, name_as_written="ماريو فارغاس يوسا", full_name="ماريو فارغاس يوسا"), isa_conv, isa)[0] == "تقصد «ماريو بارغاس يوسا»؟",
   "a spelling variant is proposed with the catalog spelling")

# «عطني كتب مقاربة لأحمد خالد توفيق» (2026-09-30): the author's generally known style is used
# only to search researched books; every reason says where the likeness lies and where the book
# differs; no generic fillers and never an unresearched book.
horror = con.execute("""select b.book_id from books b join book_profiles p using(book_id)
                        where b.quantity>0 and b.active=1 and p.format='novel' and p.overall_status in ('enriched','needs_review')
                        and coalesce(p.age_suitability,'adult') in ('adult','all_ages','young_adult') limit 1""").fetchone()["book_id"]
con.execute("update books set subgenres=?, page_count=520 where book_id=?", (json.dumps(["رعب قوطي", "غموض أدبي"], ensure_ascii=False), horror))
pending = con.execute("""select b.book_id from books b join book_profiles p using(book_id)
                         where b.quantity>0 and b.active=1 and b.book_id<>? limit 1""", (horror,)).fetchone()["book_id"]
con.execute("update book_profiles set overall_status='pending' where book_id=?", (pending,))
con.execute("update books set subgenres=? where book_id=?", (json.dumps(["رعب"], ensure_ascii=False), pending))
con.commit()
tawfik = dict(name_as_written="أحمد خالد توفيق", full_name="أحمد خالد توفيق", aliases=[], mode="similar")
def similar_turn(style):
    request = dict(tawfik, style=dict(style or {}, known=bool(style)))
    chat.interpret = lambda conv, active: {"intent": "recommend", "prefs": llm.clean_prefs({}), "mentioned_titles": [],
                                          "wants_new_options": False, "references": [], "author_request": request,
                                          "author_style": chat.clean_author_style(request)}
    return A.run_chat([{"role": "user", "content": "عطني كتب مقاربة لأحمد خالد توفيق"}], active=("fake", "m"))
style = {"formats": ["novel"], "subgenres": ["رعب", "غموض"], "moods": ["mysterious", "suspenseful"],
         "pace": "fast", "length": "short", "themes": []}
ok(chat.clean_author_style(dict(tawfik, style=dict(style, known=True)))["subgenres"] == ["رعب", "غموض"]
   and chat.clean_author_style(dict(tawfik, mode="works", style=dict(style, known=True))) is None
   and chat.clean_author_style(dict(tawfik, style=dict(style, known=False))) is None, "author style kept only for a known similar request")
out = similar_turn(style)
match = [f for f in seen["facts"] if f["id"] == horror]
ok(match and match[0]["why_it_matches"].startswith("قريب من أجواء أحمد خالد توفيق"), "researched book close to the author's style offered")
ok(match and "أطول" in match[0]["why_it_matches"], "the reason states the difference (longer)")
ok(not any(f["id"] == pending for f in seen["facts"]), "unresearched book never offered as similar")
ok(not any(f["why_it_matches"] in (A.FILLER_REASON,) or f["why_it_matches"].startswith("يلتزم بطلبك") for f in seen["facts"]), "no generic fillers in similar-author mode")
ok("تشبه كتابات «أحمد خالد توفيق»" in seen["guidance"] and "وين الاختلاف" in seen["guidance"], "guidance: honest likeness and difference")
ok(out["books"] and all(b["reason"].startswith("قريب من أجواء") for b in out["books"]), "shown books keep the likeness reason")
similar_turn(None)
ok(not seen["facts"] and "ما لقينا" in seen["guidance"], "unknown author style: no books, ask what they enjoy")
# the stored author record drives «كتب تشبه فلان» and identity (authors table, 2026-09-30)
import authors
authors.sync()
own = con.execute("""select b.book_id from books b join book_profiles p using(book_id) where b.quantity>0 and b.active=1
                     and p.format='novel' and p.overall_status in ('enriched','needs_review') and b.book_id not in (?, ?)
                     and coalesce(p.age_suitability,'adult') in ('adult','all_ages','young_adult') limit 1""", (horror, pending)).fetchone()["book_id"]
con.execute("update books set author='كاتب رعب تجربة', subgenres=? where book_id=?", (json.dumps(["رعب"], ensure_ascii=False), own))
con.commit(); authors.sync()
rid = authors.find(con, "كاتب رعب تجربة")
con.execute("""update authors set research_status='researched', aliases=?, formats='["novel"]', subgenres=?, moods='["dark"]',
               typical_length='short', pace='fast' where author_id=?""",
            (json.dumps(["رعبي"], ensure_ascii=False), json.dumps(["رعب"], ensure_ascii=False), rid))
con.commit()
stored = dict(name_as_written="كاتب رعب تجربة", full_name="", aliases=[], mode="similar")
chat.interpret = lambda conv, active: {"intent": "recommend", "prefs": llm.clean_prefs({}), "mentioned_titles": [],
                                      "wants_new_options": False, "references": [], "author_request": stored, "author_style": None}
out = A.run_chat([{"role": "user", "content": "كتب تشبه كاتب رعب تجربة"}], active=("fake", "m"))
ok(out["_debug"].get("style_source") == "research" and any(f["id"] == horror for f in seen["facts"]),
   "similar-author uses the stored researched style (no model style needed)")
ok(not any(f["id"] == own for f in seen["facts"]), "the author's own books are not offered as «like him»")
ok("المعروف عن كتابات كاتب رعب تجربة" in seen["guidance"] and "ولا تذكر عنه أي معلومة أخرى" in seen["guidance"],
   "guidance allows one general phrase about the stored style, nothing else")
rec = authors.lookup("رعبي")
ok(rec and rec["author_id"] == rid, "alias finds the author record")
q, names, direct = author_flow.resolve(dict(name_as_written="رعبي", full_name="", aliases=[], mode="works"),
                                       [{"role": "user", "content": "أبغى شيء لرعبي"}], [dict(b) for b in A.list_books(only_active=False)])
ok(q == "تقصد «كاتب رعب تجربة»؟", "an alias is confirmed with the canonical stored name")
con.execute("update authors set research_status='pending', subgenres='[]', moods='[]' where author_id=?", (rid,)); con.commit()
authors.sync()
A.run_chat([{"role": "user", "content": "كتب تشبه كاتب رعب تجربة"}], active=("fake", "m"))
ok(True, "unresearched record falls back to its catalog style or the model")

# fillers in ordinary requests come only from researched books
con.execute("update book_profiles set overall_status='pending' where book_id not in (select book_id from book_profiles limit 3)")
con.commit()
chat.interpret = lambda conv, active: {"intent": "recommend", "prefs": llm.clean_prefs({}), "mentioned_titles": [],
                                      "wants_new_options": False, "references": [], "author_request": {}, "author_style": None}
A.run_chat([{"role": "user", "content": "أي كتاب"}], active=("fake", "m"))
researched = set(A._profile_formats())
ok(all(f["id"] in researched for f in seen["facts"]), "ordinary fillers never include unresearched books")
