"""Fill-rate report for the card-v2 pilot records (docs/book-profile-schema.md §12.8).

Usage: python3 scripts/pilot_fill_rate.py research/pilot_2026-09-28  > reports/pilot_fill_rate_2026-09-28.md
"""
import json, os, sys, collections
d = sys.argv[1]
recs = [json.load(open(os.path.join(d, f), encoding="utf-8")) for f in sorted(os.listdir(d)) if f.endswith(".json")
        and not f.startswith(("_", "import_report", "covers_"))]
GROUP = {"novel": "روايات وقصص", "short_stories": "روايات وقصص", "young_adult": "روايات وقصص",
         "self_dev": "معرفي", "thought": "معرفي", "business": "معرفي", "religious": "معرفي", "history": "معرفي",
         "biography": "سيرة ورحلات", "travel": "سيرة ورحلات", "children": "أطفال"}
def grp(r):
    if r.get("product_type"): return "منتجات أطفال غير قرائية"
    if r["match"]["status"] != "matched" and not r.get("format"): return "لم يُحدد الكتاب"
    return GROUP.get(r.get("format"), "أخرى")
FIELDS = [("author","المؤلف"),("original_language","لغة العمل الأصلية"),("translator","المترجم (للمترجم فقط)"),("publication_year","سنة النشر"),
          ("page_count","عدد الصفحات"),("isbn13","ISBN"),("series_name","السلسلة"),("summary_ar","النبذة"),("hook_ar","جملة الجذب"),
          ("cover_image_url","رابط غلاف"),("subjects","الموضوعات"),("themes","الثيمات"),("setting","المكان والزمن")]
ROWS = ["format","pace","moods","emotional_weight","perceived_length","language_difficulty","style_complexity","opening_grip","ending_tone",
        "narrative_structure","use_cases","age_suitability","sensitive_content","series_dependency","practicality","prior_knowledge"]
FICTION = {"opening_grip","ending_tone","narrative_structure"}; KNOW = {"practicality","prior_knowledge"}
status = collections.Counter(r["match"]["status"] for r in recs)
groups = collections.Counter(grp(r) for r in recs)
out = [f"# Card v2 research — fill rate ({os.path.basename(os.path.normpath(d))}, {len(recs)} books)", "",
       f"Records: {len(recs)}. Match: " + ", ".join(f"{k} {v}" for k, v in status.items()) + ".", "",
       "Groups: " + ", ".join(f"{k} {v}" for k, v in groups.items()) + ".", "",
       "## Book-level fields (share of identified books with a value; identified = matched, or a probable match that still needs staff confirmation)", "", "| Field | All identified | روايات وقصص | معرفي | سيرة ورحلات | أطفال |", "|---|---|---|---|---|---|"]
matched = [r for r in recs if r["match"]["status"] == "matched" or r.get("format")]
matched = [r for r in matched if not r.get("product_type")]
def pct(sub, f):
    if not sub: return "—"
    n = sum(1 for r in sub if r.get(f) not in (None, "", []))
    return f"{n}/{len(sub)}"
for f, label in FIELDS:
    sub = matched
    if f == "translator": sub = [r for r in matched if r.get("original_language") and r.get("original_language") != "ar" and r.get("edition_language") == "ar"]
    cols = [pct(sub, f)] + [pct([r for r in sub if grp(r) == g], f) for g in ("روايات وقصص","معرفي","سيرة ورحلات","أطفال")]
    out.append(f"| {label} | " + " | ".join(cols) + " |")
out += ["", "## Card attributes (share of identified books with at least one usable row, confidence ≥ 0.65)", "", "| Attribute | All identified | روايات وقصص | معرفي | سيرة ورحلات | أطفال |", "|---|---|---|---|---|---|"]
def has(r, a): return any(x["name"] == a and x["confidence"] >= 0.65 for x in r.get("rows", []))
def pr(sub, a):
    if not sub: return "—"
    return f"{sum(1 for r in sub if has(r, a))}/{len(sub)}"
for a in ROWS:
    sub = matched
    if a in FICTION: sub = [r for r in matched if grp(r) == "روايات وقصص"]
    if a in KNOW: sub = [r for r in matched if grp(r) == "معرفي"]
    cols = [pr(sub, a)] + [pr([r for r in sub if grp(r) == g], a) for g in ("روايات وقصص","معرفي","سيرة ورحلات","أطفال")]
    out.append(f"| {a} | " + " | ".join(cols) + " |")
hi = sum(1 for r in matched for x in r.get("rows", []) if x["confidence"] >= 0.85)
tot = sum(len(r.get("rows", [])) for r in matched)
src = collections.Counter(x["source_site"] for r in matched for x in r.get("rows", []))
out += ["", f"Rows: {tot} (High ≥0.85: {hi}). Sources: " + ", ".join(f"{k} {v}" for k, v in src.most_common()) + ".", "",
        "## Books needing staff confirmation", ""]
for r in recs:
    if r["match"]["status"] != "matched":
        out.append(f"- {r['book_id']} «{r['rewaa_title']}» — {r['match']['status']}: {r['match'].get('note','')}")
print("\n".join(out))
