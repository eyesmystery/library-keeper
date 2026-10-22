"""[AUDFIX] Daily Codex audit 2026-10-16, P2: where a screen adaptation was made is read from its recorded country only.
No country = not known (the letters of the title or the list a row came from decide nothing); every Arab League
country is Arab (السودان and البحرين were missing); «فيلم هوليوودي / أمريكي» asks for a US production, not any
foreign one. PROPOSED with the honors.py / app.py patch of research/audfix_2026-10-16/ (not active before it).
"""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["LIBRARY_KEEPER_LLM"] = "off"
import models; models.init_db()
import llm, honors, app as A
from models import get_connection
P = F = 0
def ok(cond, msg):
    global P, F
    print(("PASS " if cond else "FAIL ") + msg); P += bool(cond); F += (not cond)

# ---------------------------------------------------------------- one row at a time
def row(country=None, title="Test Film", source="test"):
    return {"country": country, "title": title, "source": source, "kind": "film"}
for title, src in (("Test Film", "test"), ("فيلم تجربة", "test"), ("Test Film", "wikidata"), ("فيلم تجربة", "wikipedia_ar")):
    r = row(None, title, src)
    ok(not any(honors._adapt_origin_ok(r, o) for o in ("arab", "foreign", "american")) and honors.adaptation_origin(r) is None,
       f"no country ({title}, {src}): not known, meets no origin wish")
for c in ("السودان", "البحرين", "مصر", "المملكة العربية السعودية", "الإمارات العربية المتحدة", "سلطنة عُمان", "Sudan", "Bahrain",
          "Egypt", "مصر، فرنسا"):
    r = row(c)
    ok(honors._adapt_origin_ok(r, "arab") and not honors._adapt_origin_ok(r, "foreign") and not honors._adapt_origin_ok(r, "american"),
       f"«{c}»: Arab")
for c in ("الولايات المتحدة", "United States", "USA"):
    r = row(c)
    ok(honors._adapt_origin_ok(r, "american") and honors._adapt_origin_ok(r, "foreign") and not honors._adapt_origin_ok(r, "arab"),
       f"«{c}»: American, so foreign too")
for c in ("فرنسا", "المملكة المتحدة", "Romania", "اليابان"):
    r = row(c)
    ok(honors._adapt_origin_ok(r, "foreign") and not honors._adapt_origin_ok(r, "american") and not honors._adapt_origin_ok(r, "arab"),
       f"«{c}»: foreign, not Hollywood")
ok(all(honors._adapt_origin_ok(row(None), None) for _ in (1,)), "no origin wish: every adaptation still counts")

# ---------------------------------------------------------------- through matches_adaptation
avail = [b for b in (dict(r) for r in A.list_books(only_active=False)) if A.is_available(b)]
b1, b2, b3 = (avail[i]["book_id"] for i in range(3))
con = get_connection()
for bid, title, country in ((b1, "A Test Film", "فرنسا"), (b2, "هيبتا تجربة", None), (b3, "Latin Title Test", None)):
    con.execute("INSERT INTO book_adaptations (book_id, kind, title, year, country, source, created_by, created_at) "
                "VALUES (?, 'film', ?, 2016, ?, 'adaptations_list', 't', 'x')", (bid, title, country))
con.commit(); con.close(); honors.clear_cache()
ok(honors.matches_adaptation(b1, "film", None, "foreign") and not honors.matches_adaptation(b1, "film", None, "american"),
   "a French film is foreign, not a Hollywood film")
ok(not honors.matches_adaptation(b2, "film", None, "foreign") and not honors.matches_adaptation(b3, "film", None, "arab"),
   "no country: neither the world list nor the title letters decide")
ok(honors.matches_adaptation(b2, "film") and honors.matches_adaptation(b3, "film"), "without an origin wish both still count as films")

# ---------------------------------------------------------------- the visitor's words
def origin(text):
    p, _, _, _ = A.prefs_from_ai(llm.clean_prefs({"adaptation": "film"}), A.normalize(text))
    return p.get("adaptation_origin")
ok(origin("رواية صارت فيلم هوليوودي") == "american" and origin("ابي رواية تحولت فيلم أمريكي") == "american", "Hollywood / American")
ok(origin("رواية صارت فيلم أجنبي") == "foreign", "foreign")
ok(origin("رواية صارت فيلم مصري") == "arab" and origin("رواية صارت فيلم عربي") == "arab", "Arab / Egyptian")
ok(origin("رواية صارت فيلم") is None, "no origin said")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
