import os, sys, sqlite3
sys.path.insert(0, os.getcwd())
import models, app as A
from auth_helper import login
c = login(A.app.test_client()); P=F=0
def ok(cond,msg):
    global P,F; print(("PASS " if cond else "FAIL ")+msg); P+=bool(cond); F+=(not cond)
con = sqlite3.connect(models.DB_PATH)
cat = c.get("/api/admin/catalog").get_json()
import json
N = len(json.load(open("../data/seed_books.json")))
ok(len(cat) == N, f"catalog lists {N} books")
ok(sum(1 for b in cat if not b["has_reviews"]) <= 2, "at most 2 books flagged without reviews")
conf_books = {r[0] for r in con.execute("select distinct book_id from attribute_evidence where source_site in ('goodreads','abjjad','web') and confidence>=0.85 and conflict_seen=0")}
ok({b["book_id"] for b in cat if b["counts"]["confirmed"] > 0} == conf_books, "confirmed counts match DB")
ok(sum(b["counts"]["conflict"] for b in cat) == con.execute("select count(*) from attribute_evidence where conflict_seen=1 and human_verified=0 and source_site!='derived'").fetchone()[0], "conflict counts match DB")
total_ev = con.execute("select count(*) from attribute_evidence").fetchone()[0]
ok(sum(sum(b["counts"].values()) for b in cat) == total_ev, "every evidence row counted once")
d = c.get("/api/admin/books/3").get_json()
ok([a["key"] for a in d["attributes"]][:2] == ["format", "genres"] and len(d["attributes"]) == 17, "card has 17 attributes in order (10 + 7 card-v2; the age category is hidden since 2026-10-04)")
ok(all(e["label"] and e["tier"] in ("confirmed","impression","weak","conflict","derived") for a in d["attributes"] for e in a["evidence"]), "evidence labelled and tiered")
ok(sum(len(a["evidence"]) for a in d["attributes"]) == con.execute("select count(*) from attribute_evidence where book_id=3 and attribute_name != 'age_suitability'").fetchone()[0], "all evidence rows of book 3 shown (age rows are hidden)")
ok(all(u.startswith("https://") for u in d["sources"]) and d["sources"], "source links present and http(s) only")
ok(not any(e["label"] in ("moderate","difficult") for a in d["attributes"] for e in a["evidence"]), "Arabic labels, no raw enum leaks")
ok(c.get("/api/admin/books/9999").status_code == 404, "unknown book 404")
html = open("../admin/index.html", encoding="utf-8").read()
settings_tabs = ["settingsTrial", "settingsSections", "settingsUsers", "settingsAi", "settingsAudit", "settingsAbout"]
ok('role="tablist" aria-label="أقسام الإعدادات"' in html and all(f'aria-controls="{x}"' in html for x in settings_tabs),
   "settings are split into six labelled tabs")
ok(all(f'id="{x}" class="settings-pane" role="tabpanel"' in html for x in settings_tabs),
   "every settings tab controls its own panel")
ok("if (which === 'settings') selectSettingsTab(currentSettingsTab);" in html,
   "opening settings loads only the selected settings panel")
ok('id="refreshModels"' in html and 'id="modelsState"' in html and '/api/admin/llm/models?provider=' in html,
   "AI settings can refresh the selected provider's model list")
ok("القائمة الحالية والاختيار اليدوي ما زالوا متاحين" in html,
   "model-list failure keeps the fallback list and manual entry")
ok("sel.size = 1;" in html and "? 8 : 1" not in html,
   "the model picker is a closed dropdown for every provider, Ollama included")
ok("if (pid === 'codex')" not in html and "loadProviderModels(false)" in html,
   "Codex uses the same refresh flow as the other providers")
import re
version_file = open("../VERSION", encoding="utf-8").read().strip()
ok(re.fullmatch(r"\d+\.\d+(\.\d+)? beta", version_file) is not None, "VERSION file holds one version line like '0.8 beta' or '0.8.1 beta'")
ok(c.get("/api/admin/version").get_json() == {"version": version_file}, "the server reports the version from the VERSION file")
ok(A.app.test_client().get("/api/admin/version").status_code == 401, "the version route needs a login")
ok('<b id="aboutVersion">—</b>' in html and "if (which === 'about') loadVersion();" in html and "/api/admin/version" in html,
   "about page reads its version from the server and shows a dash until it arrives")
ok("١٫٠ ·" not in html and "سبتمبر ٢٠٢٦" not in html, "no hand-written version or date is left in the about page")
ok(all(token in html for token in ['class="about-hero"', 'class="about-journey" role="list"',
                                    'class="about-facts"', 'class="about-footer"']),
   "about section has a clear identity, journey, trust facts, and footer structure")
ok(all(text in html for text in ["يسولف مع ثلاث الذكي", "نبحث في الموجود", "يختار بثقة",
                                 "خصوصية من البداية", "يستمر وقت التعطل"]),
   "about copy explains the visitor journey, privacy, and fallback in plain Arabic")
ok('about-marsa' not in html and 'marsa' not in html.lower() and "مرسى" not in html and '.about-thalath{width:92px;' in html and
   '<h2 id="aboutTitle">ثلاث الذكي</h2>' in html and 'class="about-kicker"' not in html,
   "about hero shows the Thalath mark alone and names the product «ثلاث الذكي»")
ok(".about-journey{grid-template-columns:1fr}" in html and ".about-facts{grid-template-columns:1fr}" in html,
   "about section stacks its journey and facts on narrow screens")
ids = __import__('re').findall(r'\bid="([^"]+)"', html)
ok(len(ids) == len(set(ids)), "admin page has no duplicate element ids")
print(f"TOTAL PASS={P} FAIL={F}")
