"""Import summaries (نبذة) and hooks written by a researcher for books ALREADY in the catalog (2026-10-03).

Usage (work on a COPY first, then back up and run on the live database):
  python3 scripts/import_summaries.py <db_path> <batch_dir> [--apply]
<batch_dir>/summaries.json: [{book_id, summary_ar, hook_ar, summary_source_url}].
Only books.summary, summary_source, summary_updated_at and hook are written, and only for a `matched` book whose
summary is still empty (an existing summary or hook is never overwritten). Nothing else in the database changes.
Refused: a summary over 80 words / 700 characters, with a double quote, or without a source URL
(the literal source `general_knowledge` is accepted since 2026-10-04: well-known works written from the model's knowledge, Ibrahim's decision).
Without --apply nothing is written. The report is saved as <batch_dir>/import_report.json.
"""
import json, os, re, sqlite3, sys
from datetime import datetime, timezone

DB, HERE = sys.argv[1], sys.argv[2]
APPLY = "--apply" in sys.argv
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")
text = lambda s, n: re.sub(r"\s+", " ", str(s or "")).strip()[:n]

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
recs = json.load(open(os.path.join(HERE, "summaries.json"), encoding="utf-8"))
report = {"imported": [], "hook_only_kept": [], "refused": []}
for d in recs:
    bid = d.get("book_id")
    b = con.execute("SELECT book_id, research_status, summary, hook FROM books WHERE book_id=?", (bid,)).fetchone()
    s, h, u = text(d.get("summary_ar"), 700), text(d.get("hook_ar"), 240), text(d.get("summary_source_url"), 300)
    why = None
    if not b: why = "no such book"
    elif b["research_status"] != "matched": why = "not matched"
    elif (b["summary"] or "").strip(): why = "already has a summary"
    elif not s or len(s.split()) > 80 or '"' in s: why = "summary empty, too long or quoted"
    elif not (u.startswith("http") or u == "general_knowledge"): why = "no source url"
    if why:
        report["refused"].append({"book_id": bid, "why": why}); continue
    keep_hook = bool((b["hook"] or "").strip()) or not h
    if APPLY:
        if keep_hook:
            con.execute("UPDATE books SET summary=?, summary_source=?, summary_updated_at=? WHERE book_id=?", (s, u, NOW, bid))
        else:
            con.execute("UPDATE books SET summary=?, summary_source=?, summary_updated_at=?, hook=? WHERE book_id=?",
                        (s, u, NOW, h, bid))
    report["imported"].append(bid)
    if keep_hook: report["hook_only_kept"].append(bid)
if APPLY:
    con.commit()
report["summary"] = {"imported": len(report["imported"]), "refused": len(report["refused"]), "applied": APPLY,
                     "integrity": con.execute("PRAGMA integrity_check").fetchone()[0]}
json.dump(report, open(os.path.join(HERE, "import_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(report["summary"], ensure_ascii=False)); print("refused:", report["refused"][:20])
