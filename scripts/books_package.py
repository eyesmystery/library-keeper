"""Books-and-authors data package (Ibrahim 2026-10-11, track BKPKG).

All the knowledge we gathered about books and the people behind them, as ONE portable file
that does not depend on this library's stock: it can be read by other applications, and another
library running Manar can import it to get ready-made cards for the books it also has.

  python scripts/books_package.py export [--out FILE.zip] [--no-covers]   # replaces data/packages/manar-books-package.zip
  python scripts/books_package.py info PACKAGE.zip
  python scripts/books_package.py import PACKAGE.zip [--db PATH] [--apply]

The package (a .zip) holds:
  books_data.db   SQLite: books, book_profiles, attribute_evidence, book_relations, book_awards,
                  book_adaptations, authors, book_authors, author_awards, package_info
  csv/            the same tables as CSV (UTF-8) for Excel and other programs
  covers/         the cover pictures, named as books.cover_file says
  manifest.json   format, date, app version, row counts
  README_AR.md    what each table and field means (Arabic)

NOT in the package — this library's own data: price, quantity, shelf location, Rewaa barcode/SKU,
sync dates, visitors' choices and ratings, notes, accounts, logs, events and sections.
book_embeddings are left out too (derived; rebuild with scripts/build_embeddings.py).

Import (another library's database, already filled from its own Rewaa file):
- only package books whose identity was settled (research_status = matched) are used;
- a book matches by ISBN (its ISBN or an ISBN-shaped barcode), else by title + author,
  else by title + publisher (the matching rule Ibrahim approved for research); a match must be
  unique, ambiguous ones are only reported;
- nothing the library already has is overwritten: empty fields are filled, a card is copied
  only when the book's card is still `pending`, evidence/prizes/adaptations/people links only
  when the book has none; the library's title, price, stock and shelf are never touched;
- every package author is brought in (also well-known authors with no book there): a person
  already present (same name or alias) is reused and only its empty fields are filled;
- dry run by default; --apply backs up first, writes in one transaction and records the run
  in audit_log.
"""
import argparse
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from rewaa_import import norm_title, norm_code, is_isbn  # noqa: E402
from authors import key as name_key, UNKNOWN  # noqa: E402

FORMAT = "manar-books-package"
FORMAT_VERSION = 1
DB = os.path.join(ROOT, "data", "library.db")
COVERS = os.path.join(ROOT, "data", "covers")
OUT_DIR = os.path.join(ROOT, "data", "packages")

# columns of `books` that describe this library's copy, not the book
LIBRARY_ONLY = ["price", "quantity", "shelf_location", "active", "barcode", "rewaa_sku", "source",
                "last_synced_at", "cover_url"]
BOOK_TABLES = ["book_profiles", "attribute_evidence", "book_relations", "book_awards", "book_adaptations"]
PEOPLE_TABLES = ["authors", "book_authors", "author_awards"]
CSV_TABLES = ["books", "book_profiles", "authors", "book_authors", "attribute_evidence", "book_relations",
              "book_awards", "author_awards", "book_adaptations"]
UNKNOWN_KEYS = {name_key(u) for u in UNKNOWN}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _has(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _cols(con, table):
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]


def _ddl(con, table, drop=(), extra=()):
    info = [r for r in con.execute(f'PRAGMA table_info("{table}")') if r[1] not in drop]
    pks = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
    parts = [f'"{r[1]}" {r[2] or ""}'.strip() for r in info] + list(extra)
    if pks:
        parts.append("PRIMARY KEY (" + ", ".join(f'"{p}"' for p in pks) + ")")
    return f'CREATE TABLE "{table}" (' + ", ".join(parts) + ")", [r[1] for r in info]


def _copy_table(src, dst, table, drop=()):
    ddl, cols = _ddl(src, table, drop)
    dst.execute(ddl)
    q = ", ".join(f'"{c}"' for c in cols)
    rows = src.execute(f'SELECT {q} FROM "{table}"').fetchall()
    dst.executemany(f'INSERT INTO "{table}" ({q}) VALUES ({", ".join("?" * len(cols))})', rows)
    return len(rows)


def _cover_name(url):
    if not url or not str(url).startswith("/covers/"):
        return None
    name = str(url).split("/covers/", 1)[1].split("?", 1)[0]
    return name if name and "/" not in name and os.path.isfile(os.path.join(COVERS, name)) else None


README_AR = """# حزمة بيانات الكتب والمؤلفين — منار

حزمة مستقلة فيها كل ما جمعناه عن الكتب والأشخاص المرتبطين بها، منفصلة عن مخزون أي مكتبة.
تُفتح بأي برنامج يقرأ SQLite أو CSV (Excel مثلًا)، ويستوردها تطبيق منار في مكتبة أخرى.

## الملفات
- `books_data.db`: قاعدة SQLite فيها كل الجداول أدناه.
- `csv/`: الجداول نفسها كملفات CSV (UTF-8).
- `covers/`: صور الأغلفة؛ اسم ملف كل غلاف في عمود `books.cover_file`.
- `manifest.json`: تاريخ الحزمة ونسخة التطبيق وعدد الصفوف.

## الجداول
- `books`: بيانات الكتاب: العنوان، المؤلف، المترجم، الناشر، السنة، الصفحات، ISBN، اللغة واللغة الأصلية، النوع والأنواع الفرعية، الموضوعات، السلسلة، النبذة ومصدرها، الجملة التشويقية (hook)، العمر المناسب، وحالة التحقق من هوية الكتاب (`research_status`: matched = هويته مؤكدة).
  - `barcode_isbn`: باركود المكتبة إذا كان رقم ISBN صحيحًا (يساعد في المطابقة).
  - `cover_file`: مسار الغلاف داخل الحزمة.
- `book_profiles`: بطاقة القراءة: الشكل، الإيقاع، صعوبة اللغة، الطول المحسوس، نبرة النهاية، الأمزجة، الثيمات، المحتوى الحساس، مستوى القارئ…
- `attribute_evidence`: دليل كل صفة: القيمة، الثقة، ملخص الدليل، روابط المصادر، عدد المراجعات، وهل اختلف القرّاء فيها.
- `book_relations`: علاقات بين الكتب: مشابه، نفس المؤلف، الجزء التالي/السابق، بديل أقصر/أخف/أسهل.
- `book_awards`: جوائز الكتب مع المصدر.
- `book_adaptations`: الأفلام والمسلسلات المقتبسة من الكتب.
- `authors`: قاعدة الأشخاص (مؤلفون ومترجمون ورسامون ومحررون): الاسم العربي واللاتيني، الأسماء البديلة، البلد، أشهر الأعمال، الأسلوب المعتاد، الجمهور، المصادر.
- `book_authors`: ربط الكتاب بالأشخاص مع الدور (author / translator / illustrator / editor).
- `author_awards`: جوائز المؤلفين مع المصدر.
- `package_info`: معلومات الحزمة.

الحقول التي تحمل قوائم (مثل moods, themes, aliases, source_urls) محفوظة كنص JSON.
الأرقام التعريفية (`book_id`, `author_id`) خاصة بالحزمة وتربط الجداول ببعضها؛ لا تمثل رقم الكتاب في أي مكتبة.

## ما لا تحتويه الحزمة
بيانات المكتبة نفسها: السعر، الكمية، موقع الرف، باركود ورمز رواء، اختيارات الزوار وتقييماتهم، الملاحظات، الحسابات، والسجلات.
"""


def export(out=None, covers=True):
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    # one fixed name: each export replaces the previous package (Ibrahim 2026-10-11, monthly refresh);
    # the package's own date is in manifest.json / package_info
    out = out or os.path.join(OUT_DIR, "manar-books-package.zip")
    tmp = tempfile.mkdtemp(prefix="books_pkg_")
    snap = os.path.join(tmp, "snap.db")
    live = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    s = sqlite3.connect(snap)
    live.backup(s)  # consistent copy, safe while the server runs
    live.close()
    pkg_path = os.path.join(tmp, "books_data.db")
    p = sqlite3.connect(pkg_path)
    counts = {}

    ddl, cols = _ddl(s, "books", drop=LIBRARY_ONLY, extra=['"barcode_isbn" TEXT', '"cover_file" TEXT'])
    p.execute(ddl)
    all_cols = _cols(s, "books")
    cover_files = {}
    n = 0
    for row in s.execute(f'SELECT {", ".join(all_cols)} FROM books ORDER BY book_id'):
        r = dict(zip(all_cols, row))
        code = norm_code(r.get("barcode"))
        cov = _cover_name(r.get("cover_url")) if covers else None
        if cov:
            cover_files[f"covers/{cov}"] = os.path.join(COVERS, cov)
        p.execute(f'INSERT INTO books ({", ".join(cols)}, barcode_isbn, cover_file) VALUES '
                  f'({", ".join("?" * (len(cols) + 2))})',
                  [r[c] for c in cols] + [code if is_isbn(code) else None, f"covers/{cov}" if cov else None])
        n += 1
    counts["books"] = n
    for table in BOOK_TABLES + PEOPLE_TABLES:
        if _has(s, table):
            counts[table] = _copy_table(s, p, table)
    counts["covers"] = len(cover_files)
    version = open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
    info = {"format": FORMAT, "format_version": FORMAT_VERSION, "created_at": _now(),
            "source_app": "منار (Manar)", "source_library": "ثلاث (Thalath)", "app_version": version,
            "counts": counts, "includes_covers": bool(covers)}
    p.execute("CREATE TABLE package_info (key TEXT PRIMARY KEY, value TEXT)")
    p.executemany("INSERT INTO package_info VALUES (?, ?)",
                  [(k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v) for k, v in info.items()])
    p.commit()
    ok = p.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        raise SystemExit(f"package database fails integrity_check: {ok}")
    p.execute("VACUUM")
    csv_files = {}
    for table in CSV_TABLES:
        if not _has(p, table):
            continue
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(_cols(p, table))
        w.writerows(p.execute(f'SELECT * FROM "{table}"'))
        csv_files[f"csv/{table}.csv"] = "﻿" + buf.getvalue()
    p.close()
    s.close()

    part = out + ".part"
    with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(info, ensure_ascii=False, indent=2))
        z.writestr("README_AR.md", README_AR)
        z.write(pkg_path, "books_data.db")
        for name, text in csv_files.items():
            z.writestr(name, text)
        for arc, path in sorted(cover_files.items()):
            z.write(path, arc, compress_type=zipfile.ZIP_STORED)  # jpg/png are already compressed
    os.replace(part, out)
    for f in (snap, pkg_path):
        os.remove(f)
    os.rmdir(tmp)
    problems = check(out, expect=counts)
    size = os.path.getsize(out) / 1e6
    print(f"wrote {os.path.relpath(out, ROOT)} ({size:.0f} MB)")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    if problems:
        print("PROBLEMS:\n  " + "\n  ".join(problems))
        return 1
    print("check: package reopens, integrity ok, row counts and covers match")
    return 0


def _open_pkg(path):
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    if "books_data.db" not in names or "manifest.json" not in names:
        raise SystemExit("not a books package (books_data.db / manifest.json missing)")
    tmp = tempfile.mkdtemp(prefix="books_pkg_")
    z.extract("books_data.db", tmp)
    con = sqlite3.connect(os.path.join(tmp, "books_data.db"))
    con.row_factory = sqlite3.Row
    return z, con, tmp, json.loads(z.read("manifest.json"))


def _close_pkg(z, con, tmp):
    con.close()
    z.close()
    os.remove(os.path.join(tmp, "books_data.db"))
    os.rmdir(tmp)


def check(path, expect=None):
    z, con, tmp, man = _open_pkg(path)
    problems = []
    try:
        if man.get("format") != FORMAT:
            problems.append(f"format is {man.get('format')!r}")
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            problems.append(f"integrity_check: {ok}")
        counts = expect or man.get("counts", {})
        for table, n in counts.items():
            if table == "covers":
                continue
            got = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if got != n:
                problems.append(f"{table}: {got} rows, expected {n}")
        names = set(z.namelist())
        listed = [r[0] for r in con.execute("SELECT cover_file FROM books WHERE cover_file IS NOT NULL")]
        missing = [c for c in listed if c not in names]
        if missing:
            problems.append(f"{len(missing)} covers listed but not in the zip (e.g. {missing[0]})")
        if "covers" in counts and len(set(listed)) != counts["covers"]:
            problems.append(f"covers: {len(set(listed))} listed, expected {counts['covers']}")
    finally:
        _close_pkg(z, con, tmp)
    return problems


def info(path):
    z, con, tmp, man = _open_pkg(path)
    _close_pkg(z, con, tmp)
    print(json.dumps(man, ensure_ascii=False, indent=2))
    problems = check(path)
    print("check: ok" if not problems else "PROBLEMS:\n  " + "\n  ".join(problems))
    return 1 if problems else 0


# ---------------------------------------------------------------- import

def _empty(col, v):
    if v is None or (isinstance(v, str) and v.strip() in ("", "[]", "{}")):
        return True
    return col in ("author", "translator") and name_key(v) in UNKNOWN_KEYS


def _match(pkg, target):
    """-> ({target_book_id: (pkg_book_id, how)}, ambiguous list, unmatched list)"""
    by_code, by_ta, by_tp = {}, {}, {}
    for b in pkg.execute("SELECT * FROM books WHERE research_status = 'matched'"):
        for c in {norm_code(b["isbn"]), norm_code(b["barcode_isbn"])}:
            if is_isbn(c):
                by_code.setdefault(c, set()).add(b["book_id"])
        t = norm_title(b["title"])
        if not t:
            continue
        if not _empty("author", b["author"]):
            by_ta.setdefault((t, name_key(b["author"])), set()).add(b["book_id"])
        if b["publisher"]:
            by_tp.setdefault((t, norm_title(b["publisher"])), set()).add(b["book_id"])
    titles = {r[0]: norm_title(r[1]) for r in pkg.execute("SELECT book_id, title FROM books")}
    pubs = {r[0]: norm_title(r[1]) for r in pkg.execute("SELECT book_id, publisher FROM books")}
    matched, ambiguous, unmatched = {}, [], []
    tcols = _cols(target, "books")
    for b in target.execute("SELECT * FROM books"):
        b = dict(b)
        t = norm_title(b["title"])
        codes = {norm_code(b.get("isbn")), norm_code(b.get("barcode"))}
        cand = set().union(*[by_code.get(c, set()) for c in codes if is_isbn(c)])
        how = "isbn"
        if len(cand) > 1:
            cand = {i for i in cand if titles.get(i) == t}
            how = "isbn+title"
        if not cand and not _empty("author", b.get("author")):
            cand, how = by_ta.get((t, name_key(b["author"])), set()), "title+author"
        if len(cand) > 1 and b.get("publisher"):  # several editions: the library's publisher decides
            cand = {i for i in cand if pubs.get(i) == norm_title(b["publisher"])}
            how += "+publisher"
        if not cand and b.get("publisher"):
            cand, how = by_tp.get((t, norm_title(b["publisher"])), set()), "title+publisher"
        row = {"book_id": b["book_id"], "title": b["title"]}
        if len(cand) == 1:
            matched[b["book_id"]] = (next(iter(cand)), how)
        elif len(cand) > 1:
            ambiguous.append({**row, "candidates": sorted(cand), "by": how})
        else:
            unmatched.append(row)
    assert "book_id" in tcols
    return matched, ambiguous, unmatched


def _insert(con, table, row, cols):
    use = [c for c in cols if c in row]
    con.execute(f'INSERT INTO "{table}" ({", ".join(use)}) VALUES ({", ".join("?" * len(use))})',
                [row[c] for c in use])


def import_package(path, db, apply=False):
    z, pkg, tmp, man = _open_pkg(path)
    target = sqlite3.connect(db)
    target.row_factory = sqlite3.Row
    covers_dir = os.path.join(os.path.dirname(os.path.abspath(db)), "covers")
    st = {"books_filled": 0, "fields_filled": {}, "cards_copied": 0, "evidence_rows": 0,
          "book_awards": 0, "adaptations": 0, "relations": 0, "people_links": 0, "covers": 0,
          "authors_new": 0, "authors_reused": 0, "authors_filled": 0, "author_awards": 0}
    try:
        matched, ambiguous, unmatched = _match(pkg, target)
        how = {}
        for _, (_, h) in matched.items():
            how[h] = how.get(h, 0) + 1
        if apply:
            _backup(db)
            target.execute("BEGIN")
        now = _now()

        # people first, so book links can point at them
        amap = {}
        if _has(pkg, "authors") and _has(target, "authors"):
            tcols = _cols(target, "authors")
            index = {}
            for a in target.execute("SELECT author_id, name_ar, aliases FROM authors"):
                names = [a["name_ar"]]
                try:
                    names += json.loads(a["aliases"] or "[]")
                except ValueError:
                    pass
                for nm in names:
                    index.setdefault(name_key(nm), a["author_id"])
            for a in pkg.execute("SELECT * FROM authors"):
                a = dict(a)
                tid = index.get(name_key(a["name_ar"]))
                if tid is None:
                    st["authors_new"] += 1
                    if apply:
                        row = {k: v for k, v in a.items() if k != "author_id"}
                        _insert(target, "authors", row, tcols)
                        tid = target.execute("SELECT last_insert_rowid()").fetchone()[0]
                    else:
                        tid = -a["author_id"]
                    index[name_key(a["name_ar"])] = tid
                    new = True
                else:
                    st["authors_reused"] += 1
                    new = False
                    cur = dict(target.execute("SELECT * FROM authors WHERE author_id = ?", (tid,)).fetchone())
                    if cur.get("research_status") in (None, "pending"):
                        sets = {c: a[c] for c in tcols if c in a and c not in ("author_id", "name_ar", "created_at")
                                and _empty(c, cur.get(c)) and not _empty(c, a[c])}
                        if sets and a.get("research_status") not in (None, "pending") and "research_status" in tcols:
                            sets["research_status"] = a["research_status"]
                        if sets:
                            st["authors_filled"] += 1
                            if apply:
                                target.execute(f"UPDATE authors SET {', '.join(f'{c} = ?' for c in sets)} "
                                               f"WHERE author_id = ?", (*sets.values(), tid))
                amap[a["author_id"]] = tid
                if _has(pkg, "author_awards") and _has(target, "author_awards"):
                    has = (not new) and target.execute("SELECT 1 FROM author_awards WHERE author_id = ?",
                                                       (tid,)).fetchone()
                    if not has:
                        acols = _cols(target, "author_awards")
                        for r in pkg.execute("SELECT * FROM author_awards WHERE author_id = ?", (a["author_id"],)):
                            st["author_awards"] += 1
                            if apply:
                                row = {k: v for k, v in dict(r).items() if k != "row_id"}
                                row["author_id"] = tid
                                _insert(target, "author_awards", row, acols)

        bcols = _cols(target, "books")
        fill_cols = [c for c in _cols(pkg, "books") if c in bcols and c not in
                     ("book_id", "title", "research_candidates", "research_note", "research_note_ar", *LIBRARY_ONLY)]
        for tid, (pid, _) in matched.items():
            pb = dict(pkg.execute("SELECT * FROM books WHERE book_id = ?", (pid,)).fetchone())
            tb = dict(target.execute("SELECT * FROM books WHERE book_id = ?", (tid,)).fetchone())
            sets = {c: pb[c] for c in fill_cols if _empty(c, tb.get(c)) and not _empty(c, pb[c])}
            if pb.get("cover_file") and _empty("cover_url", tb.get("cover_url")) and pb["cover_file"] in z.namelist():
                st["covers"] += 1
                name = f"{tid}{os.path.splitext(pb['cover_file'])[1] or '.jpg'}"
                if apply:
                    os.makedirs(covers_dir, exist_ok=True)
                    with open(os.path.join(covers_dir, name), "wb") as f:
                        f.write(z.read(pb["cover_file"]))
                sets["cover_url"] = f"/covers/{name}?v={int(datetime.now().timestamp())}"
            if sets:
                st["books_filled"] += 1
                for c in sets:
                    st["fields_filled"][c] = st["fields_filled"].get(c, 0) + 1
                if apply:
                    target.execute(f"UPDATE books SET {', '.join(f'{c} = ?' for c in sets)} WHERE book_id = ?",
                                   (*sets.values(), tid))
            # reading card
            if _has(target, "book_profiles"):
                pp = pkg.execute("SELECT * FROM book_profiles WHERE book_id = ?", (pid,)).fetchone()
                tp = target.execute("SELECT * FROM book_profiles WHERE book_id = ?", (tid,)).fetchone()
                if pp and (tp is None or tp["overall_status"] in (None, "pending")):
                    st["cards_copied"] += 1
                    if apply:
                        pcols = _cols(target, "book_profiles")
                        row = {k: v for k, v in dict(pp).items() if k != "profile_id"}
                        row["book_id"] = tid
                        if tp is None:
                            _insert(target, "book_profiles", row, pcols)
                        else:
                            use = [c for c in pcols if c in row and c not in ("profile_id", "book_id")]
                            target.execute(f"UPDATE book_profiles SET {', '.join(f'{c} = ?' for c in use)} "
                                           f"WHERE book_id = ?", (*[row[c] for c in use], tid))
            # rows that belong to the book: copied only when the book has none
            for table, idcol, stat in (("attribute_evidence", "evidence_id", "evidence_rows"),
                                       ("book_awards", "row_id", "book_awards"),
                                       ("book_adaptations", "row_id", "adaptations")):
                if not (_has(pkg, table) and _has(target, table)):
                    continue
                if target.execute(f"SELECT 1 FROM {table} WHERE book_id = ?", (tid,)).fetchone():
                    continue
                tc = _cols(target, table)
                for r in pkg.execute(f"SELECT * FROM {table} WHERE book_id = ?", (pid,)):
                    st[stat] += 1
                    if apply:
                        row = {k: v for k, v in dict(r).items() if k != idcol}
                        row["book_id"] = tid
                        _insert(target, table, row, tc)
            # people links
            if amap and _has(target, "book_authors") and not target.execute(
                    "SELECT 1 FROM book_authors WHERE book_id = ?", (tid,)).fetchone():
                for r in pkg.execute("SELECT * FROM book_authors WHERE book_id = ?", (pid,)):
                    st["people_links"] += 1
                    if apply:
                        target.execute("INSERT OR IGNORE INTO book_authors (book_id, author_id, role, position, source) "
                                       "VALUES (?, ?, ?, ?, ?)",
                                       (tid, amap[r["author_id"]], r["role"], r["position"], r["source"]))
        # relations between two books this library has
        if _has(pkg, "book_relations") and _has(target, "book_relations"):
            back = {}
            for tid, (pid, _) in matched.items():
                back.setdefault(pid, tid)
            rc = _cols(target, "book_relations")
            for r in pkg.execute("SELECT * FROM book_relations"):
                s, t = back.get(r["source_book_id"]), back.get(r["target_book_id"])
                if s is None or t is None or s == t:
                    continue
                if target.execute("SELECT 1 FROM book_relations WHERE source_book_id = ? AND target_book_id = ? "
                                  "AND relation_type = ?", (s, t, r["relation_type"])).fetchone():
                    continue
                st["relations"] += 1
                if apply:
                    row = {k: v for k, v in dict(r).items() if k != "relation_id"}
                    row.update(source_book_id=s, target_book_id=t)
                    _insert(target, "book_relations", row, rc)

        report = {"package": os.path.basename(path), "package_created_at": man.get("created_at"),
                  "target_db": os.path.abspath(db), "dry_run": not apply, "at": now,
                  "target_books": target.execute("SELECT COUNT(*) FROM books").fetchone()[0],
                  "matched": len(matched), "matched_by": how, "ambiguous": len(ambiguous),
                  "unmatched": len(unmatched), **st}
        if apply:
            if _has(target, "audit_log"):
                target.execute("INSERT INTO audit_log (at, username, action, target, details) VALUES (?, ?, ?, ?, ?)",
                               (now, "books_package", "books_package_import", os.path.basename(path),
                                json.dumps(report, ensure_ascii=False)))
            target.commit()
            ok = target.execute("PRAGMA integrity_check").fetchone()[0]
            report["integrity"] = ok
    except Exception:
        if apply:
            target.rollback()
        raise
    finally:
        target.close()
        _close_pkg(z, pkg, tmp)

    os.makedirs(OUT_DIR, exist_ok=True)
    rpath = os.path.join(OUT_DIR, f"import-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    with open(rpath, "w", encoding="utf-8") as f:
        json.dump({**report, "ambiguous_books": ambiguous, "unmatched_books": unmatched}, f,
                  ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"full list of ambiguous/unmatched books: {os.path.relpath(rpath, ROOT)}")
    if not apply:
        print("dry run — nothing written. Add --apply to import.")
    return 0


def _backup(db):
    if os.path.abspath(db) == os.path.abspath(DB):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "backup_db.py"),
                            "--reason", "before books package import"])
        if r.returncode != 0:
            raise SystemExit("backup failed; nothing imported")
        return
    dest = f"{db}.before-books-package-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    src = sqlite3.connect(db)
    out = sqlite3.connect(dest)
    src.backup(out)
    out.close()
    src.close()
    print(f"backup: {dest}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out")
    e.add_argument("--no-covers", action="store_true")
    i = sub.add_parser("info")
    i.add_argument("package")
    m = sub.add_parser("import")
    m.add_argument("package")
    m.add_argument("--db", default=DB)
    m.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "export":
        return export(a.out, covers=not a.no_covers)
    if a.cmd == "info":
        return info(a.package)
    return import_package(a.package, a.db, apply=a.apply)


if __name__ == "__main__":
    sys.exit(main())
