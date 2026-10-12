"""Import awards / screen adaptations rows into book_awards, author_awards, book_adaptations.

  backend/venv/bin/python scripts/import_honors.py <rows.json>            # dry run on a copy
  backend/venv/bin/python scripts/import_honors.py <rows.json> --apply    # backup, then the live database

rows.json: a list of {"table": "book_awards" | "author_awards" | "book_adaptations",
  "book_id" | "author_id", ...fields of backend/honors.validate(), "source": "wikidata" | "ipaf_list" | ...}
Every row must carry a source_url. Exact duplicates are skipped. Report: <rows>.report.json.

  --replace ipaf:2020,booker,...   first remove the older research rows (source wikidata / wikipedia_ar, or the
                                   list given with --replace-sources) of those prizes; "key:YEAR" = from that year on.
  --replace-other <names.txt>      also remove those rows stored under «other» whose prize name is one line of the file
  --replace-results winner         limit both removals to these results (default: every result)
  --replace-from 1977              limit both removals to rows of that year or later (rows without a year are kept)
"""
import json, os, shutil, sqlite3, subprocess, sys, tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))


def _arg(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path, apply = sys.argv[1], "--apply" in sys.argv
    rows = json.load(open(path, encoding="utf-8"))
    import models
    live = models.DB_PATH
    if apply:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "backup_db.py"), "--reason", "honors_import"],
                           capture_output=True, text=True)
        print(r.stdout.strip()[-300:])
        if r.returncode != 0:
            sys.exit("backup failed: " + r.stderr[-300:])
        db = live
    else:
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "copy.db")
        shutil.copy(live, db)
    models.DB_PATH = db
    import honors
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    honors.ensure_table(conn)
    books = {r[0] for r in conn.execute("SELECT book_id FROM books")}
    people = {r[0] for r in conn.execute("SELECT author_id FROM authors")}
    report = {"read": len(rows), "added": 0, "duplicate": 0, "refused": [], "removed": {}}
    # --replace ipaf:2020,booker,...  removes earlier research rows of those prizes (optionally from a year on)
    # before adding; only rows whose source is one of --replace-sources (default wikidata,wikipedia_ar).
    spec, other = _arg("--replace"), _arg("--replace-other")
    sources = (_arg("--replace-sources") or "wikidata,wikipedia_ar").split(",")
    results = [r for r in (_arg("--replace-results") or "").split(",") if r]
    only = (f" AND result IN ({','.join('?' for _ in results)})" if results else "", results)
    if _arg("--replace-from"):   # e.g. lists that start in 1977 replace nothing older
        only = (only[0] + " AND year >= ?", only[1] + [int(_arg("--replace-from"))])
    if spec:
        for part in spec.split(","):
            key, _, start = part.partition(":")
            for t in ("book_awards", "author_awards"):
                sql = f"DELETE FROM {t} WHERE award_key=? AND source IN ({','.join('?' for _ in sources)})" + only[0]
                args = [key] + sources + only[1]
                if start:
                    sql += " AND year >= ?"
                    args.append(int(start))
                n = conn.execute(sql, args).rowcount
                report["removed"][f"{t}:{part}"] = n
    if other:   # prizes stored under «other» by name (e.g. Wikidata's «جائزة الكتاب الوطنية») now kept under one name
        for name in [l.strip() for l in open(other, encoding="utf-8") if l.strip() and not l.startswith("#")]:
            for t in ("book_awards", "author_awards"):
                n = conn.execute(f"DELETE FROM {t} WHERE award_key='other' AND award_name=? AND source IN "
                                 f"({','.join('?' for _ in sources)})" + only[0], [name] + sources + only[1]).rowcount
                if n:
                    report["removed"][f"{t}:other:{name}"] = n
    if spec or other:
        honors.clear_cache()
    for i, row in enumerate(rows):
        table = row.get("table")
        owner = row.get("author_id") if table == "author_awards" else row.get("book_id")
        try:
            if table not in ("book_awards", "author_awards", "book_adaptations"):
                raise ValueError("table")
            if owner not in (people if table == "author_awards" else books):
                raise ValueError("unknown book/author id")
            if not str(row.get("source_url") or "").startswith(("http://", "https://")):
                raise ValueError("no source_url")
            rid = honors.add(table, owner, row, row.get("source") or "research", "import_honors", conn=conn)
            report["added" if rid else "duplicate"] += 1
        except ValueError as e:
            report["refused"].append({"index": i, "error": str(e), "row": row})
    if spec and _arg("--replace-from"):
        # a research row without a year is dropped once this import gives the same person / book that prize with its year
        keys = [p.partition(":")[0] for p in spec.split(",")]
        for row in rows:
            t = row.get("table")
            if t in ("book_awards", "author_awards") and row.get("award_key") in keys and row.get("year"):
                owner = "author_id" if t == "author_awards" else "book_id"
                n = conn.execute(f"DELETE FROM {t} WHERE {owner}=? AND award_key=? AND year IS NULL AND source IN "
                                 f"({','.join('?' for _ in sources)})", [row.get(owner), row["award_key"]] + sources).rowcount
                if n:
                    report["removed"][f"{t}:{row['award_key']}:no_year:{row.get(owner)}"] = n
        honors.clear_cache()
    conn.commit()
    ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    report["integrity"] = ok
    for t in ("book_awards", "author_awards", "book_adaptations"):
        report[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    conn.close()
    report["applied"] = apply
    json.dump(report, open(path.replace(".json", "") + ".report.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in report.items() if k != "refused"}, ensure_ascii=False), "refused:", len(report["refused"]))


if __name__ == "__main__":
    main()
