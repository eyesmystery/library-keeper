# -*- coding: utf-8 -*-
"""[QADKWFIX] Standing score of the keyword reader (the chat's understanding with the model down) against the model's own readings
saved in the QAD rounds (research/chat_tests_*/results.json, turns[].debug.prefs). Ibrahim 2026-11-14 on Q-QADKW-1, item 4.

Same method as research/fallback_study_2026-11-13/report.md (scripts/measure.py there): every QAD turn the model read (understanding
"ai", debug.prefs a dict) is read again by the keyword reader with the visitor's earlier messages of the case, and compared field by
field with the model's reading:
  ok      the model read the field and the keyword reader read it too (at least one value in common)
  missed  the model read it, the keyword reader did not
  wrong   both read it, nothing in common
  extra   the keyword reader read a field the model left empty
A turn is «complete» when every field the model read is ok and nothing is extra, «partly» when at least one is ok, «nothing» when none
is. The headline («shared fields», the report's 39%) uses the twelve fields both readers had on 2026-11-13: genres, audience, length,
moods_include, moods_exclude, weight_include, use_cases, avoid_content, original_languages, translated, awards, adaptation, plus the
author (app.fallback_author on the message; from 2026-11-14 app.fallback_author_in, which also follows «شي قصير له» back to the
author named before). The keyword reading is app.fallback_understand (the model-free answer's; app.local_understand on a tree that
has none). «all fields» adds the subgenre (a field the keyword reader gained on 2026-11-14; kinds
compared by family: «بوليسية» = «جريمة» = «غموض») and whether the subject's words survive as search words (topic).
The model is the reference: it is sometimes wrong, but it is what the chat works with.

Read-only: the app is imported with a private copy of data/library.db (refreshed from the live file when it is newer, opened
read-only), and LIBRARY_KEEPER_LLM=off. Nothing in the project is written unless --rows is given.

  backend/venv/bin/python scripts/kw_score.py                                    # every round, one line
  backend/venv/bin/python scripts/kw_score.py --round research/chat_tests_2026-11-14   # one round (the daily QAD report)
  backend/venv/bin/python scripts/kw_score.py --fields                           # + the table field by field and by kind of turn
  options: --code DIR (import the backend of another tree, e.g. a `git archive` copy), --rows FILE (per-turn rows as JSON),
           --db FILE (the database copy to use), --json (the summary as one JSON object instead of the line)
"""
import argparse, collections, glob, json, os, sqlite3, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED = ["genres", "audience", "length", "moods_include", "moods_exclude", "weight_include", "use_cases",
          "avoid_content", "original_languages", "translated", "awards", "adaptation"]
ALL = SHARED + ["authors", "subgenres", "topic"]


def _db_copy(target):
    src_path = os.path.join(ROOT, "data", "library.db")
    if os.path.exists(target) and os.path.getmtime(target) >= os.path.getmtime(src_path):
        return target
    os.makedirs(os.path.dirname(target), exist_ok=True)
    src = sqlite3.connect("file:" + src_path + "?mode=ro", uri=True)
    dst = sqlite3.connect(target + ".part")
    src.backup(dst)
    dst.close()
    src.close()
    os.replace(target + ".part", target)
    return target


def _load_app(code, db):
    os.environ["LIBRARY_KEEPER_LLM"] = "off"
    sys.path.insert(0, os.path.join(code, "backend"))
    os.chdir(code)
    import models
    models.DB_PATH = db
    import app
    import keyword_prefs
    return app, keyword_prefs


_W = {}


def _worker_init(code, db):
    app, K = _load_app(code, db)
    _W.update(app=app, K=K, books={b["book_id"]: dict(b) for b in app.list_books(only_active=False)})


def _worker_run(chunk):
    return [score_turn(x, _W["app"], _W["K"], _W["books"]) for x in chunk]


def turns_of(round_dirs):
    """Every turn the model read: (round, case n, k, lang, message, earlier visitor messages, the model's prefs)."""
    out, skipped = [], {}
    for d in round_dirs:
        rnd = os.path.basename(os.path.normpath(d))[-10:]
        f = os.path.join(d, "results.json")
        try:
            res = json.load(open(f, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - a round without results, or one being written
            skipped[rnd] = type(e).__name__
            continue
        for c in res if isinstance(res, list) else []:
            hist = []
            for k, t in enumerate(c.get("turns") or [], 1):
                q = t.get("q") or ""
                prefs = (t.get("debug") or {}).get("prefs")
                if t.get("understanding") == "ai" and isinstance(prefs, dict):
                    out.append({"round": rnd, "n": c.get("n"), "k": k, "lang": c.get("lang", "ar"), "q": q,
                                "history": list(hist), "prefs": prefs})
                hist.append(q)
    return out, skipped


def _set(v):
    if v in (None, "", False):
        return set()
    if isinstance(v, (list, tuple, set)):
        return {str(x) for x in v if x not in (None, "")}
    return {str(v)}


def _aud(v):
    return v if v in ("children", "young_adult") else None


def compare(ai, kw, field, K=None):
    a, k = _set(ai.get(field)), _set(kw.get(field))
    if field == "audience":
        a, k = _set(_aud(ai.get("audience"))), _set(_aud(kw.get("audience")))
    if field == "genres" and len(a) > 1:   # the model often adds «رواية» to a precise wish
        a = a - {"رواية"} or a
    if field == "subgenres":
        fam = getattr(K, "subgenre_families", None)
        if fam is None or "subgenres" not in kw:
            return "missed" if a else None      # a reader without the field (before 2026-11-14)
        a = set().union(*[fam(x) for x in a]) if a else set()
        k = set().union(*[fam(x) for x in k]) if k else set()
        if not a and not _set(ai.get("subgenres")):
            return "extra" if k else None
        if not a:
            return None                          # a kind the family table does not name: not counted
    if not a and not k:
        return None
    if a and not k:
        return "missed"
    if k and not a:
        return "extra"
    return "ok" if a & k else "wrong"


def score_turn(x, app, K, all_books):
    msgs = x["history"] + [x["q"]]
    reader = getattr(app, "fallback_understand", None) or app.local_understand
    try:
        kw = reader(msgs)
    except Exception as e:  # noqa: BLE001
        kw = {}
        print("ERR", x["round"], x["n"], x["k"], repr(e), file=sys.stderr)
    p = x["prefs"]
    r = {f: compare(p, kw, f, K) for f in SHARED + ["subgenres"]}
    want = [K.normalize(a) for a in (p.get("authors") or []) if a]
    r["authors"] = None
    if want:
        # the author as the model-free answer reads it: the latest message, or an earlier one it points back to («شي قصير له»)
        fa = (app.fallback_author_in(x["history"] + [x["q"]], all_books) if hasattr(app, "fallback_author_in")
              else app.fallback_author(x["q"], all_books))
        if not fa:
            r["authors"] = "missed"
        else:
            got = K.normalize(fa[0])
            r["authors"] = "ok" if any(w in got or got in w or set(w.split()) & set(got.split()) for w in want) else "wrong"
    topics = [t for t in (p.get("unsupported_requests") or []) if t]
    r["topic"] = None
    if topics:
        tk = {K.strip_al(w) for w in kw.get("title_keywords") or []}
        r["topic"] = "ok" if any(K.strip_al(w) in tk for t in topics for w in K.normalize(t).split() if len(w) > 2) else "missed"
    return dict(round=x["round"], n=x["n"], k=x["k"], lang=x["lang"], q=x["q"], r=r,
                kw={f: kw.get(f) for f in SHARED + ["subgenres", "title_keywords"]})


def verdict(rows, fields):
    full = part = none_ = n = 0
    for row in rows:
        asked = [f for f in fields if row["r"].get(f) not in (None, "extra")]
        if not asked:
            continue
        n += 1
        got = sum(row["r"][f] == "ok" for f in asked)
        extra = any(row["r"].get(f) == "extra" for f in fields)
        full += got == len(asked) and not extra
        part += got > 0
        none_ += got == 0
    return n, full, part, none_


def pct(a, b):
    return round(100 * a / b) if b else 0


def summary(rows):
    n, full, part, none_ = verdict(rows, SHARED + ["authors"])
    n2, full2, _, _ = verdict(rows, ALL)
    en = [r for r in rows if r["lang"] == "en"]
    ne, fe, _, _ = verdict(en, SHARED + ["authors"])
    fu = [r for r in rows if r["k"] > 1]
    nf, ff, _, _ = verdict(fu, SHARED + ["authors"])
    fields = {}
    for f in ALL:
        c = collections.Counter(r["r"][f] for r in rows if r["r"].get(f))
        asked = c["ok"] + c["missed"] + c["wrong"]
        fields[f] = {"asked": asked, "ok": c["ok"], "missed": c["missed"], "wrong": c["wrong"], "extra": c["extra"],
                     "pct": pct(c["ok"], asked)}
    return {"turns": len(rows), "with_wish": n, "complete": full, "complete_pct": pct(full, n), "partly_pct": pct(part, n),
            "nothing_pct": pct(none_, n), "all_fields_complete_pct": pct(full2, n2), "english_complete_pct": pct(fe, ne),
            "english_turns": ne, "followups_complete_pct": pct(ff, nf), "followup_turns": nf, "fields": fields}


def line(label, s):
    f = s["fields"]
    return (f"KW score {label}: complete {s['complete_pct']}% ({s['complete']}/{s['with_wish']} turns the model read; shared fields), "
            f"partly {s['partly_pct']}%, nothing {s['nothing_pct']}%; all fields {s['all_fields_complete_pct']}%; "
            f"English {s['english_complete_pct']}% ({s['english_turns']}); follow-ups {s['followups_complete_pct']}% ({s['followup_turns']}); "
            f"subgenres {f['subgenres']['pct']}%, authors {f['authors']['pct']}%, length {f['length']['pct']}%, "
            f"moods {f['moods_include']['pct']}%")


def table(rows, label):
    s = summary(rows)
    print(f"== {label}: turns {s['turns']}, with a wish {s['with_wish']}: complete {s['complete_pct']}%, partly {s['partly_pct']}%, "
          f"nothing {s['nothing_pct']}%; all fields complete {s['all_fields_complete_pct']}%")
    for f in ALL:
        v = s["fields"][f]
        if v["asked"] or v["extra"]:
            print(f"   {f:20s} asked {v['asked']:5d}  ok {v['ok']:5d} ({v['pct']:3d}%)  missed {v['missed']:5d}  "
                  f"wrong {v['wrong']:4d}  extra {v['extra']:4d}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--round", action="append", help="a round folder (research/chat_tests_YYYY-MM-DD); repeatable; default: all")
    ap.add_argument("--code", default=ROOT, help="the tree whose backend is measured (default: this one)")
    ap.add_argument("--db", default="/tmp/lk_kw_score/library.db", help="private database copy (made / refreshed read-only)")
    ap.add_argument("--rows", help="write the per-turn rows to this JSON file")
    ap.add_argument("--fields", action="store_true", help="also print the table field by field")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    ap.add_argument("--workers", type=int, default=4, help="processes for the whole set (default 4; 1 = in this process)")
    a = ap.parse_args(argv)
    rounds = [os.path.abspath(os.path.join(ROOT, d) if not os.path.isabs(d) else d) for d in a.round] if a.round else \
        sorted(glob.glob(os.path.join(ROOT, "research", "chat_tests_*")))
    data, skipped = turns_of(rounds)
    code = os.path.abspath(a.code)
    db = _db_copy(a.db)
    if a.workers > 1 and len(data) > 200:
        from concurrent.futures import ProcessPoolExecutor
        chunks = [data[i::a.workers] for i in range(a.workers)]
        with ProcessPoolExecutor(a.workers, initializer=_worker_init, initargs=(code, db)) as ex:
            parts = list(ex.map(_worker_run, chunks))
        order = {(x["round"], x["n"], x["k"]): i for i, x in enumerate(data)}
        rows = sorted((r for part in parts for r in part), key=lambda r: order[(r["round"], r["n"], r["k"])])
    else:
        app, K = _load_app(code, db)
        all_books = {b["book_id"]: dict(b) for b in app.list_books(only_active=False)}
        rows = [score_turn(x, app, K, all_books) for x in data]
    label = (os.path.basename(os.path.normpath(rounds[0]))[-10:] if len(rounds) == 1
             else f"{len({r['round'] for r in rows})} rounds")
    s = summary(rows)
    if a.rows:
        json.dump(rows, open(a.rows, "w", encoding="utf-8"), ensure_ascii=False)
    if a.json:
        print(json.dumps(dict(s, label=label, skipped=skipped), ensure_ascii=False))
    else:
        print(line(label, s) + (f" [skipped: {', '.join(sorted(skipped))}]" if skipped else ""))
    if a.fields:
        table(rows, "all turns")
        table([r for r in rows if r["k"] == 1], "first turns")
        table([r for r in rows if r["k"] > 1], "follow-ups")
        table([r for r in rows if r["lang"] == "en"], "English page")
    return s


if __name__ == "__main__":
    main()
