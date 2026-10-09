#!/usr/bin/env python3
"""Staff answers for books that need identity confirmation (see backend/confirmations.py).

Usage (project root, backend/venv/bin/python):
  scripts/confirmations.py                 open answers as JSON (not yet handled)
  scripts/confirmations.py --all           handled ones too
  scripts/confirmations.py --handled ID "what was done"    mark one answer handled after the record was corrected
"""
import json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import confirmations  # noqa: E402

if "--handled" in sys.argv:
    i = sys.argv.index("--handled")
    print("marked", confirmations.mark_handled(int(sys.argv[i + 1]), sys.argv[i + 2] if len(sys.argv) > i + 2 else ""))
else:
    rows = confirmations.open_answers("--all" in sys.argv)
    print(json.dumps({"count": len(rows), "answers": rows}, ensure_ascii=False, indent=1))
