#!/bin/zsh
# ثلاث الذكي — تشغيل على هذا الجهاز فقط (بدون جوالات). للفحص بعد تحديث.
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
if lsof -iTCP:8802 -sTCP:LISTEN >/dev/null 2>&1; then echo "السيرفر شغّال من قبل. أغلق نافذته أولًا."; exit 1; fi
"$ROOT/scripts/ensure_venv.sh" || exit 1
"$ROOT/backend/venv/bin/python" "$ROOT/scripts/backup_db.py" --reason local-start || echo "تنبيه: النسخة الاحتياطية فشلت."
( sleep 4; open "http://127.0.0.1:8802/admin" ) &
cd "$ROOT/backend" && exec ./venv/bin/python app.py
