import os, sys, json
sys.path.insert(0, os.getcwd())
import models, llm, chat, card_review, app as A
from auth_helper import login
P = F = 0
def ok(c, name):
    global P, F
    if c: P += 1; print("PASS", name)
    else: F += 1; print("FAIL", name)
llm.get_active = lambda: None
c = login(A.app.test_client())
cards = models.get_profile_cards()
def suitable(bid, aud):
    return A.age_allowed(cards.get(bid), aud)
def med(bid, attr, v):
    return A._medium_or_better(cards.get(bid), attr, v)

n = A.normalize
ok(A.detect_audience(n("هدية لبنتي عمرها 8 سنوات")) == "children", "age 8 -> children")
ok(A.detect_audience(n("ولدي عمره ١٥")) == "young_adult", "Arabic digits 15 -> teen")
ok(A.detect_audience(n("ابغى رواية مشوقة")) is None, "no audience in adult request")
ok(A.detect_audience(n("كتاب لطفلي")) == "children", "لطفلي -> children")

# Age rule since 2026-10-04 (Ibrahim): no age categories; the children's / young-adult shelf (card format)
# decides who a book is offered to, and sexual content is the only age statement («لا يناسب الأطفال»).
kids_ok = [b for b in cards if cards[b].get("format") == "children" and suitable(b, "children")]
ok(len(kids_ok) >= 30, f"children's books in catalog ({len(kids_ok)})")
ok(all(cards[b].get("format") == "children" for b in cards if suitable(b, "children")), "a child is offered only the children's shelf")
ok(all(cards[b].get("format") in ("young_adult", "children") for b in cards if suitable(b, "young_adult")), "a teenager is offered only the young-adult and children's shelves")
ok(not any("sexual_content" in (cards[b].get("sensitive_content") or []) and med(b, "sensitive_content", "sexual_content")
           for b in cards if suitable(b, "children") or suitable(b, "young_adult")), "no book with sexual content is offered to a child or teenager")

for q in ["ابغى قصة لبنتي عمرها 7 سنوات", "قصص اطفال مضحكة", "هدية لطفلي"]:
    r = c.post("/api/recommend", json={"query": q}).get_json()["results"]
    ok(r and all(suitable(b["book_id"], "children") for b in r), f"recommend children only: {q} -> {[b['title'] for b in r]}")
for q in ["ولدي مراهق عمره 14 يحب المغامرة", "رواية لليافعين فيها غموض"]:
    r = c.post("/api/recommend", json={"query": q}).get_json()["results"]
    ok(r and all(suitable(b["book_id"], "young_adult") for b in r),
       f"recommend teen-safe: {q} -> {[b['title'] for b in r]}")
for q in ["رواية حزينة", "ابغى كتاب خفيف مع القهوة"]:
    r = c.post("/api/recommend", json={"query": q}).get_json()["results"]
    ok(all(cards[b["book_id"]].get("format") != "children" for b in r),
       f"adult request shows no children's book: {q}")

# chat keyword fallback (no model)
r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "ابغى كتاب لولدي الصغير عمره 6"}]}).get_json()
ok(r["books"] and all(suitable(b["book_id"], "children") for b in r["books"]), f"chat fallback children-safe {[b['title'] for b in r['books']]}")

# chat AI path with a fake model that tries to show an adult book to a child
adult = next(b for b in cards if cards[b].get("format") == "novel" and med(b, "sensitive_content", "sexual_content"))
kid = kids_ok[0]
base = llm.clean_prefs({"audience": "children"})
chat.interpret = lambda conv, active: {"intent": "recommend", "prefs": base, "mentioned_titles": [],
                                      "wants_new_options": False}
seen_facts = {}
def fake_respond(conv, facts_json, guidance, active):
    seen_facts["facts"] = json.loads(facts_json); seen_facts["guidance"] = guidance
    return {"reply": "x", "show_book_ids": [adult, kid], "suggestions": []}
chat.respond = fake_respond
out = A.run_chat([{"role": "user", "content": "هدية لطفل"}], active=("fake", "m"))
ids = [b["book_id"] for b in out["books"]]
ok(adult not in ids, "adult book id from model is dropped for a child")
ok(all(suitable(f["id"], "children") for f in seen_facts["facts"]), "all chat facts age-suitable for a child")
ok("طفل" in seen_facts["guidance"], "guidance tells the model the reader is a child")
labels = [l for f in seen_facts["facts"] for l in f["confirmed"] + f["impression"]]
ok(not any(l.startswith("الفئة العمرية") for l in labels), "no age-category label is given to the model any more")
books_by_id = {b["book_id"]: dict(b) for b in A.list_books(only_active=False)}
fact = A.book_fact(books_by_id[adult], cards[adult], "test")
ok("لا يناسب الأطفال" in fact["confirmed"] + fact["impression"], "a book with sexual content says «لا يناسب الأطفال»")
fact = A.book_fact(books_by_id[kid], cards[kid], "test")
ok("لا يناسب الأطفال" not in fact["confirmed"] + fact["impression"], "a children's book carries no such statement")

# avoid_content
flagged = next((b for b in cards if med(b, "sensitive_content", "violence")), None)
ok(flagged and not A.age_allowed(cards[flagged], None, {"violence"}), "avoid_content excludes a flagged book")
ok(llm.clean_prefs({"audience": "teen", "avoid_content": ["violence", "x"]}) ["audience"] is None, "clean_prefs rejects unknown audience")
ok(llm.clean_prefs({"avoid_content": ["violence", "x"]})["avoid_content"] == ["violence"], "clean_prefs filters avoid_content")

# admin + review
d = c.get(f"/api/admin/books/{kid}").get_json()
keys = [a["key"] for a in d["attributes"]]
ok("age_suitability" not in keys and "sensitive_content" in keys, "admin card shows content warnings and no age category")
card_review.apply_action(adult, "age_suitability", "set", value="all_ages", reviewer="اختبار")
conn = models.get_connection()
row = conn.execute("SELECT age_suitability, sensitive_content FROM book_profiles WHERE book_id=?", (adult,)).fetchone()
conn.close()
ok(row["age_suitability"] == "all_ages", "staff set age updates the card")
ok(isinstance(json.loads(row["sensitive_content"] or "[]"), list), "sensitive_content stays a JSON list")
r = c.get("/api/admin/catalog").get_json()
ok(len(r) == len(json.load(open("../data/seed_books.json"))), f"catalog size {len(r)}")
# --- severity rules (card v2 §12.4, 2026-09-28)
def mk(age, flags):
    conf = {("sensitive_content", f): (0.8, False) for f in flags}
    return {"confidence": conf, "severity": dict(flags), "sensitive_content": list(flags),
            "format": age if age in ("children", "young_adult") else "novel"}
ok(A.age_allowed(mk("young_adult", {"strong_language": 1}), "young_adult"), "teen: mild strong-language warning does not block")
ok(not A.age_allowed(mk("young_adult", {"strong_language": 2}), "young_adult"), "teen: clear strong-language warning blocks")
ok(not A.age_allowed(mk("young_adult", {"sexual_content": None}), "young_adult"), "teen: a blocking flag with unknown severity still blocks (flag decides, as before)")
ok(A.age_allowed(mk("young_adult", {"violence": None}), "young_adult"), "teen: violence with unknown severity is not assumed strong")
ok(A.age_allowed(mk("children", {"grief": None}), "children"), "child: grief with unknown severity is not assumed strong")
ok(A.age_allowed(mk("young_adult", {"violence": 2, "grief": 2}), "young_adult"), "teen: clear violence/grief allowed")
ok(not A.age_allowed(mk("young_adult", {"violence": 3}), "young_adult"), "teen: strong violence blocks")
ok(not A.age_allowed(mk("children", {"violence": 1}), "children"), "child: any violence warning blocks")
ok(A.age_allowed(mk("children", {"grief": 2}), "children") and not A.age_allowed(mk("children", {"grief": 3}), "children"), "child: grief blocks only when strong")
ok(A.age_allowed(mk("adult", {"sexual_content": 3}), None), "adult reader: warnings do not block unless asked to avoid")
ok(not A.age_allowed(mk("adult", {}), "children") and not A.age_allowed(mk("adult", {}), "young_adult"), "a book off the children's / young-adult shelves is not offered to a child or teenager")
ok(A.age_allowed(mk("children", {}), "young_adult") and not A.age_allowed(mk("young_adult", {}), "children"), "a teenager may get a children's book; a child gets no young-adult book")
ok(not A.age_allowed(mk("children", {"sexual_content": 1}), "children"), "child: sexual content blocks at any severity")
print(f"TOTAL PASS={P} FAIL={F}")
