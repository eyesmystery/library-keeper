"""[QADFIX20] The minor items of the QAD round of 2026-10-25 (Ibrahim 2026-10-25 22:0x «صلحها»; AGY-20261025-01):
«بدون مشاهد للكبار» keeps the young reader (3.2), the grown-up reader said later stays (22.3), the visitor addressed in the
masculine (22.3), a page count from another edition (40.1), an unlisted prize name is not a subject (33.1), a shelf label read
by the summary (21.3), a clear mood wish answered with books (15.1), no «Do you mean …?» for an author we have none of (26.1),
no family-name question after the visitor corrected his own spelling (28.2), no invented wish / no likeness without a shared
trait (12.1, 7.2)."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, chat as C, chat_rules as R, author_flow as AF
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

R.set_model_parent(False)

# --- 3.2: «للكبار» under a negation is content to keep out, not the reader
ok(not A.adult_said("بدون أي مشاهد للكبار، تكون نظيفة"), "3.2: «بدون أي مشاهد للكبار» is not a grown-up reader")
ok(A.adult_said("أبي رواية للكبار"), "3.2: «رواية للكبار» still is")
ok(A.adult_said("تاريخ العثمانيين، لي أنا مو له"), "3.2: «لي أنا مو له» still makes the visitor the reader")
ok(A._chat_audience(["بنت خالتي بثالث متوسط تبي رواية فيها قصة حب", "بدون أي مشاهد للكبار، تكون نظيفة"],
                    {"audience": "young_adult", "genres": ["رواية"]}, False) == "young_adult",
   "3.2: the girl of 14 stays the reader")

# --- 22.3: the grown-up reader said in the second message stays in the third
_msgs = ["قصة لولدي عمره ٦ عن الصدق", "وأنا أبي كتاب طبخ لي", "قلت كتاب طبخ! وصفات أكل"]
ok(A._chat_audience(_msgs, {"audience": "adult", "genres": []}, False) is None, "22.3: no child badge on «وصفات أكل»")
ok(A._chat_audience(_msgs, {"audience": "children", "genres": []}, False) is None, "22.3: not even when the model carries «children»")
ok(A._chat_audience(_msgs[:2] + ["وعندكم قصة عن الحيوانات له؟"], {"audience": "children", "genres": []}, False) == "children",
   "22.3: «… له؟» names the child again")
ok(A._chat_audience(["قصة لولدي عمره ٦ عن الصدق", "وعن الحيوانات بعد"], {"audience": "children", "genres": []}, False) == "children",
   "22.3: a follow-up without a grown-up keeps the child")

# --- 22.3: masculine address
ok(R.masculine_address("معك حق، كنتِ تقصدين وصفات أكل؛ وش نوع الوصفات اللي تبينها؟")
   == "معك حق، كنت تقصد وصفات أكل؛ وش نوع الوصفات اللي تبيها؟", "22.3: «كنتِ تقصدين … تبينها» → «كنت تقصد … تبيها»")
ok(R.masculine_address("تسعين صفحة") == "تسعين صفحة", "22.3: other words untouched")

# --- 28.2: the visitor corrected his own spelling
_conv = [{"role": "user", "content": "روايات يوسف زيدن"}, {"role": "assistant", "content": "ما لقيت عندنا مؤلفًا بهذا الاسم. تبغى أقترح لك بدائل؟"},
         {"role": "user", "content": "أقصد يوسف زيدان، صاحب عزازيل"}]
ok(AF._respelt_by_visitor("يوسف زيدان", _conv), "28.2: «يوسف زيدن» then «يوسف زيدان» is the same person respelt")
ok(not AF._respelt_by_visitor("يوسف زيدان", [{"role": "user", "content": "روايات جرجي زيدان"}, _conv[1], _conv[2]]),
   "28.2: another first name is not")

_src = open(os.path.join(os.path.dirname(A.__file__), "app.py"), encoding="utf-8").read()
_af = open(os.path.join(os.path.dirname(A.__file__), "author_flow.py"), encoding="utf-8").read()
ok('and not _respelt_by_visitor(written, conversation)' in _af, "28.2: no family-name question then")
ok("if ambiguous and not options and known_record and full" in _af, "26.1: no «Do you mean …?» for an author we have none of")
ok('facts[-1]["pages_approx_other_edition"]' in _src, "40.1: the page count of another edition of the work")
ok("_prize_phrases" in _src and "topics.searchable([u for u in unsupported if u not in _prize_phrases]" in _src,
   "33.1: an unlisted prize name is not searched as a subject")
ok("_relaxed_said = [x for x in relaxed_subgenres if x not in topics_found]" in _src, "21.3: a label found by subject is not «unconfirmed»")
ok("_unsup_said = [u for u in unsupported if u not in (relaxed_subgenres or ())]" in _src, "21.3: nor «I found nothing about it»")
ok('_wish_kinds >= 2' in _src, "15.1: a mood + pace wish is answered with books")
ok("pages_approx_other_edition" in C.RESPOND_SYSTEM_AR and "pages_approx_other_edition" in C.RESPOND_SYSTEM_EN, "40.1: the reply rules know it")
ok("ولا تنسب للزائر رغبة ما قالها" in C.RESPOND_SYSTEM_AR and "Never give the visitor a wish they did not state" in C.RESPOND_SYSTEM_EN,
   "12.1: no invented wish")
ok("فاذكر وجه الشبه" in C.RESPOND_SYSTEM_AR and "name what they share" in C.RESPOND_SYSTEM_EN, "7.2: likeness only with a shared trait")
ok("إذا نقص الرقم عند أحدهما فلا تقل إن الثاني أقصر" in C.RESPOND_SYSTEM_AR, "12.1: no «shorter» against a missing count")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
