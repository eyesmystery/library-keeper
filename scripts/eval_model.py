"""Evaluate the chat model on a fixed set of Arabic requests (tests/eval/cases.json).

Runs every case through the real chat pipeline (interpret -> database candidates -> respond)
with the configured provider/model, and measures per stage: JSON success, corrective retries,
schema refusals, latency and tokens; plus whether the understood preferences match the
expectations (negation, dialect, age, genre changes...). Nothing is written to the database
and no test log is kept; the report goes to eval_results/ (not committed).

  backend/venv/bin/python scripts/eval_model.py                     # active provider, both modes
  backend/venv/bin/python scripts/eval_model.py --mode schema --repeat 2
  backend/venv/bin/python scripts/eval_model.py --provider openrouter --model google/gemini-3.8-flash
  backend/venv/bin/python scripts/eval_model.py --cases kid-age neg-sad
"""
import argparse
import datetime
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.dont_write_bytecode = True
import app as A  # noqa: E402
import llm  # noqa: E402
import models  # noqa: E402

TARGET = 0.98


def check(expect, dbg, books, cards):
    """Return a list of failed expectation names (empty = pass)."""
    fails = []
    p = dbg.get("prefs") or {}
    g = lambda k: p.get(k) or []
    for k, v in expect.items():
        good = True
        if k == "moods_exclude_has":
            good = all(x in g("moods_exclude") for x in v)
        elif k == "moods_include_not":
            good = not any(x in g("moods_include") for x in v)
        elif k == "moods_include_any":
            good = any(x in g("moods_include") for x in v)
        elif k == "avoid_or_exclude":
            good = any(x in g("avoid_content") or x in g("moods_exclude") for x in v)
        elif k == "avoid_content_has":
            good = all(x in g("avoid_content") for x in v)
        elif k == "genres_has":
            good = all(x in g("genres") for x in v)
        elif k == "genres_any":
            good = any(x in g("genres") for x in v)
        elif k == "genres_not":
            good = not any(x in g("genres") for x in v)
        elif k == "length":
            good = p.get("length") == v
        elif k == "length_not":
            good = p.get("length") != v
        elif k == "length_or_quick":
            good = p.get("length") == "short" or "quick_read" in g("use_cases")
        elif k == "weight_exclude_or_light":
            good = "heavy" in g("weight_exclude") or "intense" in g("weight_exclude") or "light" in g("weight_include")
        elif k == "use_cases_any":
            good = any(x in g("use_cases") for x in v)
        elif k == "use_or_level":
            good = "habit_start" in g("use_cases") or "beginner" in g("reader_level")
        elif k == "authors_contains":
            good = any(v in a for a in g("authors"))
        elif k == "audience":
            good = p.get("audience") == v
        elif k == "intent_in":
            good = dbg.get("intent") in v
        elif k == "titles_nonempty":
            good = bool(dbg.get("mentioned_titles"))
        elif k == "unsupported_nonempty":
            good = bool(g("unsupported_requests"))
        elif k == "wants_new_options":
            good = bool(dbg.get("wants_new_options")) == v
        elif k == "books_age_ok":
            good = all(A.age_allowed(cards.get(b["book_id"]), v) for b in books)
        elif k == "books_valid":
            good = all(b.get("quantity", 0) > 0 and b.get("active") for b in books)
        else:
            good = False
            k = "unknown-check:" + k
        if not good:
            fails.append(k)
    return fails


def run_case(case, active, cards):
    conv, turns = [], []
    out = None
    for text in case["messages"]:
        conv.append({"role": "user", "content": text})
        t0 = time.time()
        out = A.run_chat(conv, active=active)
        dbg = out.get("_debug", {})
        calls = dbg.get("calls") or {}
        if dbg.get("failed_stage"):
            calls = dict(calls)
            calls[dbg["failed_stage"]] = dbg.get("call") or {}
            if dbg["failed_stage"] == "respond":
                calls.setdefault("interpret", {"ok": True})
        turns.append({"ms": int((time.time() - t0) * 1000), "ai": out.get("understanding") == "ai",
                      "failed_stage": dbg.get("failed_stage"), "calls": calls})
        conv.append({"role": "assistant", "content": out.get("reply", ""),
                     "book_ids": [b["book_id"] for b in out.get("books", [])]})
    dbg = out.get("_debug", {})
    interp = dbg if dbg.get("mode") == "ai" else (dbg.get("interpreted") or {})
    fails = check(case["expect"], interp, out.get("books", []), cards) if interp else ["no-interpretation"]
    return {"id": case["id"], "turns": turns, "fails": fails, "intent": interp.get("intent"),
            "prefs": interp.get("prefs"), "books": [b["title"] for b in out.get("books", [])],
            "dropped_ids": dbg.get("dropped_ids") or (dbg.get("interpreted") or {}).get("dropped_ids")}


def summarize(results):
    turns = [t for r in results for t in r["turns"]]
    stage = {}
    for name in ("interpret", "respond"):
        cs = [t["calls"][name] for t in turns if name in t["calls"] and "ok" in t["calls"][name]]
        ms = [c["ms"] for c in cs if isinstance(c.get("ms"), int)]
        stage[name] = {
            "calls": len(cs), "ok": sum(1 for c in cs if c.get("ok")),
            "retried": sum(1 for c in cs if c.get("retried")),
            "schema_refused": sum(1 for c in cs if "refused" in str(c.get("format"))),
            "formats": sorted({str(c.get("format")) for c in cs}),
            "median_ms": int(statistics.median(ms)) if ms else None,
            "p90_ms": int(sorted(ms)[int(0.9 * (len(ms) - 1))]) if ms else None,
            "errors": sorted({c.get("error") for c in cs if c.get("error")})[:5],
            "first_try_errors": sorted({c.get("first_error") for c in cs if c.get("first_error")})[:5],
            "tokens": sum(sum(v for v in (c.get("usage") or {}).values() if isinstance(v, int)) for c in cs),
        }
    ai_turns = sum(1 for t in turns if t["ai"])
    return {"turns": len(turns), "ai_turns": ai_turns, "structured_success": ai_turns / len(turns) if turns else 0,
            "fallback_turns": len(turns) - ai_turns, "stages": stage,
            "cases": len(results), "cases_passed": sum(1 for r in results if not r["fails"]),
            "failed_cases": {r["id"]: r["fails"] for r in results if r["fails"]},
            "median_turn_ms": int(statistics.median([t["ms"] for t in turns])) if turns else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--mode", choices=["schema", "json", "both"], default="both")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--cases", nargs="*")
    args = ap.parse_args()
    active = llm.get_active()
    if args.provider:
        active = (args.provider, args.model or llm.PROVIDERS[args.provider]["default_model"])
    if not active:
        print("No model is configured. Choose one in the admin page (الإعدادات) first.")
        return 2
    cases = json.load(open(os.path.join(ROOT, "tests", "eval", "cases.json"), encoding="utf-8"))
    if args.cases:
        cases = [c for c in cases if c["id"] in args.cases]
    cards = models.get_profile_cards()
    modes = ["schema", "json"] if args.mode == "both" else [args.mode]
    report = {"provider": active[0], "model": active[1], "when": datetime.datetime.now().isoformat(timespec="seconds"),
              "cases_file": "tests/eval/cases.json", "repeat": args.repeat, "modes": {}}
    print(f"Model: {active[0]} / {active[1]} — {len(cases)} cases × {args.repeat} × modes {modes}")
    for mode in modes:
        os.environ["LIBRARY_KEEPER_STRUCTURED"] = mode
        llm._schema_unsupported.clear()
        results = []
        for rep in range(args.repeat):
            for i, case in enumerate(cases, 1):
                r = run_case(case, active, cards)
                results.append(r)
                mark = "✓" if not r["fails"] and all(t["ai"] for t in r["turns"]) else "✗"
                fb = [t["failed_stage"] for t in r["turns"] if t["failed_stage"]]
                print(f"  [{mode} {rep + 1}.{i:02d}] {mark} {case['id']:22s} {sum(t['ms'] for t in r['turns']):6d} ms"
                      + (f"  fallback@{','.join(fb)}" if fb else "") + (f"  miss:{','.join(r['fails'])}" if r["fails"] else ""))
        s = summarize(results)
        report["modes"][mode] = {"summary": s, "results": results}
        st = s["stages"]
        print(f"\n== mode {mode}: structured success {s['structured_success']:.1%} ({s['ai_turns']}/{s['turns']} turns, target {TARGET:.0%})"
              f" | understanding {s['cases_passed']}/{s['cases']} cases | median turn {s['median_turn_ms']} ms")
        for name in ("interpret", "respond"):
            x = st[name]
            print(f"   {name:9s} ok {x['ok']}/{x['calls']}  retried {x['retried']}  schema refused {x['schema_refused']}"
                  f"  median {x['median_ms']} ms  p90 {x['p90_ms']} ms  tokens {x['tokens']}  formats {x['formats']}")
            for e in x["errors"]:
                print(f"      error: {e}")
            for e in x["first_try_errors"]:
                print(f"      fixed by retry: {e}")
        print()
    out_dir = os.path.join(ROOT, "eval_results")
    os.makedirs(out_dir, exist_ok=True)
    name = f"eval-{datetime.datetime.now():%Y%m%d-%H%M%S}-{active[0]}-{active[1].replace('/', '_').replace(':', '_')}.json"
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"Report saved: eval_results/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
