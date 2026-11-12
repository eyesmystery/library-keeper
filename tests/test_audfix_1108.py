"""[AUDFIX 2026-11-08] Codex audit P2: the QADFIX31 Nobel guidance read an empty honors lookup as «the author did not win» and
forbade any uncertainty, also for a book with no linked author record. Now three states: a recorded win, a sourced «not in our
list» (a linked, named author: our Nobel rows are the official nobelprize.org list matched to our author records), and unknown
(no linked or a missing author) — never stated as a non-win. The block runs here from the source (by AST, as the audit did)."""
import ast, os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

src = open(A.__file__, encoding="utf-8").read()
fn = next(x for x in ast.parse(src).body if isinstance(x, ast.FunctionDef) and x.name == "_run_chat_once")
node = next(x for x in ast.walk(fn) if isinstance(x, ast.If) and "nobel" in ast.unparse(x.test))
block = compile(ast.Module(body=[node], type_ignores=[]), "nobel_block", "exec")

def run(books, linked, won, language="ar"):
    ns = {"re": A.re, "normalize": A.normalize, "user_messages": ["صحيح إن الكتاب فاز بجائزة نوبل؟"], "asked_books": books,
          "honors": type("H", (), {"matches_award": staticmethod(lambda bid, keys: ["جائزة نوبل للآداب"] if bid in won else [])}),
          "book_author_map": lambda *a, **k: {b: [1] for b in linked}, "author_flow": A.author_flow, "chat_rules": A.chat_rules,
          "guidance": "", "en": language == "en", "language": language}
    exec(block, ns)
    return ns["guidance"]

unk = {"book_id": 9001, "title": "سجل ناقص الجوائز", "author": "مؤلف صناعي مجهول"}
g = run([unk], linked=set(), won=set())
ok("ما فاز بها" not in g and "ليس ضمن الفائزين" not in g and "ولا تقل إنه فاز ولا إنه ما فاز" in g, "audit P2: no linked author → never «did not win»")
ok("ما نقدر نحدد" in g, "audit P2: unknown is said as unknown")
g = run([unk], linked=set(), won=set(), language="en")
ok("not among the Nobel laureates" not in g and "cannot tell" in g and "never say he did or did not win" in g, "audit P2 (en): unknown, never a non-win")
nobody = {"book_id": 9002, "title": "كتاب", "author": "غير معروف"}
g = run([nobody], linked={9002}, won=set())
ok("ما نقدر نحدد" in g and "ليس ضمن الفائزين" not in g, "an «غير معروف» author is unknown even when linked")
known = {"book_id": 9003, "title": "موسم الهجرة إلى الشمال", "author": "الطيب صالح"}
g = run([known], linked={9003}, won=set())
ok("ليس ضمن الفائزين" in g and "قائمة nobelprize.org" in g, "a linked, named author not in the list: a sourced «not in our list»")
ok("جائزة نوبل للآداب تُمنح للكاتب" in g, "the prize goes to a writer, said kindly (QADFIX31 kept)")
win = {"book_id": 9004, "title": "الشيخ والبحر", "author": "إرنست همنغواي"}
g = run([win], linked={9004}, won={9004})
ok("فاز بها (author_awards)" in g and "ما نقدر نحدد" not in g, "a recorded win stays a win")
g = run([win, unk], linked={9004}, won={9004}, language="en")
ok("did win it" in g and "cannot tell" in g, "mixed: one win, one unknown")
print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
