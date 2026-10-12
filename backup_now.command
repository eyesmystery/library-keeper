#!/bin/zsh
# منار — نسخة احتياطية الآن (آمنة والسيرفر يعمل).
ROOT="$(cd "$(dirname "$0")" && pwd)"
"$ROOT/scripts/ensure_venv.sh" || exit 1
"$ROOT/backend/venv/bin/python" "$ROOT/scripts/backup_db.py" --reason manual
