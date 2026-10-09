"""Apply author duplicate decisions (docs/author-profile-proposal.md).

  python3 scripts/merge_authors.py <db_path> <decisions.json> [--apply]

decisions.json: {"merge": [{"keep": "<canonical name>", "drop": ["<variant>", ...]}]}
Only for one person whose name is written differently (Ibrahim, 2026-09-30). Each merge first
replaces the variant spelling in the linked books' displayed author/translator text with the
canonical name (Ibrahim's choice, 2026-09-30; audited per book), then calls
authors.merge_authors() (variants become aliases, links move, style fields merge).
Without --apply: dry run on a copy.
"""
import json, os, sqlite3, sys, tempfile
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))
import models  # noqa: E402
import authors  # noqa: E402

db, decisions, apply = sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8")), "--apply" in sys.argv[3:]
if not apply:
    tmp = os.path.join(tempfile.mkdtemp(), "copy.db")
    src, dst = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(tmp)
    src.backup(dst); src.close(); dst.close()
    db = tmp
models.DB_PATH = db
models.init_db()
authors.sync()
conn = models.get_connection()
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
report = {"applied": apply, "merged": [], "problems": []}
try:
    for m in decisions.get("merge", []):
        for variant in m["drop"]:
            keep, drop = authors.find(conn, m["keep"]), authors.find(conn, variant)
            if keep is None or drop is None or drop == keep:
                report["problems"].append(f"not found or already merged: {m['keep']} / {variant}")
                continue
            # "unify": false keeps the books' own text (e.g. an English edition keeps its Latin author name)
            changed = authors.unify_display(conn, keep, drop) if m.get("unify", True) else []
            for c in changed:
                conn.execute("""INSERT INTO audit_log (at, user_id, username, action, target, details)
                                VALUES (?, NULL, 'claude', 'author.unify_spelling', ?, ?)""",
                             (now, str(c["book_id"]), json.dumps(c, ensure_ascii=False)))
            conn.commit()
            result = authors.merge_authors(keep, drop)
            report["merged"].append(dict(result, books_changed=changed))
finally:
    conn.close()
report["after"] = authors.sync()
report["remaining_duplicates"] = [[x[1], x[3]] for x in authors.probable_duplicates()]
print(json.dumps(report, ensure_ascii=False, indent=1))
