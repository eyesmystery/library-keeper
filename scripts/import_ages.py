"""Import researched target ages of children's / young readers' books (2026-10-09).

Input: a JSON list of {"book_id", "age_min", "age_max", "source", "note"} written by the
age-research conversation (AGE) in research/ages_2026-10-09/. A row needs at least one of the
two ages and a source (URL, or «publisher» / «cover» with a note). Staff-set ages are never
overwritten. Back up first: backend/venv/bin/python scripts/backup_db.py --reason ages

  backend/venv/bin/python scripts/import_ages.py research/ages_2026-10-09/batch_01.json [--apply]
"""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from models import get_connection, init_db


def check(row, conn):
    try:
        bid = int(row["book_id"])
    except (KeyError, TypeError, ValueError):
        return None, "no book_id"
    b = conn.execute("SELECT book_id, title, age_source FROM books WHERE book_id = ?", (bid,)).fetchone()
    if b is None:
        return None, f"{bid}: no such book"
    if b["age_source"] == "staff":
        return None, f"{bid}: set by staff, kept"
    lo, hi = row.get("age_min"), row.get("age_max")
    lo = None if lo in ("", None) else int(lo)
    hi = None if hi in ("", None) else int(hi)
    if lo is None and hi is None:
        return None, f"{bid}: no age"
    if (lo is not None and not 0 <= lo <= 18) or (hi is not None and not 0 <= hi <= 18) or (lo is not None and hi is not None and lo > hi):
        return None, f"{bid}: bad range {lo}-{hi}"
    src = str(row.get("source") or "").strip()[:300]
    if not src:
        return None, f"{bid}: no source"
    return (lo, hi, src, str(row.get("note") or "").strip()[:300] or None, bid), None


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    rows = json.load(open(sys.argv[1], encoding="utf-8"))
    apply = "--apply" in sys.argv
    init_db()
    conn = get_connection()
    good, bad = [], []
    for r in rows:
        v, err = check(r, conn)
        (good.append(v) if v else bad.append(err))
    if apply:
        conn.executemany("UPDATE books SET age_min = ?, age_max = ?, age_source = ?, age_note = ? WHERE book_id = ?", good)
        conn.commit()
    conn.close()
    print(json.dumps({"rows": len(rows), "ok": len(good), "skipped": bad, "applied": apply}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
