#!/bin/zsh
# Double-click (or run in Terminal) to run every test suite on a throwaway copy of the project.
ROOT="$(cd "$(dirname "$0")" && pwd)"
"$ROOT/scripts/ensure_venv.sh" || exit 1
"$ROOT/backend/venv/bin/python" "$ROOT/tests/run_all.py" "$@"
rc=$?
[ $rc -eq 0 ] && echo "\n✓ كل الاختبارات نجحت" || echo "\n✗ فيه اختبارات فشلت"
exit $rc
