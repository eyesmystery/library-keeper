"""Look authors up on Wikidata and add sourced facts to `authors` (docs/author-profile-proposal.md).

Run on the Mac (needs internet):
  python3 scripts/lookup_authors_wikidata.py fetch <db_path> <out_dir> [--status thin,needs_sources] [--limit N] [--delay 0.4]
  python3 scripts/lookup_authors_wikidata.py apply <db_path> <out_dir>/results.json [--apply]

fetch  searches Wikidata (Arabic and Latin names), keeps humans only, scores each candidate
       (exact name/alias match, writer-type occupation or description, a notable work matching one of
       our titles) and writes results.json: matched / ambiguous (candidates listed) / not_found.
       The database is only read.
apply  for matched authors only: adds Arabic/Latin names and aliases (never one another author uses),
       fills Latin name, origin (country of citizenship) and known works when empty, adds the Wikidata
       URL to source_urls and a note; needs_sources becomes thin. Style fields are not guessed from
       Wikidata. Without --apply: dry run on a copy. Report: <out_dir>/apply_report.json.
"""
import json, os, re, sqlite3, sys, tempfile, time, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))
import authors  # noqa: E402  (key, _load, _taken, find)

API = "https://www.wikidata.org/w/api.php"
UA = "LibraryKeeperAuthorLookup/1.0 (https://www.wikidata.org/wiki/Wikidata:Data_access; single-user catalogue lookup, 1 request/s) Python-urllib"
LANGS = "ar|en|fr|de|es|it|pt|ru|fa|tr"
NOT_PERSONS = {authors.key(x) for x in ("مجموعة مؤلفين", "مؤلف مجهول", "Various", "غير معروف")}
WRITER_OCC = {"Q36180", "Q6625963", "Q49757", "Q1930187", "Q201788", "Q4964182", "Q482980", "Q11774202",
              "Q333634", "Q1622272", "Q1650915", "Q15949613", "Q214917", "Q28389", "Q715301", "Q191633",
              "Q18814623", "Q1231865", "Q42973", "Q170790", "Q2306091", "Q1238570", "Q16287483", "Q188094"}
WRITER_WORDS = re.compile(r"writer|novelist|poet|author|historian|philosopher|journalist|essayist|scholar|"
                          r"translator|playwright|mangaka|comic|illustrat|academic|economist|sociologist|"
                          r"كاتب|كاتبة|روائي|روائية|شاعر|شاعرة|مؤرخ|فيلسوف|صحفي|أديب|مترجم|باحث|أكاديمي", re.I)


def _get(params, tries=6):
    """Wikidata API call; on 429/5xx waits (Retry-After when given) and retries."""
    import urllib.error
    url = API + "?" + urllib.parse.urlencode(dict(params, format="json"))
    try:
        import net
        ctx = net.ssl_context()
    except Exception:
        ctx = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
                data = json.loads(r.read().decode("utf-8"))
            if data.get("error", {}).get("code") == "maxlag":
                raise urllib.error.HTTPError(url, 503, "maxlag", {}, None)
            return data
        except urllib.error.HTTPError as e:
            if i == tries - 1 or e.code not in (429, 500, 502, 503, 504):
                raise
            wait = e.headers.get("Retry-After") if getattr(e, "headers", None) else None
            time.sleep(int(wait) if wait and str(wait).isdigit() else 10 * (i + 1))
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def _claims(ent, prop):
    out = []
    for c in ent.get("claims", {}).get(prop, []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(v, dict) and "id" in v:
            out.append(v["id"])
        elif isinstance(v, dict) and "time" in v:
            out.append(v["time"])
    return out


def _names(ent):
    names = [v["value"] for v in ent.get("labels", {}).values()]
    for vs in ent.get("aliases", {}).values():
        names += [v["value"] for v in vs]
    return names


def _label(ent, pref=("ar", "en")):
    for l in pref:
        if l in ent.get("labels", {}):
            return ent["labels"][l]["value"]
    return next((v["value"] for v in ent.get("labels", {}).values()), None)


def fetch(db, out_dir, statuses, limit, delay):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    rows = con.execute(f"SELECT * FROM authors WHERE research_status IN ({','.join('?' * len(statuses))}) ORDER BY author_id",
                       statuses).fetchall()
    titles = {}
    for r in con.execute("SELECT ba.author_id, b.title FROM book_authors ba JOIN books b USING(book_id)"):
        titles.setdefault(r[0], []).append(r[1])
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "results.json")
    done = {x["author_id"]: x for x in json.load(open(path, encoding="utf-8"))} if os.path.exists(path) else {}
    out = list(done.values())
    todo = [r for r in rows if r["author_id"] not in done and authors.key(r["name_ar"]) not in NOT_PERSONS][:limit or None]
    errors = 0
    for n, r in enumerate(todo, 1):
        try:
            out.append(_lookup_one(r, titles, delay))
        except Exception as e:  # one author failing never stops the run; it is retried on the next run
            errors += 1
            print(f"error for {r['name_ar']}: {e}", flush=True)
            time.sleep(30)
        if n % 10 == 0 or n == len(todo):
            json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"{n}/{len(todo)} matched={sum(1 for x in out if x['status']=='matched')} errors={errors}", flush=True)
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s = {k: sum(1 for x in out if x["status"] == k) for k in ("matched", "ambiguous", "not_found")}
    print(json.dumps(dict(s, errors=errors)))


def _lookup_one(r, titles, delay):
    if True:
        ours = [r["name_ar"], r["name_latin"]] + authors._load(r["aliases"])
        ours = [x for x in dict.fromkeys(ours) if x and authors.key(x)]
        keys = {authors.key(x) for x in ours}
        qids = []
        for name in ours[:3]:
            lang = "en" if authors._is_latin(name) else "ar"
            res = _get({"action": "wbsearchentities", "search": name, "language": lang, "uselang": lang, "type": "item", "limit": 7})
            qids += [s["id"] for s in res.get("search", []) if s["id"] not in qids]
            time.sleep(delay)
        rec = {"author_id": r["author_id"], "name": r["name_ar"], "status": "not_found", "candidates": []}
        if qids:
            ents = _get({"action": "wbgetentities", "ids": "|".join(qids[:20]), "props": "labels|aliases|descriptions|claims", "languages": LANGS}).get("entities", {})
            time.sleep(delay)
            our_titles = [authors.key(t) for t in titles.get(r["author_id"], [])]
            cands = []
            for qid, e in ents.items():
                if "Q5" not in _claims(e, "P31"):
                    continue  # humans only
                names = _names(e)
                exact = any(authors.key(x) in keys for x in names)
                desc = " ".join(d["value"] for d in e.get("descriptions", {}).values())
                writer = bool(set(_claims(e, "P106")) & WRITER_OCC) or bool(WRITER_WORDS.search(desc))
                score = (4 if exact else 0) + (2 if writer else 0)
                cands.append({"qid": qid, "label": _label(e), "label_en": _label(e, ("en",)), "description": _label({"labels": e.get("descriptions", {})}, ("ar", "en")),
                              "score": score, "exact": exact, "writer": writer, "_e": e})
            # a notable work matching one of our titles confirms the person
            works_q = {w for c in cands for w in _claims(c["_e"], "P800")}
            if works_q and our_titles:
                we = _get({"action": "wbgetentities", "ids": "|".join(list(works_q)[:40]), "props": "labels", "languages": LANGS}).get("entities", {})
                time.sleep(delay)
                for c in cands:
                    for w in _claims(c["_e"], "P800"):
                        wn = [authors.key(x) for x in _names(we.get(w, {}))]
                        if any(t and x and (t == x or t in x or x in t) for t in our_titles for x in wn):
                            c["score"] += 3; c["work_match"] = True; break
            cands.sort(key=lambda c: -c["score"])
            rec["candidates"] = [{k: v for k, v in c.items() if k != "_e"} for c in cands[:5]]
            top = cands[0] if cands else None
            second = cands[1]["score"] if len(cands) > 1 else -1
            if top and top["exact"] and top["writer"] and top["score"] > second:
                rec["status"] = "matched"
                rec.update(_details(top["_e"], top["qid"], delay))
            elif top and top["score"] >= 4:
                rec["status"] = "ambiguous"
        return rec


def _details(e, qid, delay=1.2):
    if True:
        if True:
                ref = set(_claims(e, "P27") + _claims(e, "P106") + _claims(e, "P136") + _claims(e, "P800")[:8])
                lab = _get({"action": "wbgetentities", "ids": "|".join(list(ref)[:50]), "props": "labels", "languages": "ar|en"}).get("entities", {}) if ref else {}
                time.sleep(delay)
                L = lambda q: _label(lab.get(q, {}))
                return dict(qid=qid, url=f"https://www.wikidata.org/wiki/{qid}",
                           labels={l: v["value"] for l, v in e.get("labels", {}).items()},
                           aliases={l: [v["value"] for v in vs] for l, vs in e.get("aliases", {}).items()},
                           countries=[L(q) for q in _claims(e, "P27") if L(q)],
                           occupations=[L(q) for q in _claims(e, "P106") if L(q)][:6],
                           genres=[L(q) for q in _claims(e, "P136") if L(q)][:6],
                           notable_works=[L(q) for q in _claims(e, "P800")[:8] if L(q)],
                           born=(_claims(e, "P569") or [None])[0])


def review(out_dir, picks, rejects, no_origin):
    """Claude's review of the automatic result: pick a candidate for an ambiguous author (fetching its
    details), reject a wrong match, or drop a doubtful country."""
    path = os.path.join(out_dir, "results.json")
    res = json.load(open(path, encoding="utf-8"))
    for rec in res:
        aid = rec["author_id"]
        if aid in picks:
            qid = picks[aid]
            e = _get({"action": "wbgetentities", "ids": qid, "props": "labels|aliases|descriptions|claims", "languages": LANGS})["entities"][qid]
            rec.update(_details(e, qid), status="matched", reviewed="picked")
        elif aid in rejects:
            rec.update(status="rejected", reviewed="wrong person")
        if aid in no_origin and rec.get("status") == "matched":
            rec["countries"], rec["reviewed"] = [], "country dropped (doubtful)"
    json.dump(res, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: sum(1 for x in res if x["status"] == k) for k in ("matched", "ambiguous", "not_found", "rejected")}))


# former states Wikidata lists as "country of citizenship" (الدولة العثمانية، مملكة بروسيا…) are not
# what a visitor means by the author's country; they are skipped (2026-10-01)
HISTORIC = re.compile(r"الدولة|السلطنة|(?<!ال)مملكة|المملكة المصرية|المملكة العراقية|لبريطانيا العظمى وأيرلندا|الإمبراطورية|الحماية|الراج|الجمهورية العربية المتحدة|الجمهورية السورية الثانية|"
                      r"سيسليثانيا|يوغسلافيا|Yugoslavia|الاتحاد السوفيتي|تشيكوسلوفاكيا|القاجارية|البهلوية|سلالة|Kingdom|Empire|الخديوية|المستعمرة")


MODERN = {"جمهورية مصر": "مصر", "السلطنة المصرية": "مصر", "المملكة المصرية": "مصر", "الخديوية المصرية": "مصر",
          "الجمهورية العربية المتحدة": "مصر", "المملكة العراقية": "العراق", "الدولة القاجارية": "إيران", "الدولة البهلوية": "إيران",
          "الإمبراطورية الروسية": "روسيا", "مملكة بروسيا": "ألمانيا", "الإمبراطورية الألمانية": "ألمانيا", "مملكة إيطاليا": "إيطاليا",
          "الحماية الفرنسية في تونس": "تونس", "الجمهورية السورية الثانية": "سوريا", "المستعمرة النيجيرية": "نيجيريا",
          "المملكة المتحدة لبريطانيا العظمى وأيرلندا": "المملكة المتحدة", "مملكة إنجلترا": "المملكة المتحدة", "دولة فلسطين": "فلسطين"}


def modern_countries(countries):
    """Former states -> today's country where clear; other former states dropped; duplicates removed."""
    out = []
    for c in countries:
        c = MODERN.get((c or "").strip(), (c or "").strip())
        if c and not HISTORIC.search(c) and c not in out:
            out.append(c)
    return out


def _script_ok(name):
    return bool(re.fullmatch(r"[؀-ۿ\s.\-ـ]+", name)) or bool(re.fullmatch(r"[A-Za-zÀ-ɏ\s.'\-]+", name))


def apply(db, results_path, do_apply):
    if not do_apply:
        tmp = os.path.join(tempfile.mkdtemp(), "copy.db")
        src, dst = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(tmp)
        src.backup(dst); src.close(); dst.close(); db = tmp
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = {"applied": do_apply, "updated": [], "skipped": [], "alias_clashes": []}
    for rec in json.load(open(results_path, encoding="utf-8")):
        if rec["status"] != "matched":
            continue
        row = con.execute("SELECT * FROM authors WHERE author_id = ?", (rec["author_id"],)).fetchone()
        if row is None or authors.key(row["name_ar"]) != authors.key(rec["name"]) and authors.find(con, rec["name"]) != rec["author_id"]:
            report["skipped"].append({"author_id": rec["author_id"], "why": "author record changed"}); continue
        aliases = authors._load(row["aliases"])
        have = {authors.key(x) for x in aliases + [row["name_ar"], row["name_latin"] or ""]}
        names = [rec["labels"].get("ar"), rec["labels"].get("en")] + rec["aliases"].get("ar", []) + rec["aliases"].get("en", [])[:3] \
            + [v for l, v in rec["labels"].items() if l not in ("ar", "en")][:4]
        added = []
        for n in names:
            if not n or authors.key(n) in have or not _script_ok(n) or len(added) >= 8:
                continue
            clash = authors._taken(con, [n], row["author_id"])
            if clash:
                report["alias_clashes"].append(f"{row['name_ar']}: «{n}» belongs to «{clash}»"); continue
            aliases.append(n); have.add(authors.key(n)); added.append(n)
        name_latin = row["name_latin"] or rec["labels"].get("en")
        modern = modern_countries(rec["countries"])
        origin = row["origin"] or (" / ".join(modern[:2]) or None)
        known = authors._load(row["known_for"]) or [w for w in rec["notable_works"] if w][:5]
        urls = authors._load(row["source_urls"])
        if rec["url"] not in urls:
            urls.append(rec["url"])
        note = (row["research_note"] or "").split(" | Wikidata")[0] + f" | Wikidata {rec['qid']}: names, origin, known works"
        status = "thin" if row["research_status"] in ("needs_sources", "pending") else row["research_status"]
        con.execute("""UPDATE authors SET aliases=?, name_latin=?, origin=?, known_for=?, source_urls=?, research_note=?,
                       research_status=?, updated_at=? WHERE author_id=?""",
                    (json.dumps(aliases, ensure_ascii=False), name_latin, origin, json.dumps(known, ensure_ascii=False),
                     json.dumps(urls, ensure_ascii=False), note, status, now, row["author_id"]))
        report["updated"].append({"author_id": row["author_id"], "name": row["name_ar"], "qid": rec["qid"], "aliases_added": added,
                                  "origin": origin if not row["origin"] else None, "known_for_filled": not authors._load(row["known_for"]) and bool(known)})
    con.commit(); con.close()
    json.dump(report, open(os.path.join(os.path.dirname(results_path), "apply_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"updated": len(report["updated"]), "skipped": len(report["skipped"]), "alias_clashes": len(report["alias_clashes"]), "applied": do_apply}))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fetch":
        args = sys.argv[4:]
        opt = lambda k, d: args[args.index(k) + 1] if k in args else d
        fetch(sys.argv[2], sys.argv[3], opt("--status", "thin,needs_sources").split(","), int(opt("--limit", 0)), float(opt("--delay", 1.2)))
    elif cmd == "review":  # review <out_dir> --pick 196=Q123,341=Q456 --reject 88,152 --no-origin 83
        args = sys.argv[3:]
        opt = lambda k: args[args.index(k) + 1] if k in args else ""
        picks = {int(a): q for a, q in (x.split("=") for x in opt("--pick").split(",") if x)}
        review(sys.argv[2], picks, {int(x) for x in opt("--reject").split(",") if x}, {int(x) for x in opt("--no-origin").split(",") if x})
    elif cmd == "apply":
        apply(sys.argv[2], sys.argv[3], "--apply" in sys.argv[4:])
