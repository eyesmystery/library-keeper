#!/bin/zsh
# Double-click to start Library Keeper locally (opens the browser automatically).
# API keys: entered in the admin page (stored in ~/.library_keeper/keys.json) or exported in your shell.
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
echo "أمين المكتبة — تشغيل السيرفر على http://127.0.0.1:8802"
if lsof -iTCP:8802 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "السيرفر شغّال من قبل. أفتح المتصفح…"; open "http://127.0.0.1:8802"; exit 0
fi
"$ROOT/scripts/ensure_venv.sh" || exit 1
# First run from a fresh clone: build the catalog database from the committed snapshot.
[ -f "$ROOT/data/library.db" ] || "$ROOT/backend/venv/bin/python" "$ROOT/scripts/catalog_snapshot.py" build || exit 1
# Backup before every start (local backups/ + iCloud mirror). A failure here never blocks the server.
"$ROOT/backend/venv/bin/python" "$ROOT/scripts/backup_db.py" --reason server-start || echo "تنبيه: النسخة الاحتياطية فشلت (راجع backups/backup_log.txt)."
[ -n "$ANTHROPIC_API_KEY" ] && echo "✓ مفتاح Claude موجود"
[ -n "$OPENAI_API_KEY" ] && echo "✓ مفتاح ChatGPT موجود"
[ -n "$GEMINI_API_KEY" ] && echo "✓ مفتاح Gemini موجود"
curl -s -m 1 http://localhost:11434/api/tags >/dev/null 2>&1 && echo "✓ Ollama شغّال"
echo "لإيقاف السيرفر: اضغط Ctrl+C أو سكّر النافذة."
( sleep 2; open "http://127.0.0.1:8802" ) &
cd "$ROOT/backend" && exec ./venv/bin/python app.py
