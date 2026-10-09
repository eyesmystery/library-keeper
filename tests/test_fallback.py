"""Keyword fallback (no AI model): negation, whole-word matching, dialect, message-by-message
understanding, and constraints that are never broken to fill the list.
Cases: tests/fallback_cases.json (plan item 3 acceptance set)."""
import os, sys, json
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models, app as A, keyword_prefs as K
from auth_helper import login
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# whole-word matching
wm = lambda w, t: K.word_match(K.normalize(w), K.normalize(t))
ok(not wm("سريع", "سر") and wm("السر", "سر") and wm("وسر", "سر"), "سر matches only the word سر (not سريع)")
ok(not wm("حبيب", "حب") and wm("الحب", "حب"), "حب does not match حبيب")
ok(not wm("مشاعر", "شعر") and not wm("أشعر", "شعر") and wm("الشعر", "شعر") and wm("شعري", "شعر"), "شعر vs أشعر/مشاعر")
ok(wm("حزينة", "حزين") and wm("بالحزن", "حزن"), "stems and attached prefixes")
ok(K.negated(K.normalize("ما أبغى شي حزين").split(), 3) and not K.negated(K.normalize("أبغى شي حزين").split(), 2), "negation window")

c = login(A.app.test_client())
cards = models.get_profile_cards()
books = {b["book_id"]: dict(b) for b in models.list_books(only_active=False)}
cases = json.load(open("../tests/fallback_cases.json", encoding="utf-8"))

def has(p, k, v):
    return (all(x in (p.get(k) or []) for x in v) if isinstance(v, list) else p.get(k) == v)

bad_cases = []
for case in cases:
    msgs = case.get("chat") or [case["q"]]
    label = " / ".join(msgs)
    fails = []
    p = A.local_understand(msgs)
    for k, v in (case.get("understood") or {}).items():
        if not has(p, k, v):
            fails.append(f"understood {k}={v} got {p.get(k)}")
    for k, v in (case.get("never") or {}).items():
        if (any(x in (p.get(k) or []) for x in v) if isinstance(v, list) else p.get(k) == v):
            fails.append(f"never {k}={v}")
    for t in case.get("never_tokens", []):
        if K.normalize(t) in p["title_keywords"]:
            fails.append(f"token {t} used for title search")
    if "chat" in case:
        out = c.post("/api/chat", json={"messages": [{"role": "user", "content": m} for m in msgs]}).get_json()
        res, msg = out["books"], out["reply"]
    else:
        out = c.post("/api/recommend", json={"query": case["q"]}).get_json()
        res, msg = out["results"], out.get("message") or ""
    if len(res) > 4:
        fails.append("more than 4 books")
    for b in res:
        bid, card = b["book_id"], cards.get(b["book_id"])
        if not (books[bid]["active"] and books[bid]["quantity"] > 0):
            fails.append(f"unavailable {b['title']}")
        if b.get("match") == "filler" and "كما طلبت" in b["reason"]:
            fails.append("filler claims a match")
        if "genre" in case and b["genre"] not in case["genre"]:
            fails.append(f"genre {b['genre']} ({b['title']})")
        if case.get("short") and A.is_long(b, card):
            fails.append(f"long book {b['title']}")
        for m in case.get("not_mood", []):
            if A.excluded_by_card(card, {"moods": {m}, "weight": set()}):
                fails.append(f"excluded mood {m} in {b['title']}")
            if "أجواؤه " + A.MOOD_LABELS_FEM[m] in b["reason"]:
                fails.append(f"reason claims {m}")
        for w in case.get("not_weight", []):
            if A.excluded_by_card(card, {"moods": set(), "weight": {w}}):
                fails.append(f"excluded weight {w} in {b['title']}")
        for a in case.get("avoid", []):
            if A._medium_or_better(card, "sensitive_content", a):
                fails.append(f"content {a} in {b['title']}")
        for key in ("audience", "audience_ok"):
            if key in case and not A.age_allowed(card, case[key]):
                fails.append(f"age not suitable: {b['title']}")
    if "author" in case and not (res and res[0]["author"] == case["author"]):
        fails.append(f"author first: got {res[0]['author'] if res else None}")
    if case.get("not_understood") and "ما قدرت أفهم" not in msg:
        fails.append("did not say it did not understand")
    if not case.get("not_understood") and not res and "ما عندنا الآن كتاب" not in msg:
        fails.append("no books and no explanation")
    if fails:
        bad_cases.append(label)
    ok(not fails, f"{label}" + (f"  -> {fails[:3]}" if fails else ""))

print(f"\ncases {len(cases)}, failed {len(bad_cases)}")
# explicit constraint that cannot be met -> honest empty answer, no violating fillers
r = c.post("/api/recommend", json={"query": "مسرحية قصيرة للأطفال بدون عنف"}).get_json()
ok(all(b["genre"] in ("مسرحية", "أطفال") and not A.is_long(b, cards.get(b["book_id"])) for b in r["results"]),
   "impossible combination never filled with violating books")
# genres added 2026-10-03: comics, essays, literary criticism, arts and cinema
g = lambda q: A.local_understand([q])["genres"]
ok(g("أبغى مانغا") == ["قصص مصورة"] and g("عندكم كوميكس؟") == ["قصص مصورة"], "مانغا / كوميكس -> قصص مصورة")
ok(g("أبغى رواية مصورة") == ["قصص مصورة"], "رواية مصورة is comics, not رواية")
ok(g("أبغى كتاب مقالات") == ["مقالات ونصوص"], "مقالات -> مقالات ونصوص")
ok(g("كتاب في النقد الأدبي") == ["نقد أدبي"], "النقد الأدبي -> نقد أدبي (not أدب)")
ok(g("كتب عن السينما") == ["فنون وسينما"], "السينما -> فنون وسينما")
ok(g("أبغى رواية") == ["رواية"] and g("قصص قصيرة") == ["قصص قصيرة"], "older genres unchanged")
import llm as L, chat as C, card_review as CR
ok(g("كتاب في علم الاجتماع") == ["علم اجتماع"] and g("علم نفس") == ["علم نفس"], "علم الاجتماع -> علم اجتماع; علم نفس unchanged")
NEW_AR = ["قصص مصورة", "مقالات ونصوص", "نقد أدبي", "فنون وسينما", "علم اجتماع"]
NEW_F = ["comics", "essays", "criticism", "arts", "sociology"]
ok(all(x in L.GENRES and x in K.GENRES and x in A.GENRE_LABELS_EN for x in NEW_AR), "new genres known to the model list, the fallback and the English labels")
ok(all(f in C.FORMATS and f in CR.FORMATS and A.FORMAT_LABELS.get(f) == ar for f, ar in zip(NEW_F, NEW_AR)), "new formats in chat, card review and labels")
print(f"TOTAL PASS={P} FAIL={F}")
