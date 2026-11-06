"""Import the Arabic Goodreads pages found by the GRSEARCH full run (Ibrahim 2026-11-05 «استوردها بدون ما ترجع لي»).

Reads every results file of the search (research/gr_search_full_2026-11-04/batches/*.jsonl and, from MGR's own
duplicate run of 11-04, research/gr_search_full/results.jsonl). Only verdicts match / match_other_edition (an Arabic
page of the same work) and only books that still have no goodreads_url; nothing is overwritten. Source per book:
«grsearch <run date> (<verdict>): web search + /book/show check». Dry run without --apply.
  backend/venv/bin/python scripts/import_grsearch.py [--apply]
"""
import glob, json, os, sys
from datetime import datetime, timezone
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import models

APPLY = "--apply" in sys.argv
FILES = sorted(glob.glob(os.path.join(ROOT, "research", "gr_search_full_2026-11-04", "batches", "*.jsonl"))) + \
    [os.path.join(ROOT, "research", "gr_search_full", "results.jsonl")]
seen, todo, skipped = set(), [], 0
for path in FILES:
    if not os.path.exists(path):
        continue
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        url, bid = (d.get("url") or "").strip(), d.get("book_id")
        if d.get("verdict") not in ("match", "match_other_edition") or bid in seen:
            continue
        if not url.startswith("https://www.goodreads.com/book/show/"):
            continue
        seen.add(bid)
        b = models.get_book(bid)
        if not b or (b["goodreads_url"] or "").strip():
            skipped += 1
            continue
        when = str(d.get("run_at") or d.get("checked_at") or "2026-11")[:10]
        todo.append((bid, url.split("?")[0], f"grsearch {when} ({d['verdict']}): web search + /book/show check"))
for bid, url, src in todo:
    if APPLY:
        models.update_book(bid, {"goodreads_url": url, "goodreads_url_source": src})
print(("applied" if APPLY else "dry run"), len(todo), "· already had a link", skipped,
      "· at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
