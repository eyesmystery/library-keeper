"""Install an update of «منار» WITHOUT losing what the library changed on its own computer.

A full package (scripts/make_release.py) replaces everything. An update package is different:

  kept as they are      staff accounts and passwords, settings and the AI model choice, events and their
                        pictures, library sections, stock (price, quantity, active, barcode, SKU),
                        visitor activity, staff notes and requests, the audit log, backups
  replaced              the code: backend/ (not its Python environment), frontend/, admin/, scripts/,
                        VERSION, the start files
  merged, row by row    the catalog knowledge: books (all but the stock fields; the shelf is merged too since
                        2026-10-11), book cards, evidence,
                        authors and their links, relations, prizes, adaptations, meaning-search vectors,
                        cover pictures

The merge is a three-way one. The catalog the library installed last time is kept as
data/catalog_base.db («base»). For every field:

  library == base                 -> the update's value is written
  library != base, update == base -> the library's value stays (only they changed it)
  all three differ                -> the library's value stays and the case is written to the report
                                     (updates/update_report_*.md) that the library sends to Ibrahim

Rows the update added are inserted, rows it removed are removed when the library did not change them,
rows the library added or removed are left alone. A book deleted in the update («ليس كتابًا») is deleted
here too, with its record in the deleted-books list, unless the library changed it.

Identifiers: rows created in the library get ids from 1,000,000,000 up (the sequences are raised by
make_release.py and again here), so an id below that always means the same row here and in the update.
A row the library created earlier with a low id is moved up when the update brings a different row
with that id; a book or author both sides added is recognised (SKU / barcode, author name) and joined.

First update of an installation that has no data/catalog_base.db: the base is taken from the update
package when it carries one for the installed version (bases/<version>.db); otherwise the update's
catalog wins for the books both sides have, and every value it replaced is listed in the report.

Safety: the server must be stopped; the database is merged in a copy and swapped in only at the end;
the old database, code and replaced covers are kept under backups/ and data/.

Usage (the install_update start file runs this with the app's own Python):
    python apply_update.py [--app <app folder>] [--dry-run]
Standard library only.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys

LOCAL_ID_START = 1_000_000_000
PORT = 8802
# stock and identity-from-Rewaa fields of `books`: always the library's, never written for an existing book
# shelf_location left this list on 2026-10-11 (SHELF, Ibrahim: «بالنسبة للمواقع اعتمد اللي في التحديث»): the shelves come
# from the library's own sheet imported on Ibrahim's side and travel with the update; a shelf the staff change in the
# book card is kept (three-way merge), recorded in the staff changes file, and Ibrahim applies it on his side too.
BOOK_LOCAL_COLS = ("price", "quantity", "active", "barcode", "rewaa_sku", "source", "last_synced_at")
BOOK_REF_COLS = ("book_id", "source_book_id", "target_book_id")
# order matters: parents first
TABLES = [
    {"name": "authors", "key": ("author_id",), "auto": True, "natural": ("name_ar",)},
    {"name": "books", "key": ("book_id",), "auto": True, "local": BOOK_LOCAL_COLS},
    {"name": "book_profiles", "key": ("book_id",), "ignore": ("profile_id",)},
    {"name": "book_authors", "key": ("book_id", "author_id", "role"), "derived": True},
    {"name": "attribute_evidence", "key": ("evidence_id",), "auto": True,
     "natural": ("book_id", "attribute_name", "attribute_value", "source_site")},
    {"name": "book_relations", "key": ("relation_id",), "auto": True, "derived": True,
     "natural": ("source_book_id", "target_book_id", "relation_type")},
    {"name": "book_awards", "key": ("row_id",), "auto": True,
     "natural": ("book_id", "award_key", "result", "year", "category")},
    {"name": "author_awards", "key": ("row_id",), "auto": True,
     "natural": ("author_id", "award_key", "result", "year", "work_title")},
    {"name": "book_adaptations", "key": ("row_id",), "auto": True, "natural": ("book_id", "kind", "title", "year")},
    {"name": "book_embeddings", "key": ("book_id",), "derived": True, "quiet": True},
]
CATALOG_TABLES = [t["name"] for t in TABLES] + ["deleted_books"]
# rows of these tables go with a deleted book / author
BOOK_CHILDREN = [("book_profiles", "book_id"), ("attribute_evidence", "book_id"), ("book_authors", "book_id"),
                 ("book_relations", "source_book_id"), ("book_relations", "target_book_id"),
                 ("book_awards", "book_id"), ("book_adaptations", "book_id"), ("book_embeddings", "book_id")]
AUTHOR_CHILDREN = [("book_authors", "author_id"), ("author_awards", "author_id")]
MAX_LISTED = 2000   # report lists are capped; the counts are always complete


class UpdateError(Exception):
    pass


class RollbackIncomplete(Exception):
    """Undoing a half-done step did not fully work; .args[0] is the list of what could not be put back."""


def q(name):
    return '"' + name.replace('"', '""') + '"'


def tables_of(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def columns_of(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({q(table)})")]


def load(con, table, cols, key):
    out = {}
    for r in con.execute(f"SELECT {', '.join(q(c) for c in cols)} FROM {q(table)}"):
        d = dict(zip(cols, r))
        out[tuple(d[k] for k in key)] = d
    return out


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def same_book(a, b):
    """Is it the same product? SKU decides when both have one, then the barcode, then title + author."""
    if a.get("rewaa_sku") and b.get("rewaa_sku"):
        return a["rewaa_sku"] == b["rewaa_sku"]
    if a.get("barcode") and b.get("barcode"):
        return a["barcode"] == b["barcode"]
    return norm(a.get("title")) == norm(b.get("title")) and norm(a.get("author")) == norm(b.get("author"))


def same_row(spec, a, b):
    if spec["name"] == "books":
        return same_book(a, b)
    nat = spec.get("natural")
    if not nat:
        return True
    return all((norm(a.get(c)) if isinstance(a.get(c), str) else a.get(c)) ==
               (norm(b.get(c)) if isinstance(b.get(c), str) else b.get(c)) for c in nat)


def empty(v):
    return v is None or v == "" or v == "[]" or v == "{}"


def show(v):
    if isinstance(v, (bytes, memoryview)):
        return "<%d bytes>" % len(v)
    if isinstance(v, str) and len(v) > 300:
        return v[:300] + "…"
    return v


# ------------------------------------------------------------------ building the catalog file (on the Mac)
def file_sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def cover_manifest(covers_dir):
    out = {}
    if covers_dir and os.path.isdir(covers_dir):
        for name in sorted(os.listdir(covers_dir)):
            p = os.path.join(covers_dir, name)
            if os.path.isfile(p) and not name.startswith("."):
                out[name] = file_sha1(p)
    return out


def build_catalog(src_db, dest, meta, covers_dir=None):
    """Copy the catalog tables of `src_db` into a new SQLite file `dest`, with the list of cover files
    (`covers_dir`: the covers folder, or a ready {file name: sha1} dict).
    This file is both the update's catalog and, once installed, the library's next base."""
    if os.path.exists(dest):
        os.remove(dest)
    out = sqlite3.connect(dest)
    try:
        out.execute("ATTACH DATABASE ? AS src", (src_db,))
        have = {r[0] for r in out.execute("SELECT name FROM src.sqlite_master WHERE type='table'")}
        for table in CATALOG_TABLES:
            if table not in have:
                continue
            sql = out.execute("SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
            out.execute(sql)
            out.execute(f"INSERT INTO main.{q(table)} SELECT * FROM src.{q(table)}")
        out.execute("CREATE TABLE update_meta (key TEXT PRIMARY KEY, value TEXT)")
        out.executemany("INSERT INTO update_meta VALUES (?, ?)", [(k, str(v)) for k, v in sorted(meta.items())])
        out.execute("CREATE TABLE cover_files (name TEXT PRIMARY KEY, sha1 TEXT NOT NULL)")
        covers = covers_dir if isinstance(covers_dir, dict) else cover_manifest(covers_dir)
        out.executemany("INSERT INTO cover_files VALUES (?, ?)", sorted(covers.items()))
        out.commit()
        out.execute("DETACH DATABASE src")
        out.execute("VACUUM")
    finally:
        out.close()
    return dest


def raise_sequences(con):
    """Rows created from now on in this database get ids from LOCAL_ID_START up."""
    if "sqlite_sequence" not in tables_of(con):
        return
    have = tables_of(con)
    for spec in TABLES:
        if not spec.get("auto") or spec["name"] not in have:
            continue
        key = spec["key"][0]
        top = con.execute(f"SELECT MAX({q(key)}) FROM {q(spec['name'])}").fetchone()[0] or 0
        want = max(LOCAL_ID_START - 1, top)
        cur = con.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (spec["name"],)).fetchone()
        if cur is None:
            con.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (spec["name"], want))
        elif cur[0] < want:
            con.execute("UPDATE sqlite_sequence SET seq=? WHERE name=?", (want, spec["name"]))


def meta_of(con):
    if "update_meta" not in tables_of(con):
        return {}
    return dict(con.execute("SELECT key, value FROM update_meta"))


# ------------------------------------------------------------------ the merge
class Merge:
    def __init__(self, local, new, base):
        self.L, self.N, self.B = local, new, base
        self.base_mode = base is not None
        self.report = {"mode": "three-way" if self.base_mode else "first-update-without-base",
                       "tables": {}, "conflicts": [], "kept_after_removal": [], "replaced_values": [],
                       "removed_local_rows": [], "books_deleted": [], "cover_renames": [], "notes": []}
        self.next_id = {}

    # ---- helpers
    def stat(self, table, what, n=1):
        t = self.report["tables"].setdefault(table, {})
        t[what] = t.get(what, 0) + n

    def listed(self, name, item):
        if len(self.report[name]) < MAX_LISTED:
            self.report[name].append(item)

    def label(self, table, row):
        for c in ("title", "name_ar", "book_title", "award_name", "attribute_name"):
            if row.get(c):
                return str(row[c])[:80]
        return ""

    def fresh_id(self, spec):
        t, k = spec["name"], spec["key"][0]
        if t not in self.next_id:
            top = self.L.execute(f"SELECT MAX({q(k)}) FROM {q(t)}").fetchone()[0] or 0
            self.next_id[t] = max(LOCAL_ID_START, top + 1)
        v = self.next_id[t]
        self.next_id[t] = v + 1
        return v

    def renumber(self, spec, old, new):
        """Give one row a new id and carry every reference to it along."""
        t, k = spec["name"], spec["key"][0]
        if t == "books":
            for table in tables_of(self.L):
                if table == "deleted_books":   # records of other, already deleted books
                    continue
                for c in columns_of(self.L, table):
                    if c in BOOK_REF_COLS:
                        self.L.execute(f"UPDATE {q(table)} SET {q(c)}=? WHERE {q(c)}=?", (new, old))
            self.L.execute("UPDATE books SET cover_url = REPLACE(cover_url, ?, ?) WHERE book_id=? AND cover_url LIKE '/covers/%'",
                           (f"/covers/{old}.", f"/covers/{new}.", new))
            self.report["cover_renames"].append([old, new])
        elif t == "authors":
            for table in tables_of(self.L):
                if "author_id" in columns_of(self.L, table):
                    self.L.execute(f"UPDATE {q(table)} SET author_id=? WHERE author_id=?", (new, old))
        else:
            self.L.execute(f"UPDATE {q(t)} SET {q(k)}=? WHERE {q(k)}=?", (new, old))
        self.stat(t, "renumbered")

    def ensure_schema(self, spec):
        """The library's table gets the columns (or the whole table) the update's catalog has."""
        t = spec["name"]
        if t not in tables_of(self.L):
            for (sql,) in self.N.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL "
                                         "ORDER BY type DESC", (t,)):
                self.L.execute(sql)
            return
        have = set(columns_of(self.L, t))
        for _cid, name, ctype, notnull, default, _pk in self.N.execute(f"PRAGMA table_info({q(t)})"):
            if name in have:
                continue
            decl = f"ALTER TABLE {q(t)} ADD COLUMN {q(name)} {ctype or ''}"
            if default is not None:
                decl += (" NOT NULL" if notnull else "") + f" DEFAULT {default}"
            self.L.execute(decl)

    def parents_ok(self, row):
        for c in BOOK_REF_COLS:
            if c in row and row[c] not in self.book_ids:
                return False
        return not ("author_id" in row and row["author_id"] not in self.author_ids)

    def refresh_parents(self):
        self.book_ids = {r[0] for r in self.L.execute("SELECT book_id FROM books")}
        self.author_ids = ({r[0] for r in self.L.execute("SELECT author_id FROM authors")}
                           if "authors" in tables_of(self.L) else set())

    # ---- one table
    def merge_table(self, spec):
        t, key = spec["name"], spec["key"]
        if t not in tables_of(self.N):
            return
        self.ensure_schema(spec)
        cols = [c for c in columns_of(self.N, t) if c not in spec.get("ignore", ())]
        mcols = [c for c in cols if c not in key and c not in spec.get("local", ())]
        N = load(self.N, t, cols, key)
        bcols = []
        B = {}
        if self.base_mode and t in tables_of(self.B):
            bcols = [c for c in columns_of(self.B, t) if c in cols]
            B = load(self.B, t, bcols, key)
        L = load(self.L, t, cols, key)

        # -- rows that carry the same id but are not the same thing, and rows both sides added
        if spec.get("auto"):
            if self.base_mode:
                local_only = {k for k in L if k not in B}
                added = [k for k in N if k not in B]
            else:
                local_only = {k for k in L if k not in N or not same_row(spec, N[k], L[k])}
                added = [k for k in N if k not in L or k in local_only]
            for k in added:
                if k in L and k in local_only and not same_row(spec, N[k], L[k]):
                    nid = self.fresh_id(spec)
                    self.renumber(spec, k[0], nid)
                    L[(nid,)] = L.pop(k)
                    L[(nid,)][key[0]] = nid
                    local_only.discard(k)
                    local_only.add((nid,))
            index = {}
            for k in local_only:
                row = L[k]
                if t == "books":
                    for c in ("rewaa_sku", "barcode"):
                        if row.get(c):
                            index.setdefault((c, row[c]), k)
                elif spec.get("natural"):
                    index.setdefault(tuple(norm(row.get(c)) if isinstance(row.get(c), str) else row.get(c)
                                           for c in spec["natural"]), k)
            for k in added:
                if k in L:
                    continue
                n = N[k]
                if t == "books":
                    hit = next((index[(c, n[c])] for c in ("rewaa_sku", "barcode") if n.get(c) and (c, n[c]) in index), None)
                else:
                    hit = index.get(tuple(norm(n.get(c)) if isinstance(n.get(c), str) else n.get(c)
                                          for c in spec.get("natural", ()))) if spec.get("natural") else None
                if hit is not None and hit in L and hit in local_only and same_row(spec, n, L[hit]):
                    self.renumber(spec, hit[0], k[0])
                    L[k] = L.pop(hit)
                    L[k][key[0]] = k[0]
                    local_only.discard(hit)
                    local_only.add(k)
                    self.stat(t, "joined")
            if t in ("books", "authors"):
                self.refresh_parents()

        where = " AND ".join(f"{q(c)}=?" for c in key)
        for k, n in N.items():
            l = L.get(k)
            b = B.get(k) if self.base_mode else l
            if l is None:
                if self.base_mode and k in B:
                    self.stat(t, "removed_by_library_left_out")
                    continue
                if t not in ("books", "authors") and not self.parents_ok(n):
                    self.stat(t, "skipped_parent_missing")
                    continue
                self.L.execute(f"INSERT INTO {q(t)} ({', '.join(q(c) for c in cols)}) VALUES ({', '.join('?' * len(cols))})",
                               [n[c] for c in cols])
                self.stat(t, "added")
                continue
            sets = {}
            both_added = self.base_mode and k not in B
            for c in mcols:
                lv, nv = l[c], n[c]
                if lv == nv:
                    continue
                if both_added:
                    if empty(lv):
                        sets[c] = nv
                    elif not empty(nv):
                        self.conflict(t, k, c, l, None, nv)
                    continue
                bv = b[c] if (c in b) else lv      # a column the base did not have yet: the update's value
                if lv == bv:
                    sets[c] = nv
                    if not self.base_mode and not spec.get("quiet") and not empty(lv):
                        self.stat(t, "values_replaced")
                        self.listed("replaced_values", {"table": t, "key": list(k), "what": self.label(t, l), "column": c,
                                                        "library": show(lv), "update": show(nv)})
                elif nv == bv:
                    self.stat(t, "library_values_kept")
                else:
                    self.conflict(t, k, c, l, bv, nv)
            if sets:
                self.L.execute(f"UPDATE {q(t)} SET {', '.join(q(c) + '=?' for c in sets)} WHERE {where}",
                               list(sets.values()) + list(k))
                self.stat(t, "rows_updated")
                self.stat(t, "fields_updated", len(sets))

        # -- rows the update no longer has
        if t in ("books", "authors"):
            self.refresh_parents()
            return
        if self.base_mode:
            for k, b in B.items():
                if k in N or k not in L:
                    continue
                l = L[k]
                if all(l[c] == b[c] for c in mcols if c in b):
                    self.L.execute(f"DELETE FROM {q(t)} WHERE {where}", list(k))
                    self.stat(t, "removed")
                else:
                    self.stat(t, "kept_after_removal")
                    self.listed("kept_after_removal", {"table": t, "key": list(k), "what": self.label(t, l)})
        else:
            # first update without a base: for books and authors the update knows, the library's rows
            # that the update does not have are removed and saved in the report
            n_books = {k[0] for k in load(self.N, "books", ["book_id"], ("book_id",))}
            n_auth = ({k[0] for k in load(self.N, "authors", ["author_id"], ("author_id",))}
                      if "authors" in tables_of(self.N) else set())
            natural = spec.get("natural")
            seen = {tuple(norm(n.get(c)) if isinstance(n.get(c), str) else n.get(c) for c in natural)
                    for n in N.values()} if natural else set()
            for k, l in L.items():
                if k in N:
                    continue
                if natural and tuple(norm(l.get(c)) if isinstance(l.get(c), str) else l.get(c) for c in natural) in seen:
                    self.L.execute(f"DELETE FROM {q(t)} WHERE {where}", list(k))   # the same row under another id
                    self.stat(t, "removed_duplicates")
                    continue
                owners = [l[c] in n_books for c in BOOK_REF_COLS if c in l] + \
                         ([l["author_id"] in n_auth] if "author_id" in l else [])
                if not owners or not all(owners):
                    continue        # belongs to a book or author only the library has
                self.L.execute(f"DELETE FROM {q(t)} WHERE {where}", list(k))
                self.stat(t, "removed")
                if not spec.get("derived"):
                    self.listed("removed_local_rows", {"table": t, "row": {c: show(v) for c, v in l.items()}})

    def conflict(self, t, k, c, l, bv, nv):
        self.stat(t, "conflicts")
        self.listed("conflicts", {"table": t, "key": list(k), "what": self.label(t, l), "column": c,
                                  "library": show(l[c]), "update": show(nv), "before": show(bv)})

    # ---- books and authors the update no longer has
    def book_deletions(self):
        if "deleted_books" not in tables_of(self.N):
            return
        if "deleted_books" not in tables_of(self.L):
            self.L.execute(self.N.execute("SELECT sql FROM sqlite_master WHERE name='deleted_books'").fetchone()[0])
        dcols = [c for c in columns_of(self.N, "deleted_books") if c in columns_of(self.L, "deleted_books") and c != "deletion_id"]
        known = {tuple(r) for r in self.L.execute("SELECT book_id, deleted_at FROM deleted_books")}
        if self.base_mode and "deleted_books" in tables_of(self.B):
            known |= {tuple(r) for r in self.B.execute("SELECT book_id, deleted_at FROM deleted_books")}
        n_books = {r[0] for r in self.N.execute("SELECT book_id FROM books")}
        bcols = [c for c in columns_of(self.L, "books")]
        base_books = {}
        if self.base_mode and "books" in tables_of(self.B):
            base_books = load(self.B, "books", columns_of(self.B, "books"), ("book_id",))
        for d in self.N.execute(f"SELECT {', '.join(q(c) for c in dcols)} FROM deleted_books "
                                "WHERE restored_at IS NULL ORDER BY deletion_id").fetchall():
            d = dict(zip(dcols, d))
            bid = d["book_id"]
            if (bid, d.get("deleted_at")) in known or bid in n_books:
                continue
            row = self.L.execute(f"SELECT {', '.join(q(c) for c in bcols)} FROM books WHERE book_id=?", (bid,)).fetchone()
            if row is None:
                continue
            l = dict(zip(bcols, row))
            if not same_book(l, d):
                continue
            b = base_books.get((bid,))
            if b is not None and any(l.get(c) != b[c] for c in b if c not in BOOK_LOCAL_COLS and c != "book_id"):
                self.stat("books", "kept_after_removal")
                self.listed("kept_after_removal", {"table": "books", "key": [bid], "what": l.get("title")})
                continue
            self.L.execute(f"INSERT INTO deleted_books ({', '.join(q(c) for c in dcols)}) VALUES ({', '.join('?' * len(dcols))})",
                           [d[c] for c in dcols])
            have = tables_of(self.L)
            for table, col in BOOK_CHILDREN:
                if table in have:
                    self.L.execute(f"DELETE FROM {q(table)} WHERE {q(col)}=?", (bid,))
            self.L.execute("DELETE FROM books WHERE book_id=?", (bid,))
            self.stat("books", "removed")
            self.listed("books_deleted", {"book_id": bid, "title": l.get("title"), "reason": d.get("reason")})

    def author_removals(self):
        if not self.base_mode or "authors" not in tables_of(self.B) or "authors" not in tables_of(self.N):
            return
        cols = [c for c in columns_of(self.B, "authors") if c in columns_of(self.L, "authors")]
        B = load(self.B, "authors", cols, ("author_id",))
        N = {r[0] for r in self.N.execute("SELECT author_id FROM authors")}
        L = load(self.L, "authors", cols, ("author_id",))
        have = tables_of(self.L)
        for k, b in B.items():
            if k[0] in N or k not in L:
                continue
            used = any(self.L.execute(f"SELECT 1 FROM {q(t)} WHERE {q(c)}=? LIMIT 1", k).fetchone()
                       for t, c in AUTHOR_CHILDREN if t in have)
            if used or L[k] != b:
                self.stat("authors", "kept_after_removal")
                self.listed("kept_after_removal", {"table": "authors", "key": list(k), "what": L[k].get("name_ar")})
                continue
            self.L.execute("DELETE FROM authors WHERE author_id=?", k)
            self.stat("authors", "removed")

    def run(self):
        before = self.L.execute("PRAGMA foreign_key_check").fetchall()
        self.refresh_parents()
        for spec in TABLES:
            self.merge_table(spec)
        self.book_deletions()
        self.author_removals()
        raise_sequences(self.L)
        after = self.L.execute("PRAGMA foreign_key_check").fetchall()
        if len(after) > len(before):
            raise UpdateError(f"the merge would leave {len(after) - len(before)} broken references; nothing was changed")
        ok = self.L.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise UpdateError("integrity check failed after the merge: " + str(ok))
        return self.report


def merge(local_db, new_catalog, base_catalog=None):
    """Merge the update's catalog into `local_db` (a COPY of the library's database) -> report dict."""
    L = sqlite3.connect(local_db)
    L.execute("PRAGMA foreign_keys=OFF")
    N = sqlite3.connect(f"file:{new_catalog}?mode=ro", uri=True)
    B = sqlite3.connect(f"file:{base_catalog}?mode=ro", uri=True) if base_catalog else None
    try:
        L.execute("BEGIN")
        report = Merge(L, N, B).run()
        L.commit()
        report["update"] = meta_of(N)
        report["base"] = meta_of(B) if B else None
        return report
    except Exception:
        L.rollback()
        raise
    finally:
        L.close()
        N.close()
        if B:
            B.close()


# ------------------------------------------------------------------ installing (in the library)
def server_running(port=PORT):
    s = socket.socket()
    s.settimeout(1.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def copy_db(src, dest):
    a = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    b = sqlite3.connect(dest)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()


def pick_base(app, upd, installed):
    own = os.path.join(app, "data", "catalog_base.db")
    if os.path.exists(own):
        return own, "data/catalog_base.db"
    tag = installed.replace(" beta", "b").replace(" ", "")
    shipped = os.path.join(upd, "bases", tag + ".db")
    if tag and os.path.exists(shipped):
        return shipped, "the update's base for version " + installed
    return None, None


def merge_covers(app, upd, base_catalog, renames, keep_dir, dry, undo=None):
    """Cover files: renamed with a renumbered book, then the update's new or changed pictures.
    A picture the library replaced itself (differs from the base list) is kept.
    Every change is noted in `undo` (newest last) so that undo_covers() can take it back."""
    covers = os.path.join(app, "data", "covers")
    out = {"added": 0, "replaced": 0, "library_kept": 0, "renamed": 0}
    undo = undo if undo is not None else []
    if not dry:
        os.makedirs(covers, exist_ok=True)
    for old, new in renames:
        if not os.path.isdir(covers):
            break
        for name in os.listdir(covers):
            stem, ext = os.path.splitext(name)
            if stem == str(old):
                out["renamed"] += 1
                if not dry:
                    was, now = os.path.join(covers, name), os.path.join(covers, str(new) + ext)
                    if os.path.exists(now):  # a picture already under the new number: kept, so the rename can be undone
                        os.makedirs(keep_dir, exist_ok=True)
                        saved = os.path.join(keep_dir, "before_rename_" + str(new) + ext)
                        shutil.copy2(now, saved)
                        undo.append(("restore", saved, now))
                    os.replace(was, now)
                    undo.append(("rename", now, was))
    src = os.path.join(upd, "covers")
    if not os.path.isdir(src):
        return out
    base = None
    if base_catalog:
        con = sqlite3.connect(f"file:{base_catalog}?mode=ro", uri=True)
        try:
            if "cover_files" in tables_of(con):
                base = dict(con.execute("SELECT name, sha1 FROM cover_files"))
        finally:
            con.close()
    for name in sorted(os.listdir(src)):
        s, d = os.path.join(src, name), os.path.join(covers, name)
        if not os.path.isfile(s):
            continue
        if not os.path.exists(d):
            out["added"] += 1
            if not dry:
                undo.append(("remove", d, None))
                shutil.copy2(s, d)
            continue
        mine = file_sha1(d)
        if mine == file_sha1(s):
            continue
        if base is not None and base.get(name) != mine:
            out["library_kept"] += 1
            continue
        out["replaced"] += 1
        if not dry:
            os.makedirs(keep_dir, exist_ok=True)
            shutil.copy2(d, os.path.join(keep_dir, name))
            undo.append(("restore", os.path.join(keep_dir, name), d))
            shutil.copy2(s, d)
    return out


def undo_covers(undo):
    """Take back what merge_covers() did, newest change first. -> list of problems (empty = all undone)."""
    problems = []
    for kind, a, b in reversed(undo):
        try:
            if kind == "remove":
                if os.path.exists(a):
                    os.remove(a)
            elif kind == "restore":
                shutil.copy2(a, b)
            elif kind == "rename":
                if os.path.exists(a):
                    os.replace(a, b)
        except OSError as e:
            problems.append(f"cover {os.path.basename(b or a)}: {e}")
    return problems


CODE_DIRS = ("backend", "frontend", "admin", "scripts")
KEEP_IN_BACKEND = ("venv", "venv-win")


def undo_code(journal):
    """Put the old code back: what the update wrote is removed, what was moved to backups/ returns.
    -> list of problems (empty = all undone)."""
    problems = []
    for kind, a, b in reversed(journal):
        try:
            if kind == "created":
                if os.path.isdir(a) and not os.path.islink(a):
                    shutil.rmtree(a)
                elif os.path.lexists(a):
                    os.remove(a)
            else:  # "moved": a = where the old one was put, b = where it belongs
                if os.path.isdir(b) and not os.path.islink(b):
                    shutil.rmtree(b)
                elif os.path.lexists(b):
                    os.remove(b)
                shutil.move(a, b)
        except OSError as e:
            problems.append(f"code {os.path.basename(b or a)}: {e}")
    return problems


def swap_code(app, upd, keep, journal=None):
    """Old code moves to backups/…/code, the update's code takes its place. The Python environment stays.
    `journal` receives every step, so a failure later in the update can undo the swap (undo_code)."""
    payload = os.path.join(upd, "payload")
    journal = journal if journal is not None else []
    try:
        for d in CODE_DIRS:
            src, dst, old = os.path.join(payload, d), os.path.join(app, d), os.path.join(keep, "code", d)
            if not os.path.isdir(src):
                continue
            os.makedirs(old, exist_ok=True)
            if os.path.isdir(dst):
                for name in os.listdir(dst):
                    if d == "backend" and name in KEEP_IN_BACKEND:
                        continue
                    shutil.move(os.path.join(dst, name), os.path.join(old, name))
                    journal.append(("moved", os.path.join(old, name), os.path.join(dst, name)))
            os.makedirs(dst, exist_ok=True)
            for name in os.listdir(src):
                s, t = os.path.join(src, name), os.path.join(dst, name)
                journal.append(("created", t, None))
                if os.path.isdir(s):
                    shutil.copytree(s, t)
                else:
                    shutil.copy2(s, t)
        platform = "windows" if os.name == "nt" else "mac"
        extra = [os.path.join(payload, n) for n in ("VERSION", "BUILD.json")]
        start = os.path.join(payload, "start", platform)
        if os.path.isdir(start):
            for base, _dirs, files in os.walk(start):
                extra += [os.path.join(base, f) for f in files]
        for s in extra:
            if not os.path.isfile(s):
                continue
            rel = os.path.relpath(s, start) if s.startswith(start) else os.path.basename(s)
            t = os.path.join(app, rel)
            if os.path.exists(t) and not rel.startswith("scripts"):
                os.makedirs(os.path.dirname(os.path.join(keep, "code", rel)), exist_ok=True)
                shutil.move(t, os.path.join(keep, "code", rel))
                journal.append(("moved", os.path.join(keep, "code", rel), t))
            os.makedirs(os.path.dirname(t), exist_ok=True)
            if not os.path.exists(t):
                journal.append(("created", t, None))
            shutil.copy2(s, t)
    except Exception:
        problems = undo_code(journal)     # put the old code back
        del journal[:]
        if problems:
            raise RollbackIncomplete(problems)
        raise


def report_text(rep):
    lines = ["# Update report — منار", "",
             f"- Installed version before: {rep.get('from_version') or '?'}",
             f"- Update: {(rep.get('update') or {}).get('version', '?')} (built {(rep.get('update') or {}).get('built_at_riyadh', '?')}, "
             f"commit {(rep.get('update') or {}).get('git_commit', '?')})",
             f"- When: {rep.get('at')}", f"- Mode: {rep['mode']} (base: {rep.get('base_source') or 'none'})",
             f"- Dry run: {'yes, nothing was changed' if rep.get('dry_run') else 'no'}", "", "## Counts", ""]
    for t, s in rep["tables"].items():
        lines.append(f"- {t}: " + ", ".join(f"{k} {v}" for k, v in sorted(s.items())))
    lines.append(f"- covers: " + ", ".join(f"{k} {v}" for k, v in sorted((rep.get('covers') or {}).items())))
    sections = [("conflicts", "Conflicts: the library's value was kept, the update had another one",
                 lambda c: f"- {c['table']} {c['key']} «{c['what']}» · {c['column']}: library = {c['library']!r} | update = {c['update']!r} | before = {c['before']!r}"),
                ("kept_after_removal", "Kept although the update removed them (the library changed them or still uses them)",
                 lambda c: f"- {c['table']} {c['key']} «{c['what']}»"),
                ("books_deleted", "Books deleted by the update (restorable from المكتبة ← المحذوفات)",
                 lambda c: f"- {c['book_id']} «{c['title']}» ({c['reason']})"),
                ("replaced_values", "First update without a base: values of the library replaced by the update",
                 lambda c: f"- {c['table']} {c['key']} «{c['what']}» · {c['column']}: was {c['library']!r} | now {c['update']!r}"),
                ("removed_local_rows", "First update without a base: rows of the library removed (full rows in the .json file)",
                 lambda c: f"- {c['table']}: {json.dumps(c['row'], ensure_ascii=False)[:300]}")]
    inbox = rep.get("inbox") or {}
    if inbox.get("closed"):
        lines += ["", f"## Inbox items closed by this update ({len(inbox['closed'])})", ""]
        lines += [f"- {c['kind']} {c['id']} «{c.get('text') or ''}»: {c.get('resolution') or ''}" for c in inbox["closed"]]
    if inbox.get("not_open"):
        lines += ["", f"## Solved items not open here (already handled or never in this inbox) ({len(inbox['not_open'])})", ""]
        lines += [f"- {c['kind']} {c.get('ref') or ''} «{c.get('text') or ''}»" for c in inbox["not_open"]]
    if inbox.get("error"):
        lines += ["", "## Inbox items were not closed", "", f"- {inbox['error']}"]
    for name, title, fmt in sections:
        if rep.get(name):
            lines += ["", f"## {title} ({len(rep[name])}{'+' if len(rep[name]) >= MAX_LISTED else ''})", ""]
            lines += [fmt(c) for c in rep[name]]
    return "\n".join(lines) + "\n"


def roll_back(app, keep, done, error, installed, target, ts, say=print):
    """A step failed after the installation had been changed: put everything back and write a failure
    report. -> the UpdateError to raise (its text is shown to the staff member)."""
    db = os.path.join(app, "data", "library.db")
    problems = list(error.args[0]) if isinstance(error, RollbackIncomplete) else []
    say("✗ خطأ في خطوة التثبيت؛ نُرجع كل شيء كما كان…")
    problems += undo_code(done["code"])
    problems += undo_covers(done["covers"])
    restored_db = False
    if done["db_touched"]:      # the old database is put back from the copy taken in step 1
        tmp = db + ".restoring"
        try:
            copy_db(os.path.join(keep, "library.db"), tmp)
            for tail in ("-wal", "-shm", "-journal"):
                if os.path.exists(db + tail):
                    os.remove(db + tail)
            os.replace(tmp, db)
            restored_db = True
        except Exception as e:  # noqa: BLE001 - reported below with the place of the backup
            problems.append(f"database: {type(e).__name__}: {e}")
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    rel_keep = os.path.relpath(keep, app)
    cause = f"{type(error).__name__}: {error}" if not isinstance(error, RollbackIncomplete) else "rollback of the application files failed"
    name = f"update_FAILED_{(target or 'x').replace(' ', '_')}_{ts}"
    report = None
    try:
        out_dir = os.path.join(app, "updates")
        os.makedirs(out_dir, exist_ok=True)
        info = {"failed": True, "from_version": installed, "to_version": target, "at": ts, "failed_step": done["step"],
                "error": cause, "rolled_back": not problems, "rollback_problems": problems,
                "database_restored_from_backup": restored_db, "backup_folder": rel_keep,
                "version_now": read_text(os.path.join(app, "VERSION"))}
        with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        lines = [f"# Update FAILED — {installed or '?'} → {target or '?'} ({ts})", "",
                 f"- Failed step: {done['step']}", f"- Error: {cause}",
                 f"- Version in the app folder now: {info['version_now'] or '?'}",
                 "- Rolled back completely: " + ("yes — code, version, start files, covers and database are as before the update"
                                                 if not problems else "NO — see below"),
                 f"- Backup taken before the update: `{rel_keep}` (`library.db`, and the old code under `code/` if it was not put back)"]
        if problems:
            lines += ["", "## Not put back", ""] + [f"- {x}" for x in problems] + [
                "", f"Do not start the app. Copy `{rel_keep}/library.db` over `data/library.db`, and the folders under "
                    f"`{rel_keep}/code/` back into the app folder, or send this file to Ibrahim."]
        with open(os.path.join(out_dir, name + ".md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        report = os.path.join("updates", name + ".md")
    except OSError:
        pass
    where = f" التفاصيل في الملف {report}." if report else ""
    if problems:
        return UpdateError("حدث خطأ أثناء التثبيت ولم يكتمل إرجاع الوضع السابق. لا تشغّل التطبيق الآن. "
                           f"النسخة الاحتياطية الكاملة قبل التحديث موجودة في المجلد {rel_keep}.{where} أرسل هذا إلى إبراهيم: {cause}")
    return UpdateError("حدث خطأ أثناء التثبيت، فأُرجع كل شيء كما كان قبل التحديث: ملفات التطبيق والأغلفة وقاعدة البيانات. "
                       f"تقدر تشغّل التطبيق بالإصدار السابق ({installed or '؟'}) كالعادة. نسخة احتياطية في المجلد {rel_keep}.{where} "
                       f"أرسل هذا السطر إلى إبراهيم: {cause}")


# ---- Inbox items solved by this update (Ibrahim 2026-10-11: «لما نعالجها وتنضاف للتحديث والمكتبة بحملون
# التحديث، المفروض البلاغات اللي انحلت تنحذف خلاص او تعامل انها مغلقة محلولة»). The update carries
# inbox_resolutions.json (copied by make_release.py from release/inbox_resolutions.json). Every item with
# "state": "solved" that is still open in the library's inbox is marked handled («تمت معالجته»), handled_by
# «تحديث <version>», and an audit_log row «inbox.resolved_by_update» keeps the resolution note. Nothing is
# deleted: the item moves to the handled list, where staff can reopen it. Items are matched by their number
# in the export (id, written as «رقم البند» since 2026-10-11) and/or their date + text, so the same file is
# safe on any installation: an item that is not there, or already handled, is skipped.
INBOX_KINDS = {
    # kind: (table, id column, extra condition, text column)
    "report": ("visitor_notes", "note_id", "kind = 'report'", "note"),
    "suggestion": ("visitor_notes", "note_id", "kind = 'suggestion'", "note"),
    "question": ("open_questions", "q_id", None, "phrase"),
    "staff_edit": ("staff_notes", "note_id", None, "note"),
    "staff_idea": ("staff_ideas", "idea_id", None, "text"),
}
RIYADH_OFFSET = datetime.timedelta(hours=3)


def riyadh_minute(value):
    """A stored UTC time («2026-10-09T18:04:11+00:00») as the export shows it: «2026-10-09 21:04» Riyadh."""
    try:
        d = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is not None:
        d = d.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return (d + RIYADH_OFFSET).strftime("%Y-%m-%d %H:%M")


def squash(text):
    return " ".join(str(text or "").split())


def inbox_matches(con, item):
    """-> ids of the OPEN inbox rows this resolution item names (usually one, never guessed loosely)."""
    spec = INBOX_KINDS.get(item.get("kind"))
    if not spec:
        return []
    table, id_col, cond, text_col = spec
    if table not in tables_of(con):
        return []
    cols = columns_of(con, table)
    where = "status = 'open'" + (f" AND {cond}" if cond else "")
    cur = con.execute(f"SELECT * FROM {q(table)} WHERE {where}")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    want_text = squash(item.get("text"))
    out = []
    for r in rows:
        if item.get("id") is not None and str(r[id_col]) != str(item["id"]):
            continue
        if item.get("kind") == "question":
            if item.get("question_kind") and r.get("kind") != item["question_kind"]:
                continue
            if want_text and squash(r.get("phrase")) != want_text and r.get("phrase_key") != item.get("phrase_key"):
                continue
        else:
            created = riyadh_minute(r.get("created_at"))
            if item.get("created") and created != item["created"]:
                continue
            if want_text and not squash(r.get(text_col)).startswith(want_text):
                continue
            if item.get("book_id") is not None and "book_id" in cols and str(r.get("book_id")) != str(item["book_id"]):
                continue
        if item.get("id") is None and not (want_text or item.get("created")):
            continue      # an item with nothing to recognise it by closes nothing
        out.append(r[id_col])
    return out


def apply_resolutions(db, upd, version, dry_run=False):
    """-> {"closed": [...], "not_open": [...]} for the report. Never raises: the update is installed already."""
    path = os.path.join(upd, "inbox_resolutions.json")
    result = {"closed": [], "not_open": [], "error": None}
    if not os.path.exists(path):
        return result
    try:
        with open(path, encoding="utf-8") as f:
            items = [i for i in (json.load(f).get("items") or []) if isinstance(i, dict) and i.get("state") == "solved"]
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True) if dry_run else sqlite3.connect(db)
        try:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            by = "تحديث " + (version or "")
            have_audit = "audit_log" in tables_of(con)
            with con:
                for item in items:
                    ids = inbox_matches(con, item)
                    label = {"kind": item.get("kind"), "ref": item.get("ref"), "text": item.get("text"),
                             "resolution": item.get("resolution")}
                    if not ids:
                        result["not_open"].append(label)
                        continue
                    table, id_col = INBOX_KINDS[item["kind"]][:2]
                    for row_id in ids:
                        result["closed"].append(dict(label, id=row_id))
                        if dry_run:
                            continue
                        con.execute(f"UPDATE {q(table)} SET status = 'done', handled_by = ?, handled_at = ? WHERE {q(id_col)} = ?",
                                    (by, now, row_id))
                        if have_audit:
                            con.execute("INSERT INTO audit_log (at, username, action, target, details) VALUES (?, ?, ?, ?, ?)",
                                        (now, by, "inbox.resolved_by_update", f"{item['kind']}:{row_id}",
                                         json.dumps({"ref": item.get("ref"), "resolution": item.get("resolution"),
                                                     "version": version}, ensure_ascii=False)))
        finally:
            con.close()
    except (OSError, ValueError, sqlite3.Error, KeyError, AttributeError) as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


SETTINGS_ALLOWED = {"test_log"}


def apply_settings(app, upd, notes, say=print):
    """Settings the update turns on or off in data/llm_settings.json (Ibrahim 2026-10-10: the next update turns on
    «وضع الاختبار» so the library's conversations are saved and reach him in the inbox file). Only keys in
    SETTINGS_ALLOWED; every other setting of the library (model, keys, budget) stays. Runs after the update is
    installed, so a failure here is a note, never a rollback. -> {key: [before, after]}"""
    path = os.path.join(upd, "settings.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            wanted = {k: v for k, v in json.load(f).items() if k in SETTINGS_ALLOWED}
        target = os.path.join(app, "data", "llm_settings.json")
        current = {}
        if os.path.exists(target):
            with open(target, encoding="utf-8") as f:
                current = json.load(f)
        changes = {k: [current.get(k), v] for k, v in wanted.items() if current.get(k) != v}
        if changes:
            current.update(wanted)
            tmp = target + ".updating"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(current, f, ensure_ascii=False, indent=1)
                f.write("\n")
            os.replace(tmp, target)
        if changes.get("test_log", [None, None])[1] is True:
            say("تم تفعيل «وضع الاختبار»: محادثات الزوار تنحفظ وتدخل في ملف البلاغات.")
        return changes
    except (OSError, ValueError) as e:
        notes.append(f"settings were not changed: {e}")
        say("تنبيه: تم التحديث لكن تعذر تغيير الإعدادات (وضع الاختبار). فعّله من الإعدادات.")
        return {}


def apply(app, upd, dry_run=False, port=PORT, say=print):
    app, upd = os.path.abspath(app), os.path.abspath(upd)
    db = os.path.join(app, "data", "library.db")
    new_catalog = os.path.join(upd, "catalog.db")
    if not os.path.exists(db) or not os.path.isdir(os.path.join(app, "backend")):
        raise UpdateError("هذا ليس مجلد التطبيق: لا يوجد فيه data/library.db. ضع مجلد التحديث داخل مجلد «منار» ثم أعد المحاولة.")
    if not os.path.exists(new_catalog) or not os.path.isdir(os.path.join(upd, "payload")):
        raise UpdateError("مجلد التحديث ناقص (catalog.db أو payload). فك الملف المضغوط كاملًا ثم أعد المحاولة.")
    if not dry_run and server_running(port):
        raise UpdateError("السيرفر يعمل الآن. أغلق نافذة السيرفر أولًا ثم شغّل التحديث من جديد.")
    installed = read_text(os.path.join(app, "VERSION"))
    target = read_text(os.path.join(upd, "payload", "VERSION"))
    ts = stamp()
    keep = os.path.join(app, "backups", f"before_update_{ts}")
    base, base_source = pick_base(app, upd, installed)
    say(f"الإصدار الحالي: {installed or '؟'}  ←  التحديث: {target or '؟'}")
    say("١/٥ نسخة احتياطية من قاعدة البيانات…")
    work = os.path.join(app, "data", f"library.db.updating-{ts}")
    base_new = os.path.join(app, "data", f"catalog_base.db.updating-{ts}")
    copy_db(db, work)
    # Everything that changes the installation is noted here, so that a failure at ANY later step puts
    # the whole installation back: covers, code, version and database together (audit 2026-10-09: a
    # failed database swap used to leave the new code and covers on the old database).
    done = {"covers": [], "code": [], "db_touched": False, "step": "backup"}
    try:
        if not dry_run:
            os.makedirs(keep, exist_ok=True)
            shutil.copy2(work, os.path.join(keep, "library.db"))
        say("٢/٥ دمج الكتالوج (تعديلاتكم المحلية تبقى)…")
        done["step"] = "catalog merge"
        rep = merge(work, new_catalog, base)
        rep.update({"from_version": installed, "to_version": target, "at": ts, "base_source": base_source, "dry_run": dry_run})
        say("٣/٥ الأغلفة…")
        done["step"] = "covers"
        rep["covers"] = merge_covers(app, upd, base, rep["cover_renames"], os.path.join(keep, "covers_replaced"), dry_run,
                                     undo=done["covers"])
        if not dry_run:
            say("٤/٥ ملفات التطبيق…")
            done["step"] = "application files"
            old_req = read_text(os.path.join(app, "backend", "requirements.txt"))
            swap_code(app, upd, keep, done["code"])
            say("٥/٥ تثبيت القاعدة المدموجة…")
            done["step"] = "database swap"
            shutil.copy2(new_catalog, base_new)      # made ready before the database is touched
            for tail in ("-wal", "-shm", "-journal"):
                if os.path.exists(db + tail):
                    done["db_touched"] = True
                    os.remove(db + tail)
            os.replace(work, db)
            done["db_touched"] = True
            done["step"] = "saving the catalog base"
            os.replace(base_new, os.path.join(app, "data", "catalog_base.db"))
            done["step"] = "installed"
            if read_text(os.path.join(app, "backend", "requirements.txt")) != old_req:
                say("تحديث مكتبات بايثون (يحتاج إنترنت)…")
                try:
                    r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "-r",
                                        os.path.join(app, "backend", "requirements.txt")])
                    failed_pip = r.returncode != 0
                except OSError:
                    failed_pip = True
                if failed_pip:
                    rep["notes"].append("pip install failed; run the start file with internet once")
                    say("تنبيه: تعذر تحديث مكتبات بايثون. شغّل التطبيق مرة والإنترنت متصل.")
    except Exception as e:
        if dry_run or done["step"] == "installed" or not (done["covers"] or done["code"] or done["db_touched"]
                                                          or isinstance(e, RollbackIncomplete)):
            raise      # nothing of the installation was changed (or it is complete): the error goes up as it is
        raise roll_back(app, keep, done, e, installed, target, ts, say) from e
    finally:
        for leftover in (work, base_new):
            if os.path.exists(leftover):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
    if not dry_run:
        rep["settings"] = apply_settings(app, upd, rep["notes"], say)
    # dry run: the matches are looked up in the live database without writing anything
    rep["inbox"] = apply_resolutions(db, upd, target, dry_run=dry_run)
    if rep["inbox"]["error"]:
        rep["notes"].append("inbox items were not closed: " + rep["inbox"]["error"])
    elif rep["inbox"]["closed"]:
        say(f"أُغلق {len(rep['inbox']['closed'])} بندًا من صندوق البلاغات عالجها هذا التحديث (تجدونها في «تمت معالجتها»).")
    out_dir = os.path.join(app, "updates")
    name = f"update_report_{(target or 'x').replace(' ', '_')}_{ts}" + ("_dry_run" if dry_run else "")
    rep["report_file"] = os.path.join(out_dir, name + ".md")
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1, default=show)
        with open(rep["report_file"], "w", encoding="utf-8") as f:
            f.write(report_text(rep))
    except OSError as e:
        if dry_run:
            raise
        # the update itself is installed; only its report could not be written
        rep["notes"].append(f"the report file could not be written: {e}")
        rep["report_file"] = None
        say("تنبيه: تم التحديث لكن تعذر حفظ ملف التقرير.")
    return rep


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=os.path.dirname(here), help="the app folder (default: the folder that holds the update folder)")
    ap.add_argument("--update", default=here, help="the unpacked update folder (default: where this file is)")
    ap.add_argument("--dry-run", action="store_true", help="merge in a copy and write the report; change nothing")
    args = ap.parse_args()
    try:
        rep = apply(args.app, args.update, dry_run=args.dry_run)
    except UpdateError as e:
        print("\n✗ لم يتم التحديث: " + str(e))
        return 1
    except Exception as e:     # anything unexpected: the database was not swapped
        print("\n✗ لم يتم التحديث بسبب خطأ غير متوقع. قاعدة البيانات لم تتغير. انسخ هذا السطر وأرسله:")
        print("   " + type(e).__name__ + ": " + str(e))
        return 1
    n = len(rep["conflicts"])
    print("\n✓ " + ("فحص تجريبي فقط، لم يتغير شيء." if rep["dry_run"] else f"تم التحديث إلى {rep['to_version']}. شغّل التطبيق من ملف التشغيل كالعادة."))
    print("  حساباتكم وإعداداتكم وفعالياتكم والمخزون كما هي.")
    if n or rep["kept_after_removal"]:
        print(f"  {n} حقلًا عدّلتموه واحتفظنا بقيمتكم مع أن التحديث فيه قيمة أخرى. أرسلوا هذا الملف إلى إبراهيم:")
    else:
        print("  تقرير التحديث:")
    print("  " + (rep["report_file"] or "(تعذر حفظ ملف التقرير)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
