"""A subject narrows the books; «أكتوبر» alone is the month (Ibrahim 2026-10-13, staff chat 07:24)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import sqlite3
import llm, chat, app as A
# the frozen test catalogue has no book on these subjects: three novels are given summaries that do
con = sqlite3.connect(models.DB_PATH)
_novels = [b for b in map(dict, A.list_books(only_active=False)) if A.is_available(b) and (b.get("genre") or "") == "رواية"][:3]
NAHL, WAR, MONTH = (b["book_id"] for b in _novels)
for bid, text in ((NAHL, "رواية تجري أحداثها بين خلايا النحل وقرى الجبل."),
                  (WAR, "رواية عن جنود عبروا القناة في حرب أكتوبر 1973."),
                  (MONTH, "رواية هادئة تجري في صباحات أكتوبر الباردة بين أوراق الشجر.")):
    con.execute("update books set summary=?, hook=NULL where book_id=?", (text, bid))
con.commit()
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

seen = {}
def fake_respond(conv, facts_json, guidance, active, *a):
    seen["facts"] = json.loads(facts_json); seen["guidance"] = guidance
    return {"reply": "x", "show_book_ids": [f["id"] for f in seen["facts"]][:4], "suggestions": []}
chat.respond = fake_respond
def interp(prefs, intent="recommend"):
    chat.interpret = lambda conv, active, *a: {"intent": intent, "prefs": llm.clean_prefs(prefs), "mentioned_titles": [],
                                              "wants_new_options": False, "references": [], "author_request": {}}
def say(*msgs):
    conv = [{"role": "user" if i % 2 == 0 else "assistant", "content": m} for i, m in enumerate(msgs)]
    return A.run_chat(conv, active=("fake", "m"))

ok(A.months_in("أكتوبر") == ["اكتوبر"] and A.MONTH_SEASON["اكتوبر"] == "autumn", "October is a month of autumn")
ok(A.month_as_event("اكتوبر", "نثر درويش بعد حرب أكتوبر.") and A.month_as_event("اكتوبر", "حرب السادس من أكتوبر")
   and A.month_as_event("اكتوبر", "يوليو 1911 - أكتوبر 1931") and not A.month_as_event("اكتوبر", "في صباح بارد من أكتوبر"),
   "«حرب أكتوبر» / «أكتوبر 1931» name an event or a date, «صباح من أكتوبر» the month")

interp({"genres": ["رواية"], "unsupported_requests": ["خلايا النحل"]})
out = say("ابغى رواية عن خلايا النحل")
ids = [f["id"] for f in seen["facts"]]
ok(ids == [NAHL], f"only the book on the subject, no «a novel, as you asked» beside it {[(f['title'], f.get('why_it_matches')) for f in seen['facts']][:5]}")

interp({"genres": ["رواية"], "unsupported_requests": ["خلايا النحل"]})
out = say("ابغى رواية عن خلايا النحل", "x", "هل في اقتراحات اخرى")
ok(all(f["id"] == NAHL for f in seen["facts"]) and all(b["book_id"] == NAHL for b in out["books"]),
   f"«هل في اقتراحات أخرى» keeps the subject: no random novel «للتنويع» {[f['title'] for f in seen['facts']][:5]}")

interp({"genres": ["رواية"], "unsupported_requests": ["زقزوقات البنفسجيين"]})
out = say("رواية عن زقزوقات البنفسجيين")
ok(out["books"] == [] and "ما لقينا" in seen["guidance"] and "لا تعرض أي كتاب" in seen["guidance"],
   f"nothing on the subject: no book, say so and ask {[b['title'] for b in out['books']]}")

interp({"unsupported_requests": ["أكتوبر"]}, intent="clarify")
out = say("ايش الكتب اللي تتكلم عن اكتوبر؟")
ids = [f["id"] for f in seen["facts"]]
ok(MONTH in ids and WAR not in ids and "الشهر نفسه" in seen["guidance"] and out["books"],
   f"«أكتوبر» with no war said: the month, not the war; books shown, not a question first {ids}")
interp({"unsupported_requests": ["حرب أكتوبر"]})
say("ابغى كتب عن حرب اكتوبر")
ok(WAR in [f["id"] for f in seen["facts"]], "«حرب أكتوبر» asked: the war book")

# staff chat 2026-10-13 23:04 and 22:59
ok(A.new_release_said("ابحث عن روايات صدرت مؤخرا") and A.new_release_said("وش احدث الروايات عندكم")
   and A.new_release_said("كتب طلعت هالسنة") and not A.new_release_said("ابغى رواية حزينة"),
   "«روايات صدرت مؤخرًا» / «أحدث الروايات» / «طلعت هالسنة» ask for new releases")
import chat_rules
ok(chat_rules.part_asked("هل له اجزاء ثانية؟") == "all" and chat_rules.part_asked("وش أجزاؤه؟") == "all"
   and chat_rules.part_asked("والجزء الثاني؟") == 2, "«هل له أجزاء ثانية؟» asks for the rest of the series")
_bk = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
_one = next(iter(_bk.values()))
_s = dict(_one, series_name="سلسلة تجربة الاختبار", series_position=1)
_t = dict(next(b for b in _bk.values() if b["book_id"] != _one["book_id"]), series_name="سلسلة تجربة الاختبار", series_position=2)
ok([b["book_id"] for b in chat_rules.series_others(_s, {_s["book_id"]: _s, _t["book_id"]: _t})] == [_t["book_id"]],
   "the other parts of a series from our records")

print(f"TOTAL PASS={P} FAIL={F}")
sys.exit(1 if F else 0)
