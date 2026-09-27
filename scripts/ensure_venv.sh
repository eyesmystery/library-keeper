#!/bin/zsh
# Creates backend/venv with the pinned requirements if it does not exist yet (first run on a Mac).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/backend/venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "تجهيز بيئة بايثون لأول مرة (backend/venv)…"
  python3 -m venv "$VENV" || { echo "تعذر إنشاء البيئة: تأكد أن python3 مثبت."; exit 1; }
  "$VENV/bin/pip" install --quiet -r "$ROOT/backend/requirements.txt" || { echo "تعذر تثبيت المتطلبات (تحتاج إنترنت)."; exit 1; }
fi
