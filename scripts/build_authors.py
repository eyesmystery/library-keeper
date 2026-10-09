"""Build/refresh the author tables from the books' author/translator text (docs/author-profile-proposal.md).

Usage (from the project folder):
  python3 scripts/build_authors.py <db_path>            # dry run on an in-memory copy; prints the report
  python3 scripts/build_authors.py <db_path> --apply    # writes (take a backup first: scripts/backup_db.py)

The report (<project>/reports/authors_report.json) lists counts per role, authors with several
books, and probable duplicate spellings to resolve in an author research batch. `books` is never
changed. The server also runs the same sync on every start.
"""
import json, os, sqlite3, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))
import models  # noqa: E402
import authors  # noqa: E402

db, apply = sys.argv[1], "--apply" in sys.argv[2:]
if not apply:  # dry run: work on a throwaway copy
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(), "copy.db")
    src, dst = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(tmp)
    src.backup(dst); src.close(); dst.close()
    db = tmp
models.DB_PATH = db
models.init_db()
result = authors.sync()
conn = models.get_connection()
roles = dict(conn.execute("SELECT role, COUNT(*) FROM book_authors GROUP BY role").fetchall())
multi = conn.execute("""SELECT a.name_ar, COUNT(*) n FROM book_authors ba JOIN authors a USING(author_id)
                        WHERE ba.role='author' GROUP BY author_id HAVING n > 1 ORDER BY n DESC""").fetchall()
styled = conn.execute("SELECT COUNT(*) FROM authors WHERE catalog_style IS NOT NULL").fetchone()[0]
dups = authors.probable_duplicates()
report = dict(result, applied=apply, links_by_role=roles, authors_with_catalog_style=styled,
              authors_with_several_books=len(multi), top=[(r[0], r[1]) for r in multi[:15]],
              probable_duplicates=[{"a": [a, na], "b": [b, nb]} for a, na, b, nb in dups])
os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
with open(os.path.join(ROOT, "reports", "authors_report.json"), "w", encoding="utf-8") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=1)
print(json.dumps({k: v for k, v in report.items() if k not in ("top", "probable_duplicates")}, ensure_ascii=False))
print("top:", report["top"][:8])
print("probable duplicates:", len(dups))
for d in report["probable_duplicates"][:40]:
    print("  ", d["a"][1], "|", d["b"][1])
