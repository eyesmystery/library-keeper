"""[QADFIX13] Fixes approved by Ibrahim 2026-10-22 23:0x («قوم بالاصلاح لكلها حسب التوصية») from AGY-20261021-01,
AGY-20261022-01 and the Codex audit of 2026-10-22. Pure checks (no model, no live database)."""
import json, os, subprocess, sys, time
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import app as A, llm as L
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

_src = open(A.__file__, encoding="utf-8").read()

# --- #4 a book with no subgenre recorded is read from its card's themes (case 32.1 «Hugo award winning sci-fi»)
_dune = {"book_id": 1, "title": "Dune", "subgenres": None}
_card = {"themes": ["الصحراء", "الإمبراطورية", "الخيال العلمي"]}
_want = {"subgenres": ["خيال علمي"]}
ok(A.matching_subgenres(_dune, _want) == [], "#4 without the card an empty subgenre field matches nothing (as before)")
ok(A.matching_subgenres(_dune, _want, card=_card) == ["الخيال العلمي"], "#4 with its card, «Dune» answers «خيال علمي» from its themes")
_own = {"book_id": 2, "title": "x", "subgenres": json.dumps(["رواية تاريخية"], ensure_ascii=False)}
ok(A.matching_subgenres(_own, _want, card=_card) == [], "#4 a book's own subgenres still decide when it has them")
ok(A.matching_subgenres(_dune, {"subgenres": ["رومانسية"]}, card=_card) == [], "#4 themes answer only the wished subgenre")
ok("score_book(book, prefs, card)" in _src and "matching_subgenres(b, prefs, card=card)" in _src,
   "#4 scoring and the constraint both pass the card")

# --- #2 a carried subject keeps the story wish (cases 22.2–22.3)
ok("(set(latest.get(\"genres\") or ()) | _story_now)" in _src, "#2 «شي واقعي عن المدرسة»: the story wish counts as a form here too")
ok("_form_genres = set(prefs.get(\"genres\") or ()) & {\"رواية\"" in _src,
   "#2 «أبيها بالعربي» (subject from an earlier message): the request's form is kept")

# --- #1 every edition's links under the folded work; like → similar first; «أحدث منه» (case 6.1)
ok("relations.for_books(_fact_ids + list(_ed_to))" in _src, "#1 the links of all editions of a folded work are read")
ok("key=lambda r: r[\"relation_type\"] != \"similar\"" in _src, "#1 a book named as the model: similar books before the author's")
ok("_newer_than" in _src, "#1 «more recent» keeps only books first published after it")
ok("_like_pre" in _src and "_like_first" in _src, "#1 like + newer: no sort of the whole stock by year; the like-books come first")
ok(A.compared_title("ما عندكم أقدم من الجريمة والعقاب؟", "الجريمة والعقاب"), "compare: the title as typed")
_fake = {7: {"book_id": 7, "title": "الجريمة والعقاب", "author": "دوستويفسكي", "quantity": 1, "active": 1}}
_orig_mt = A.match_titles
try:
    A.match_titles = lambda m, books, max_rank=3: [_fake[7]] if books is _fake else []
    ok(A.compared_title("ما عندكم أقدم من الجريمة والعقاب؟", "جريمة وعقاب", _fake),
       "compare: the model's other form of the title («جريمة وعقاب») is matched through the catalog title")
    ok(not A.compared_title("أبي الجريمة والعقاب", "جريمة وعقاب", _fake), "compare: a plain ask is not a comparison")
finally:
    A.match_titles = _orig_mt

# --- #3 one failed run of the Codex check is not a logout
_orig_run, _orig_path = subprocess.run, L.codex_path
def _boom(*a, **k):
    raise subprocess.TimeoutExpired(cmd="codex", timeout=10)
try:
    L.codex_path = lambda: "/bin/codex-fake"
    subprocess.run = _boom
    L._codex_status.clear(); L._codex_status.update(at=0.0, value=None, last_ok=time.time() - 30)
    ok(L.codex_login_status()[0] is True, "#3 a timeout 30 s after a confirmed login keeps Codex ready")
    ok(L._codex_status.get("transient") is True, "#3 … and is marked transient")
    L._codex_status.clear(); L._codex_status.update(at=0.0, value=None)
    v = L.codex_login_status()
    ok(v == (False, "تعذر تشغيل Codex"), "#3 with no recent login the failure is reported")
    calls = []
    def _good(*a, **k):
        calls.append(1)
        return subprocess.CompletedProcess(a, 0, stdout="Logged in using ChatGPT", stderr="")
    subprocess.run = _good
    ok(L.codex_login_status()[0] is False and not calls, "#3 the failure is kept for a moment (no new process at once)")
    L._codex_status["at"] -= L.CODEX_TRANSIENT_TTL + 1
    ok(L.codex_login_status()[0] is True and calls, "#3 after 5 seconds (not a minute) the check runs again and recovers")
    ok(L._codex_status.get("last_ok", 0) > time.time() - 5, "#3 a confirmed login is remembered")
    def _out(*a, **k):
        return subprocess.CompletedProcess(a, 1, stdout="Not logged in", stderr="")
    subprocess.run = _out
    ok(L.codex_login_status(max_age=0)[0] is False, "#3 a real «not logged in» answer is never covered by the grace")
finally:
    subprocess.run, L.codex_path = _orig_run, _orig_path
    L._codex_status.clear(); L._codex_status.update(at=0.0, value=None)

# --- #5 no internal test label in text that reaches the reply
_sp = json.load(open(os.path.join(os.path.dirname(A.__file__), "series_parts.json"), encoding="utf-8"))
_notes = [s.get(k) or "" for s in _sp["series"] for k in ("note_ar", "note_en")]
ok(not any(("QAD" in n or "الحالة" in n or "QADFIX" in n) for n in _notes), "#5 series notes carry no test-round labels")

print(f"\nPASS={P} FAIL={F}")
sys.exit(1 if F else 0)
