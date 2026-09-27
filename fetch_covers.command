#!/bin/zsh
# Double-click to fetch missing book covers (Open Library -> Goodreads -> Abjjad). Needs the internet.
# Books still without a cover are listed in reports/covers_missing.csv. The server may keep running.
ROOT="$(cd "$(dirname "$0")" && pwd)"
"$ROOT/scripts/ensure_venv.sh" || exit 1
cd "$ROOT" && "$ROOT/backend/venv/bin/python" "$ROOT/scripts/fetch_covers.py" "$@"
echo; echo "Done. You can close this window."
