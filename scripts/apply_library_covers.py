"""INBOXCOVERS (Ibrahim 2026-10-30 «خل البلاغ يحمل الاغلفة من التحديث القادم»): put the covers the library's staff
changed (sent inside the sealed inbox file, saved by scripts/inbox_pull.py to data/inbox_received/<stem>_covers/)
into this catalog, so Ibrahim's Mac shows the same pictures as the library.

  backend/venv/bin/python scripts/apply_library_covers.py data/inbox_received/<stem>.json [--apply]

- a picture is applied only when it differs from ours (sha256); our old picture is copied first to
  backups/covers_before_<stem>/ (rule 11: nothing is lost)
- the cover's source stays the library's («staff» when it was uploaded there), audit `library.cover_applied` with the
  inbox file, who changed it at the library and when (rule 12)
- a cover the library REMOVED is only listed: removing ours needs Ibrahim's word (rule 11)
Without --apply nothing is written."""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import covers  # noqa: E402
import models  # noqa: E402


def plan(meta_path):
    meta = json.load(open(meta_path, encoding="utf-8"))
    stem = os.path.basename(meta_path)[:-len(".json")]
    cdir = os.path.join(os.path.dirname(meta_path), stem + "_covers")
    conn = models.get_connection()
    todo, same, removed, missing = [], [], [], []
    try:
        for c in meta.get("covers") or []:
            bid = int(c["book_id"])
            row = conn.execute("SELECT title, cover_url FROM books WHERE book_id = ?", (bid,)).fetchone()
            if row is None:
                missing.append((bid, "no such book here"))
                continue
            if c.get("removed"):
                removed.append((bid, row[0], c.get("by"), c.get("at")))
                continue
            path = os.path.join(cdir, f"{bid}.{c.get('ext')}")
            if not os.path.isfile(path):
                missing.append((bid, "picture not in the folder"))
                continue
            data = open(path, "rb").read()
            if hashlib.sha256(data).hexdigest() != c.get("sha256"):
                missing.append((bid, "picture damaged (sha256)"))
                continue
            ours_name = (row[1] or "").split("?")[0].rsplit("/", 1)[-1] if (row[1] or "").startswith("/covers/") else ""
            ours = covers.cover_path(ours_name) if ours_name else None
            if ours and hashlib.sha256(open(ours, "rb").read()).hexdigest() == c.get("sha256"):
                same.append(bid)
                continue
            todo.append({"book_id": bid, "title": row[0], "data": data, "ours": ours, "by": c.get("by"), "at": c.get("at"),
                         "source": c.get("source") if c.get("source") in covers.SOURCES else "staff"})
    finally:
        conn.close()
    return stem, todo, same, removed, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("meta_json")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    stem, todo, same, removed, missing = plan(a.meta_json)
    print(f"{stem}: to apply {len(todo)} · already the same here {len(same)} · removed at the library {len(removed)} · skipped {len(missing)}")
    for t in todo:
        print(f"  COVER {t['book_id']} «{t['title']}» ({t['by']} {t['at']}){' — replaces ours' if t['ours'] else ' — new here'}")
    for bid, title, by, at in removed:
        print(f"  REMOVED AT THE LIBRARY {bid} «{title}» ({by} {at}) — ours kept; removing needs Ibrahim's word")
    for bid, why in missing:
        print(f"  SKIP {bid}: {why}")
    if not a.apply:
        print("(dry run: nothing written)")
        return
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(models.__file__)))   # the catalog's own folder
    bdir = os.path.join(app_root, "backups", "covers_before_" + stem)
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    for t in todo:
        if t["ours"]:
            os.makedirs(bdir, exist_ok=True)
            shutil.copy2(t["ours"], os.path.join(bdir, os.path.basename(t["ours"])))
        res = covers.save_cover(t["book_id"], t["data"], t["source"])
        conn = models.get_connection()
        conn.execute("INSERT INTO audit_log (at, username, action, target, details) VALUES (?, ?, ?, ?, ?)",
                     (now, "MGR (Claude)", "library.cover_applied", str(t["book_id"]),
                      json.dumps({"source": f"inbox {stem}", "by_at_library": t["by"], "at_library": t["at"],
                                  "cover_url": res["cover_url"], "old_kept_in": bdir if t["ours"] else None}, ensure_ascii=False)))
        conn.commit()
        conn.close()
    print(f"applied {len(todo)} covers" + (f"; old pictures in {bdir}" if any(t['ours'] for t in todo) else ""))


if __name__ == "__main__":
    main()
