"""Import the library's own book sheet (Thalath's Excel: shelf code, publisher, category, keywords)
into the catalog — track SHELF, Ibrahim 2026-10-11. The same import is in the admin page
(«الاستيراد» → «ملف المكتبة: الرفوف والتصنيف»); this is the command-line form, for Claude on the Mac.

  backend/venv/bin/python scripts/library_sheet.py <sheet.xlsx>                 dry run, writes the plan
  backend/venv/bin/python scripts/library_sheet.py <sheet.xlsx> --apply         backup + write
  options: --db PATH (another database), --plan OUT.json (where the full plan goes)

What is written (see backend/library_sheet.py): the shelf location with its place («روايات · الرف R04»),
an empty publisher, an empty genre (from the library's category), the keywords. A publisher that
differs from ours is only listed. Descriptions are not written by this script.
"""
import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import models  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "library.db"))
    ap.add_argument("--plan", default=None)
    a = ap.parse_args()
    models.DB_PATH = os.path.abspath(a.db)
    models.init_db()                     # adds books.keywords on a database that does not have it yet
    import library_sheet as L
    headers, rows = L.read_sheet(a.sheet, open(a.sheet, "rb").read())
    plan = L.build_plan(os.path.basename(a.sheet), headers, rows)
    out = a.plan or os.path.join(os.path.dirname(os.path.abspath(a.sheet)), "plan_%s.json" % time.strftime("%Y%m%d_%H%M"))
    json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("columns:", plan["columns"])
    print(f"rows {plan['rows_read']} · books matched {plan['matched_books']} (sure {plan['sure']}, probable {len(plan['probable'])})"
          f" · unmatched rows {len(plan['unmatched'])} · not a shelf code {len(plan['numeric_codes'])}")
    print("changes:", plan["counts"], "· books changed", len(plan["updated"]),
          "· publisher differs", len(plan["publisher_differs"]), "· category not understood", len(plan["genre_unclear"]))
    print("plan:", out)
    if not a.apply:
        print("dry run: nothing written (add --apply)")
        return
    backups = glob.glob(os.path.join(os.path.dirname(models.DB_PATH), "..", "backups", "*.db"))
    if backups:
        newest = max(backups, key=os.path.getmtime)
        if time.time() - os.path.getmtime(newest) < 120:
            sys.exit(f"another backup is less than 2 minutes old ({os.path.basename(newest)}): board rule 4, wait and re-run")
    res = L.apply_plan(plan, applied_by="Claude (scripts/library_sheet.py)")
    print("applied:", res)
    import sqlite3
    print("integrity:", sqlite3.connect(models.DB_PATH).execute("PRAGMA integrity_check").fetchone()[0])


if __name__ == "__main__":
    main()
