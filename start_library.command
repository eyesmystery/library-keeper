#!/bin/zsh
# منار — تشغيل السيرفر للمكتبة: هذا الجهاز والجوالات على نفس الواي فاي.
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
if lsof -iTCP:8802 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "السيرفر شغّال من قبل. أفتح صفحة الإدارة…"; open "http://127.0.0.1:8802/admin"; exit 0
fi
"$ROOT/scripts/ensure_venv.sh" || exit 1
if [ ! -f "$ROOT/data/library.db" ]; then
  echo "ملف الكتالوج data/library.db غير موجود. انسخ المجلد كاملًا من الحزمة مرة ثانية."; exit 1
fi
"$ROOT/backend/venv/bin/python" "$ROOT/scripts/backup_db.py" --reason server-start || echo "تنبيه: النسخة الاحتياطية فشلت (راجع backups/backup_log.txt)."
echo ""
echo "منار — السيرفر يبدأ الآن."
echo "  صفحة الإدارة على هذا الجهاز: http://127.0.0.1:8802/admin"
echo "  عنوان الزوار ورمز QR: صفحة الإدارة ← الإعدادات."
echo "اترك هذه النافذة مفتوحة والجهاز مستيقظًا. للإيقاف: Ctrl+C أو أغلق النافذة."
echo ""
( sleep 4; open "http://127.0.0.1:8802/admin" ) &
cd "$ROOT/backend" && LIBRARY_KEEPER_HOST=0.0.0.0 exec caffeinate -i ./venv/bin/python app.py
