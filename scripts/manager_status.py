#!/usr/bin/env python3
"""Read-only status collector for the project manager conversation (BOARD.md).

Prints one JSON object: catalog counts (read from a temporary COPY of
data/library.db, never the live file), progress of the research folders,
and the newest log.md headings. Writes nothing inside the project.
Usage: python3 scripts/manager_status.py [--log N]
"""
import glob, json, os, re, shutil, sqlite3, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = lambda *p: os.path.join(ROOT, *p)


def mtime(p):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.gmtime(os.path.getmtime(p) + 3 * 3600))
    except OSError:
        return None


def tail(p, n=1):
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4000))
            lines = f.read().decode("utf-8", "replace").strip().splitlines()
        return [l[:200] for l in lines[-n:]]
    except OSError:
        return []


def db():
    tmp = os.path.join(tempfile.gettempdir(), "lk_manager_copy.db")
    shutil.copyfile(R("data", "library.db"), tmp)
    c = sqlite3.connect(tmp)
    q = lambda s: c.execute(s).fetchone()[0]
    M = "research_status='matched'"
    S = M + " and quantity>0 and active=1"
    full = lambda col: "(%s is not null and trim(%s)<>'' and %s<>0)" % (col, col, col)
    out = {
        "db_written": mtime(R("data", "library.db")),
        "integrity": q("pragma integrity_check"),
        "books": q("select count(*) from books"),
        "in_stock": q("select count(*) from books where quantity>0 and active=1"),
        "matched": q("select count(*) from books where " + M),
        "needs_confirmation": q("select count(*) from books where research_status='needs_confirmation'"),
        "summaries": q("select count(*) from books where " + full("summary")),
        "matched_without_summary": q("select count(*) from books where %s and not %s" % (M, full("summary"))),
        "translator_named": q("select count(*) from books where " + full("translator")),
        "covers": q("select count(*) from books where " + full("cover_url")),
        "genre": q("select count(*) from books where " + full("genre")),
        "stock_matched": q("select count(*) from books where " + S),
        "stock_matched_missing_pages": q("select count(*) from books where %s and not %s" % (S, full("page_count"))),
        "stock_matched_missing_year": q("select count(*) from books where %s and not %s" % (S, full("publication_year"))),
        "stock_matched_missing_isbn": q("select count(*) from books where %s and not %s" % (S, full("isbn"))),
        "books_with_read_reviews": q("select count(distinct book_id) from attribute_evidence where review_count>0"),
        "authors": q("select count(*) from authors"),
        "authors_pending": q("select count(*) from authors where research_status='pending'") if
            "research_status" in [r[1] for r in c.execute("pragma table_info(authors)")] else None,
        "open_visitor_notes": q("select count(*) from visitor_notes where status='open'"),
    }
    c.close()
    os.remove(tmp)
    return out


def research():
    s = R("research", "summaries")
    batches = sorted(glob.glob(os.path.join(s, "batch_*")))
    imported = [b for b in batches if os.path.exists(os.path.join(b, "import_report.json"))]
    gr = R("research", "reviews_gr")
    tr = R("research", "translators_2026-10-03")
    md = R("research", "metadata_2026-10-03")
    try:
        nwf_total = sum(1 for l in open(os.path.join(tr, "ids_nwf.txt")) for x in re.split(r"[\s,]+", l) if x)
    except OSError:
        nwf_total = None
    try:
        nwf_done = len(json.load(open(os.path.join(tr, "nwf.json"))))
    except Exception:
        nwf_done = None
    return {
        "summaries": {"batches": len(batches), "with_import_report": len(imported),
                      "newest_batch": os.path.basename(batches[-1]) if batches else None,
                      "newest_batch_changed": mtime(batches[-1]) if batches else None,
                      "desc_files": len(os.listdir(os.path.join(s, "desc"))) if os.path.isdir(os.path.join(s, "desc")) else None,
                      "fetch_log_changed": mtime(os.path.join(s, "_fetch.log"))},
        "goodreads": {"review_files": len(glob.glob(os.path.join(gr, "[0-9]*.json"))),
                      "pages_log_changed": mtime(os.path.join(gr, "_run4.log")), "pages_last": tail(os.path.join(gr, "_run4.log")),
                      "search_log_changed": mtime(os.path.join(gr, "_search.log")), "search_last": tail(os.path.join(gr, "_search.log"))},
        "translators": {"nwf_done": nwf_done, "nwf_total": nwf_total, "nwf_log_changed": mtime(os.path.join(tr, "_nwf.log"))},
        "metadata": {"web_log_changed": mtime(os.path.join(md, "_web.log")), "web_last": tail(os.path.join(md, "_web.log")),
                     "files": sorted(os.path.basename(p) for p in glob.glob(os.path.join(md, "assignments*.json")))},
        "newest_backups": sorted(os.listdir(R("backups")))[-3:],
    }


def log(n):
    heads = [l.strip()[3:] for l in open(R("log.md"), encoding="utf-8") if l.startswith("## ")]
    return heads[-n:]


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--log") + 1]) if "--log" in sys.argv else 12
    print(json.dumps({
        "collected_riyadh": time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 3 * 3600)),
        "version": open(R("VERSION")).read().strip(),
        "log_changed": mtime(R("log.md")), "state_changed": mtime(R("APP_STATE.md")),
        "db": db(), "research": research(), "log_headings": log(n),
    }, ensure_ascii=False, indent=1))
