"""English visitor experience (2026-09-27): replies, reasons and chips in English; negation, age and greetings."""
import os, re, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import app as A, keyword_prefs as K, chat
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)
AR = re.compile(r"[؀-ۿ]")
c = A.app.test_client()
def say(text, lang="en"):
    return c.post("/api/chat", json={"messages": [{"role": "user", "content": text}], "language": lang}).get_json()

# greetings: a question back, no books
for text, lang in (("hello", "en"), ("hi there", "en"), ("السلام عليكم", "ar")):
    r = say(text, lang)
    ok(r["books"] == [] and ("?" in r["reply"] or "؟" in r["reply"]) and (not AR.search(r["reply"]) if lang == "en" else AR.search(r["reply"])), f"greeting «{text}» answered with a question")

# every English reply, reason and chip is English (book data such as author names excepted)
for q in ("I want a funny novel", "something light and short", "a book for my 8 year old daughter", "a novel but not sad",
          "I want a romance but nothing heavy", "something to read with coffee", "self help book", "poetry please",
          "a mystery novel for a teenager", "history books"):
    r = say(q)
    reasons = [b["reason"] for b in r["books"]]
    ok(r["books"] and not AR.search(r["reply"]) and not any(AR.search(x) for x in reasons) and not any(AR.search(x) for x in r["suggestions"]),
       f"English only: «{q}» -> {reasons[:1]}")

# the English reason builder covers every label the matcher can produce
labels = [f"أجواؤه {v} كما طلبت" for v in A.MOOD_LABELS_FEM.values()] + [f"إيقاعه {v}" for v in A.PACE_SHORT.values()] \
    + [f"قراءة {v} عاطفيًا" for k, v in A.WEIGHT_LABELS_FEM.items() if k != "unknown"] + [f"مناسب لـ{v}" for v in A.USE_LABELS.values()] \
    + ["مناسب " + v for v in A.AGE_LABELS.values()] + [v for k, v in A.LEVEL_LABELS.items() if k != "unknown"] \
    + [f"من تصنيف «{g}» كما طلبت" for g in A.GENRE_LABELS_EN] + ["قصير نسبيًا، حوالي ١٢٠ صفحة", "عمل طويل، حوالي ٥٠٠ صفحة", "قصير",
       "بدون أجواء حزينة، مظلمة", "مو ثقيلة", "بدون عنف، رعب", "رواية، شعر", "عنوانه يطابق كلمات من وصفك"]
left = [(l, A.localize_reason(l)) for l in labels if AR.search(A.localize_reason(l))]
ok(not left, f"all {len(labels)} reason labels have English wording" + (f" (left: {left[:3]})" if left else ""))
ok(A.localize_reason("قصير نسبيًا، حوالي ١٢٠ صفحة") == "short, about 120 pages", "English digits in English text")

# negation in English, Arabic unchanged
def neg(q):
    u = K.understand([q], lambda t: None)
    return u["moods_include"], u["moods_exclude"], u["weight_exclude"], u["genres"]
ok(neg("I don't want anything sad")[1] == ["sad"] and neg("I don't want anything sad")[0] == [], "«I don't want anything sad» excludes sad")
ok(neg("nothing too heavy please")[2] == ["heavy"], "«nothing too heavy» excludes heavy")
ok(neg("a funny novel, not sad")[:2] == (["funny"], ["sad"]), "wanted and unwanted mood in one sentence")
ok(neg("ما ابغى رواية، ابغى شعر")[3] == ["شعر"], "Arabic: second wish after a negated one is kept")
ok("تطوير ذاتي" in neg("a self help book")[3], "«self help» maps to the real category name")
ok(set(K.GENRES) <= {g for g in A.GENRE_LABELS_EN}, "every keyword category is a real catalog category")

# age in English
ok(A.detect_audience(K.normalize("a book for my 8 year old")) == "children", "8 year old -> children")
ok(A.detect_audience(K.normalize("my 15-year-old son")) == "young_adult", "15-year-old -> young adult")
ok(A.detect_audience(K.normalize("a gift for my daughter")) is None, "«my daughter» alone is not assumed to be a child")
ok(A.detect_audience(K.normalize("something for a teenager")) == "young_adult", "teenager -> young adult")

# model instructions for English visitors
ok("Arabic script" in chat.INTERPRET_SYSTEM and "نجيب محفوظ" in chat.INTERPRET_SYSTEM, "model writes English author/title mentions in Arabic script to match the catalog")
ok("Never expose internal terms" in chat.RESPOND_SYSTEM_EN and "shelf is where the book is" in chat.RESPOND_SYSTEM_EN, "English reply rules (no internal terms, no guessed location)")
r = say("hello")
ok(r["suggestions"] and not any(AR.search(x) for x in r["suggestions"]), "English chips")
print(f"TOTAL PASS={P} FAIL={F}")
