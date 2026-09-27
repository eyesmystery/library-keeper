#!/bin/zsh
# Double-click to start Library Keeper in TRIAL mode: staff phones on the same Wi-Fi can open the
# visitor page by scanning the QR code (admin → الإعدادات → رمز التجربة). Use start_server.command
# for normal use on this computer only.
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
if lsof -iTCP:8802 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "السيرفر شغّال من قبل. سكّره أول (من نافذته: Ctrl+C) ثم شغّل وضع التجربة من جديد."; exit 1
fi
"$ROOT/scripts/ensure_venv.sh" || exit 1
[ -f "$ROOT/data/library.db" ] || "$ROOT/backend/venv/bin/python" "$ROOT/scripts/catalog_snapshot.py" build || exit 1
"$ROOT/backend/venv/bin/python" "$ROOT/scripts/backup_db.py" --reason trial-start || echo "تنبيه: النسخة الاحتياطية فشلت."
IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)"
echo "أمين المكتبة — وضع التجربة (للأجهزة على نفس شبكة الواي فاي)"
if [ -n "$IP" ]; then
  echo "رابط الجوالات:  http://$IP:8802"
else
  echo "تنبيه: ما لقيت عنوان الشبكة. تأكد إن الماك متصل بالواي فاي."
fi
echo "لوحة الإدارة على هذا الجهاز:  http://127.0.0.1:8802/admin   (الإعدادات ← رمز التجربة)"
echo "إذا سألك الماك «السماح بالاتصالات الواردة» لـ Python، اختر السماح."
echo "لإيقاف السيرفر: Ctrl+C أو سكّر النافذة. الماك لازم يبقى صاحي ومتصل طول التجربة."
( sleep 2; open "http://127.0.0.1:8802/admin" ) &
cd "$ROOT/backend" && LIBRARY_KEEPER_HOST=0.0.0.0 exec ./venv/bin/python app.py
