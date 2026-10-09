"""Build the transfer copies of the app ("ثلاث الذكي") for the library's computer.

Usage (from the project root, on the Mac):
    backend/venv/bin/python scripts/make_release.py --version "0.9 beta"

Writes two zip files into release/ (ignored by Git):
    ThalathSmart-<version>-<YYYYMMDD-HHMM>-<git commit>-windows.zip      ...-mac.zip
The date (Riyadh time) and the short Git commit are in the name since 2026-10-09: two builds of the
same version number could not be told apart (audit 2026-10-08). `-dirty` after the commit means the
app's code had uncommitted changes when the copy was built. The same facts are written inside the
package as BUILD.json (the VERSION file stays one line: the app reads it).

What goes in: the app code (backend, frontend, admin), the few scripts the running app needs
(backup, restore, first-run Python environment, staff confirmations), a cleaned copy of the live
database, the cover images, start files for the platform and an Arabic install guide.

What stays out: research/, tests/, reports/, eval results, backups/, docs/, Rewaa import files,
visitor test logs, the Git history, the seed and snapshot files, every key or secret.

The database copy is taken with SQLite's backup API (safe while the server runs) and then cleaned:
only the manager account named by --keep-user stays; trial traces are emptied (audit log, visitor
feedback, choices, daily counters, visitor notes, staff confirmations). With --no-user (the copies
published on GitHub, 2026-10-09) no account stays at all: the first start asks for a new manager
account, so no password hash leaves this computer. Books, cards, evidence,
authors, sections, relations, import history and the deleted-books list are kept as they are.
The live database is never touched.

Updates (2026-10-09). A full package replaces everything on the library's computer. With
--update-from <an earlier full package .zip> (may be given several times) a third, small file is also
written: ThalathSmart-update-<version>-<time>-<commit>.zip, one file for both systems. Its installer
(scripts/apply_update.py, copied into the package) keeps the library's accounts, settings, events,
stock and activity, replaces the code and merges the catalog row by row against the catalog the
library installed last time. For that, every full package now carries data/catalog_base.db (the
catalog as shipped, with the list of cover files), and ids of rows created in the library start at
1,000,000,000. The update holds only the covers that are new or changed since the earlier packages,
and a base catalog for each of them (for installations made before catalog_base.db existed).
"""
import argparse
import datetime
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply_update  # noqa: E402  (catalog file, id ranges and the installer itself)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "release")

BACKEND_SKIP = {"venv", "venv-win", "__pycache__"}
SCRIPTS = ["backup_db.py", "restore_db.py", "confirmations.py", "ensure_venv.sh", "ensure_venv.bat", "build_embeddings.py"]
EMPTY_TABLES = ["audit_log", "feedback", "book_choices", "book_daily", "usage_daily", "visitor_notes",
                "book_confirmations", "open_questions", "staff_notes"]

# ---------------------------------------------------------------- start files (Windows)
WIN_START = r"""@echo off
rem Thalath Smart - start the server for the library: this computer and phones on the same Wi-Fi.
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "PYTHONUTF8=1"
netstat -ano | findstr /R /C:":8802 .*LISTENING" >nul && (
  echo The server is already running. Opening the admin page...
  start "" "http://127.0.0.1:8802/admin" & exit /b 0
)
call "%ROOT%scripts\ensure_venv.bat" || (pause & exit /b 1)
set "PYW=%ROOT%backend\venv-win\Scripts\python.exe"
if not exist "%ROOT%data\library.db" (
  echo The catalog file data\library.db is missing. Copy the whole folder again from the package.
  pause & exit /b 1
)
rem Backups stay in the backups folder; a second copy goes to OneDrive when this computer has it.
if not defined LIBRARY_KEEPER_BACKUP_MIRROR if exist "%USERPROFILE%\OneDrive\" set "LIBRARY_KEEPER_BACKUP_MIRROR=%USERPROFILE%\OneDrive\Thalath Smart Backups"
"%PYW%" "%ROOT%scripts\backup_db.py" --reason server-start || echo Warning: the backup failed (see backups\backup_log.txt).
echo.
echo Thalath Smart - the server is starting.
echo   Admin page on this computer:  http://127.0.0.1:8802/admin
echo   Visitors' address and QR code: admin page - Settings.
echo If Windows asks whether Python may communicate on networks, allow it on PRIVATE networks.
echo Keep this window open and this computer awake. To stop the server: close this window.
echo.
start "" /min cmd /c "timeout /t 6 /nobreak >nul & start "" http://127.0.0.1:8802/admin"
cd /d "%ROOT%backend"
set "LIBRARY_KEEPER_HOST=0.0.0.0"
"%PYW%" app.py
echo.
echo The server stopped. If this was not expected, read the lines above.
pause
"""

WIN_LOCAL = r"""@echo off
rem Thalath Smart - start for THIS computer only (no phones). Use it for checking after an update.
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "PYTHONUTF8=1"
netstat -ano | findstr /R /C:":8802 .*LISTENING" >nul && (
  echo The server is already running. Close its window first.
  pause & exit /b 1
)
call "%ROOT%scripts\ensure_venv.bat" || (pause & exit /b 1)
set "PYW=%ROOT%backend\venv-win\Scripts\python.exe"
"%PYW%" "%ROOT%scripts\backup_db.py" --reason local-start || echo Warning: the backup failed (see backups\backup_log.txt).
start "" /min cmd /c "timeout /t 6 /nobreak >nul & start "" http://127.0.0.1:8802/admin"
cd /d "%ROOT%backend"
"%PYW%" app.py
pause
"""

WIN_AUTOSTART = r"""@echo off
rem Thalath Smart - run once: start the server automatically when this Windows user signs in,
rem and make a database backup every day at 03:15. Undo with remove_autostart.bat.
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
call "%ROOT%scripts\ensure_venv.bat" || (pause & exit /b 1)
set "PYW=%ROOT%backend\venv-win\Scripts\python.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w = New-Object -ComObject WScript.Shell; $l = $w.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'Thalath Smart.lnk')); $l.TargetPath = (Join-Path $env:ROOT 'start_library.bat'); $l.WorkingDirectory = $env:ROOT; $l.WindowStyle = 7; $l.Save()"
if errorlevel 1 (echo Could not create the startup shortcut.) else (echo OK: the server will start when this user signs in.)
schtasks /Create /F /SC DAILY /ST 03:15 /TN "ThalathSmartBackup" /TR "\"%ROOT%scripts\daily_backup.bat\"" >nul
if errorlevel 1 (echo Could not create the daily backup task.) else (echo OK: daily backup at 03:15.)
pause
"""

WIN_REMOVE = r"""@echo off
rem Thalath Smart - undo install_autostart.bat.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-Item -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('Startup')) 'Thalath Smart.lnk')"
schtasks /Delete /F /TN "ThalathSmartBackup" >nul 2>&1
echo Removed: automatic start and the daily backup task.
pause
"""

WIN_DAILY = r"""@echo off
rem Thalath Smart - daily backup run by the scheduled task (03:15). Same mirror folder as the start file.
setlocal
set "ROOT=%~dp0..\"
set "PYTHONUTF8=1"
if not defined LIBRARY_KEEPER_BACKUP_MIRROR if exist "%USERPROFILE%\OneDrive\" set "LIBRARY_KEEPER_BACKUP_MIRROR=%USERPROFILE%\OneDrive\Thalath Smart Backups"
"%ROOT%backend\venv-win\Scripts\python.exe" "%ROOT%scripts\backup_db.py" --reason daily
"""

WIN_BACKUP = r"""@echo off
rem Thalath Smart - make a database backup now (safe while the server is running).
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PYTHONUTF8=1"
call "%ROOT%scripts\ensure_venv.bat" || (pause & exit /b 1)
if not defined LIBRARY_KEEPER_BACKUP_MIRROR if exist "%USERPROFILE%\OneDrive\" set "LIBRARY_KEEPER_BACKUP_MIRROR=%USERPROFILE%\OneDrive\Thalath Smart Backups"
"%ROOT%backend\venv-win\Scripts\python.exe" "%ROOT%scripts\backup_db.py" --reason manual
pause
"""

# ---------------------------------------------------------------- start files (Mac)
MAC_START = r"""#!/bin/zsh
# ثلاث الذكي — تشغيل السيرفر للمكتبة: هذا الجهاز والجوالات على نفس الواي فاي.
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
echo "ثلاث الذكي — السيرفر يبدأ الآن."
echo "  صفحة الإدارة على هذا الجهاز: http://127.0.0.1:8802/admin"
echo "  عنوان الزوار ورمز QR: صفحة الإدارة ← الإعدادات."
echo "اترك هذه النافذة مفتوحة والجهاز مستيقظًا. للإيقاف: Ctrl+C أو أغلق النافذة."
echo ""
( sleep 4; open "http://127.0.0.1:8802/admin" ) &
cd "$ROOT/backend" && LIBRARY_KEEPER_HOST=0.0.0.0 exec caffeinate -i ./venv/bin/python app.py
"""

MAC_LOCAL = r"""#!/bin/zsh
# ثلاث الذكي — تشغيل على هذا الجهاز فقط (بدون جوالات). للفحص بعد تحديث.
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
if lsof -iTCP:8802 -sTCP:LISTEN >/dev/null 2>&1; then echo "السيرفر شغّال من قبل. أغلق نافذته أولًا."; exit 1; fi
"$ROOT/scripts/ensure_venv.sh" || exit 1
"$ROOT/backend/venv/bin/python" "$ROOT/scripts/backup_db.py" --reason local-start || echo "تنبيه: النسخة الاحتياطية فشلت."
( sleep 4; open "http://127.0.0.1:8802/admin" ) &
cd "$ROOT/backend" && exec ./venv/bin/python app.py
"""

MAC_BACKUP = r"""#!/bin/zsh
# ثلاث الذكي — نسخة احتياطية الآن (آمنة والسيرفر يعمل).
ROOT="$(cd "$(dirname "$0")" && pwd)"
"$ROOT/scripts/ensure_venv.sh" || exit 1
"$ROOT/backend/venv/bin/python" "$ROOT/scripts/backup_db.py" --reason manual
"""

# ---------------------------------------------------------------- update package (both systems)
WIN_UPDATE = r"""@echo off
rem Thalath Smart - install this update. Accounts, settings, events and stock stay; the catalog is merged.
chcp 65001 >nul
setlocal
set "UPD=%~dp0"
set "APP=%~dp0.."
set "PYTHONUTF8=1"
if not exist "%APP%\data\library.db" goto :noapp
set "PYW=%APP%\backend\venv-win\Scripts\python.exe"
if not exist "%PYW%" goto :nopy
"%PYW%" "%UPD%apply_update.py" --app "%APP%" --update "%UPD%."
echo.
pause
exit /b 0
:noapp
echo This update folder must be INSIDE the Thalath Smart folder, next to start_library.bat.
echo Move the whole update folder there, then run install_update.bat again.
pause
exit /b 1
:nopy
echo The app's Python environment was not found.
echo Start the app once with start_library.bat, close its window, then run install_update.bat again.
pause
exit /b 1
"""

MAC_UPDATE = r"""#!/bin/zsh
# ثلاث الذكي — تثبيت هذا التحديث. الحسابات والإعدادات والفعاليات والمخزون تبقى؛ الكتالوج يُدمج.
UPD="$(cd "$(dirname "$0")" && pwd)"
APP="$(dirname "$UPD")"
if [ ! -f "$APP/data/library.db" ]; then
  echo "ضع مجلد التحديث داخل مجلد «ثلاث الذكي» (بجانب start_library.command) ثم شغّل هذا الملف من جديد."; exit 1
fi
PY="$APP/backend/venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "لم أجد بيئة بايثون الخاصة بالتطبيق. شغّل التطبيق مرة واحدة من start_library.command ثم أغلقه وأعد المحاولة."; exit 1
fi
"$PY" "$UPD/apply_update.py" --app "$APP" --update "$UPD"
"""


def update_guide(version, counts, n_covers, from_versions):
    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ثلاث الذكي — تحديث {version}</title><style>{GUIDE_CSS}</style></head><body><main>
<h1>ثلاث الذكي — تحديث إلى {version}</h1>
<p class="lead">تحديث فقط: لا يمسح شيئًا مما أضفتموه. فيه الكود الجديد، وتحديث كتالوج {counts['books']:,} كتابًا، و{n_covers:,} غلافًا جديدًا أو معدّلًا.</p>

<h2>الخطوات</h2>
<ol>
<li>أغلق نافذة السيرفر (التطبيق يجب أن يكون متوقفًا أثناء التحديث).</li>
<li>انقل هذا المجلد كاملًا إلى <b>داخل</b> مجلد «ثلاث الذكي»، بجانب ملف <code>start_library</code>.</li>
<li>انقر مرتين على <code>install_update.bat</code> (ويندوز) أو <code>install_update.command</code> (ماك؛ أول مرة: زر أيمن ثم «فتح»).</li>
<li>انتظر حتى تظهر علامة ✓ ثم شغّل التطبيق من <code>start_library</code> كالعادة.</li>
</ol>

<h2>ما الذي يبقى كما هو</h2>
<ul>
<li>حسابات الموظفين وكلمات المرور، والإعدادات ونموذج الذكاء الاصطناعي ومفتاحه.</li>
<li>الفعاليات وصورها، وأقسام المكتبة.</li>
<li>الكميات والأسعار ومواقع الرفوف، وكل ما استوردتموه من «رواء».</li>
<li>بلاغات الزوار، وملاحظات الموظفين وطلباتهم، والإحصاءات.</li>
<li>أي حقل في بيانات كتاب عدّلتموه بأنفسكم، وأي غلاف رفعتموه.</li>
</ul>

<h2>ما الذي يتغيّر</h2>
<ul>
<li>ملفات التطبيق (الصفحات والسيرفر).</li>
<li>بيانات الكتب التي لم تعدّلوها: النبذات، الأنواع، المؤلفون، الجوائز، آراء القراء، والأغلفة.</li>
</ul>

<div class="note">بعد التحديث يُكتب تقرير في مجلد <code>updates</code> داخل مجلد التطبيق. إذا ذكرت الشاشة أن فيه حقولًا احتفظنا
بقيمتكم فيها مع أن التحديث فيه قيمة أخرى، فأرسلوا ملف التقرير إلى إبراهيم.</div>

<h2>إذا حصلت مشكلة</h2>
<ul>
<li>لا يتغير شيء إلا بعد نجاح الدمج. إن توقف التحديث بخطأ فالتطبيق كما كان: انسخ السطر الظاهر وأرسله.</li>
<li>نسخة من القاعدة والملفات القديمة تُحفظ في <code>backups/before_update_…</code>.</li>
<li>«السيرفر يعمل الآن»: أغلق نافذة السيرفر ثم أعد تشغيل ملف التحديث.</li>
</ul>
<p class="lead">يصلح هذا التحديث لمن عنده أي إصدار سابق{(' (جُهّز خصوصًا لـ ' + '، '.join(from_versions) + ')') if from_versions else ''}.</p>
</main></body></html>
"""


def read_package(zip_path, work):
    """An earlier full package -> (its version, path of its database copied out, {cover name: sha1})."""
    import hashlib
    covers, version, db = {}, None, None
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            parts = info.filename.split("/")
            rest = "/".join(parts[1:])
            if rest == "VERSION":
                version = z.read(info).decode("utf-8").strip()
            elif rest == "data/library.db":
                db = os.path.join(work, "prev_%d.db" % len(os.listdir(work)))
                with z.open(info) as src, open(db, "wb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
            elif rest.startswith("data/covers/") and not info.is_dir():
                h = hashlib.sha1()
                with z.open(info) as src:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        h.update(chunk)
                covers[parts[-1]] = h.hexdigest()
    if not version or not db:
        raise SystemExit(f"{zip_path}: not a full package (no VERSION or data/library.db inside)")
    return version, db, covers


def build_update(stage, version, info, counts, from_zips, zip_path):
    """The small update package, made from the staged full package (before the platform files are added)."""
    up = os.path.join(OUT, "_update")
    if os.path.exists(up):
        shutil.rmtree(up)
    payload = os.path.join(up, "payload")
    for part in ("backend", "frontend", "admin", "scripts"):
        copy_tree(os.path.join(stage, part), os.path.join(payload, part))
    for name in ("VERSION", "BUILD.json"):
        shutil.copy2(os.path.join(stage, name), os.path.join(payload, name))
    win, mac = os.path.join(payload, "start", "windows"), os.path.join(payload, "start", "mac")
    for name, text in (("start_library.bat", WIN_START), ("start_this_computer_only.bat", WIN_LOCAL),
                       ("install_autostart.bat", WIN_AUTOSTART), ("remove_autostart.bat", WIN_REMOVE),
                       ("backup_now.bat", WIN_BACKUP), (os.path.join("scripts", "daily_backup.bat"), WIN_DAILY)):
        write(os.path.join(win, name), text, crlf=True)
    with open(os.path.join(ROOT, "scripts", "ensure_venv.bat"), encoding="utf-8") as f:
        write(os.path.join(win, "scripts", "ensure_venv.bat"), f.read(), crlf=True)
    for name, text in (("start_library.command", MAC_START), ("start_this_computer_only.command", MAC_LOCAL),
                       ("backup_now.command", MAC_BACKUP)):
        write(os.path.join(mac, name), text, executable=True)
    os.makedirs(os.path.join(mac, "scripts"), exist_ok=True)
    for x in ("ensure_venv.sh", "install_daily_backup.command"):
        shutil.copy2(os.path.join(ROOT, "scripts", x), os.path.join(mac, "scripts", x))
        os.chmod(os.path.join(mac, "scripts", x), 0o755)
    shutil.copy2(os.path.join(stage, "data", "catalog_base.db"), os.path.join(up, "catalog.db"))
    shutil.copy2(os.path.join(ROOT, "scripts", "apply_update.py"), os.path.join(up, "apply_update.py"))
    covers_dir = os.path.join(stage, "data", "covers")
    now = apply_update.cover_manifest(covers_dir)
    need, from_versions = set(), []
    os.makedirs(os.path.join(up, "bases"))
    with tempfile.TemporaryDirectory() as work:
        for zp in from_zips:
            prev_version, prev_db, prev_covers = read_package(zp, work)
            from_versions.append(prev_version)
            apply_update.build_catalog(prev_db, os.path.join(up, "bases", version_tag(prev_version) + ".db"),
                                       {"version": prev_version, "from_package": os.path.basename(zp)}, prev_covers)
            need |= {name for name, sha in now.items() if prev_covers.get(name) != sha}
    os.makedirs(os.path.join(up, "covers"))
    for name in sorted(need):
        shutil.copy2(os.path.join(covers_dir, name), os.path.join(up, "covers", name))
    write(os.path.join(up, "install_update.bat"), WIN_UPDATE, crlf=True)
    write(os.path.join(up, "install_update.command"), MAC_UPDATE, executable=True)
    write(os.path.join(up, "README-UPDATE-AR.html"), update_guide(version, counts, len(need), from_versions))
    check_clean(up)
    size = zip_stage(up, zip_path, f"ThalathSmart-update-{version_tag(version)}")
    shutil.rmtree(up)
    return {"path": os.path.relpath(zip_path, ROOT), "mb": round(size / 1e6, 1), "covers": len(need), "from": from_versions}


# ---------------------------------------------------------------- Arabic install guide
GUIDE_CSS = """
body{font-family:"Segoe UI",Tahoma,"Geeza Pro",Arial,sans-serif;background:#FDF9F3;color:#2b1a12;line-height:1.9;
margin:0;padding:24px 16px}main{max-width:780px;margin:0 auto}h1{color:#3F1D10;font-size:1.7rem;margin:0 0 4px}
h2{color:#3F1D10;font-size:1.2rem;margin:30px 0 8px;border-bottom:2px solid #DC634E;padding-bottom:4px}
p.lead{color:#6b5146;margin:0 0 18px}ol,ul{padding-inline-start:1.4em}li{margin:6px 0}
code{background:#f1e7da;padding:1px 6px;border-radius:4px;direction:ltr;unicode-bidi:embed;font-family:Consolas,Menlo,monospace;font-size:.92em}
.note{background:#fff;border:1px solid #e6d8c6;border-inline-start:4px solid #DC634E;padding:10px 14px;border-radius:6px;margin:12px 0}
table{border-collapse:collapse;width:100%;margin:10px 0}td,th{border:1px solid #e6d8c6;padding:7px 10px;text-align:start;vertical-align:top}
th{background:#f1e7da}
"""


def guide(platform, version, model, counts):
    win = platform == "windows"
    start = "start_library.bat" if win else "start_library.command"
    local = "start_this_computer_only.bat" if win else "start_this_computer_only.command"
    backup = "backup_now.bat" if win else "backup_now.command"
    py = ("ثبّت Python 3 من <code>python.org/downloads</code>، وعند التثبيت فعّل الخيار "
          "<b>Add python.exe to PATH</b>.") if win else (
          "تأكد أن Python 3 موجود: افتح Terminal واكتب <code>python3 --version</code>. إن لم يكن موجودًا "
          "فثبّته من <code>python.org/downloads</code>.")
    first = ("انقر مرتين على <code>start_library.bat</code>. أول مرة يجهّز بيئة بايثون (يحتاج إنترنت، دقيقة تقريبًا)، "
             "ثم تفتح صفحة الإدارة في المتصفح. إذا سأل Windows هل تسمح لـ Python بالاتصال بالشبكة فاسمح له على "
             "<b>الشبكات الخاصة</b>؛ بدون هذا لا تصل الجوالات إلى التطبيق.") if win else (
             "انقر مرتين على <code>start_library.command</code>. إذا منعه macOS أول مرة: انقر عليه بالزر الأيمن ثم "
             "«فتح». أول مرة يجهّز بيئة بايثون (يحتاج إنترنت، دقيقة تقريبًا)، ثم تفتح صفحة الإدارة في المتصفح.")
    auto = ("انقر مرتين على <code>install_autostart.bat</code> مرة واحدة: يشغّل السيرفر تلقائيًا عند دخول مستخدم "
            "Windows، ويعمل نسخة احتياطية كل يوم 03:15. للتراجع: <code>remove_autostart.bat</code>. "
            "التشغيل التلقائي يبدأ بعد تسجيل الدخول إلى Windows، لا قبله.") if win else (
            "للتشغيل التلقائي: إعدادات النظام ← عام ← عناصر تسجيل الدخول، وأضف <code>start_library.command</code>. "
            "للنسخة الاحتياطية اليومية 03:15: انقر مرتين على <code>scripts/install_daily_backup.command</code> مرة واحدة.")
    sleep = ("إعدادات Windows ← النظام ← الطاقة: اجعل «السكون» عند التوصيل بالكهرباء = <b>أبدًا</b>.") if win else (
             "ملف التشغيل يمنع السكون ما دام السيرفر يعمل. أبقِ الجهاز موصولًا بالكهرباء والغطاء مفتوحًا.")
    mirror = ("النسخ تُحفظ في مجلد <code>backups</code> داخل مجلد التطبيق (آخر 30 نسخة). إذا كان على الجهاز OneDrive "
              "تُحفظ نسخة ثانية تلقائيًا في <code>OneDrive\\Thalath Smart Backups</code>.") if win else (
              "النسخ تُحفظ في مجلد <code>backups</code> داخل مجلد التطبيق (آخر 30 نسخة)، ونسخة ثانية في iCloud Drive "
              "تحت <code>Projects/Apps/Library Keeper Backups</code> إن كان iCloud مفعّلًا.")
    restore = ("افتح موجّه الأوامر داخل مجلد التطبيق واكتب: "
               "<code>backend\\venv-win\\Scripts\\python.exe scripts\\restore_db.py --latest</code>") if win else (
               "افتح Terminal داخل مجلد التطبيق واكتب: "
               "<code>backend/venv/bin/python scripts/restore_db.py --latest</code>")
    if counts.get("user"):
        account = f"وحساب مدير واحد (<code>{counts['user']}</code>) بكلمة مروره الحالية."
        signin = f"ادخل صفحة الإدارة بالاسم <code>{counts['user']}</code> وكلمة مرورك."
    else:
        account = "وبلا أي حساب: تنشئ حساب المدير عند أول تشغيل."
        signin = ("صفحة الإدارة تطلب إنشاء حساب المدير (مرة واحدة، ومن هذا الجهاز فقط): اسم مستخدم بحروف إنجليزية "
                  "وكلمة مرور من 8 خانات على الأقل فيها حروف وأرقام.")
    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ثلاث الذكي — تعليمات التركيب</title><style>{GUIDE_CSS}</style></head><body><main>
<h1>ثلاث الذكي — تعليمات التركيب ({'ويندوز' if win else 'ماك'})</h1>
<p class="lead">الإصدار {version}. في هذه الحزمة: {counts['books']:,} كتابًا، {counts['covers']:,} غلافًا، {account}</p>

<h2>١. قبل البدء</h2>
<ol>
<li>انسخ المجلد كاملًا إلى مكان ثابت على الجهاز (مثل المستندات). لا تشغّله من داخل الملف المضغوط ولا من فلاش.</li>
<li>{py}</li>
<li>ثبّت تطبيق Ollama من <code>ollama.com/download</code> وافتحه، ثم سجّل الدخول بحسابك: من التطبيق نفسه، أو بالأمر
<code>ollama signin</code>. النموذج المضبوط <code>{model}</code> يعمل على خوادم Ollama، فلا تنزيل كبير، لكنه يحتاج
إنترنت وتسجيل الدخول.</li>
</ol>

<h2>٢. أول تشغيل</h2>
<ol>
<li>{first}</li>
<li>{signin}</li>
<li>الإعدادات ← الذكاء الاصطناعي: تأكد أن Ollama يظهر «يعمل» وأن النموذج المختار <code>{model}</code>.</li>
<li>جرّب محادثة من صفحة الزائر: <code>http://127.0.0.1:8802</code>.</li>
<li>أنشئ حسابات الموظفين من الإعدادات ← الموظفون.</li>
</ol>
<div class="note">إذا لم يتصل النموذج يبقى التطبيق يعمل ويرشّح بالكلمات المفتاحية، لكن بدون صياغة الذكاء الاصطناعي.</div>

<h2>٣. الزوار ورمز QR</h2>
<ul>
<li>عنوان الزوار ورمز QR في صفحة الإدارة ← الإعدادات ← رمز QR. العنوان هو عنوان هذا الجهاز على الواي فاي (مثل
<code>http://192.168.1.20:8802</code>)، والجوال يجب أن يكون على نفس الشبكة.</li>
<li><b>الأفضل:</b> ثبّت عنوان هذا الجهاز من الراوتر (DHCP reservation)، ثم اطبع الرمز.</li>
<li><b>إذا لم يُثبَّت:</b> اعرض الرمز على شاشة من نفس الصفحة بدل طباعته؛ إذا تغيّر العنوان يتغيّر الرمز المعروض معه.</li>
</ul>

<h2>٤. التشغيل اليومي</h2>
<table>
<tr><th>الملف</th><th>ماذا يفعل</th></tr>
<tr><td><code>{start}</code></td><td>تشغيل المكتبة: هذا الجهاز والجوالات على الواي فاي. يعمل نسخة احتياطية قبل كل تشغيل.</td></tr>
<tr><td><code>{local}</code></td><td>تشغيل على هذا الجهاز فقط، للفحص.</td></tr>
<tr><td><code>{backup}</code></td><td>نسخة احتياطية الآن.</td></tr>
</table>
<ul>
<li>للإيقاف: أغلق نافذة السيرفر.</li>
<li>{auto}</li>
<li>السكون: {sleep}</li>
</ul>

<h2>٥. النسخ الاحتياطي والاسترجاع</h2>
<ul>
<li>{mirror}</li>
<li>للاسترجاع: أوقف السيرفر أولًا، ثم {restore}. القاعدة الحالية تُحفظ قبل الاستبدال باسم
<code>data/library.db.before-restore-…</code>.</li>
<li>الكتالوج كله في ملف واحد: <code>data/library.db</code>، والأغلفة في <code>data/covers</code>.</li>
</ul>

<h2>٦. تصحيحات الموظفين</h2>
<p>ردود الموظفين على «الكتب التي تحتاج تأكيد هوية» تُخزَّن على هذا الجهاز. لإرسالها للتصحيح: صفحة الإدارة ← استيراد من رواء
← «ردود الموظفين على تأكيد هوية الكتب» ← نسخ الردود أو تنزيلها. وملف التصحيحات الراجع يُطبَّق من نفس المكان بعد معاينته.</p>

<h2>٧. إذا حصلت مشكلة</h2>
<table>
<tr><th>العَرَض</th><th>الحل</th></tr>
<tr><td>الجوال لا يفتح العنوان</td><td>تأكد أنه على نفس الواي فاي{'، وأن Python مسموح له في جدار الحماية على الشبكات الخاصة، وأن الشبكة معرّفة «خاصة» لا «عامة»' if win else '، وأن جدار الحماية في إعدادات النظام يسمح لـ Python بالاتصالات الواردة'}. بعض شبكات الضيوف تمنع الأجهزة من رؤية بعضها.</td></tr>
<tr><td>الردود بلا ذكاء اصطناعي</td><td>افتح Ollama وتأكد من تسجيل الدخول والإنترنت، ثم الإعدادات ← الذكاء الاصطناعي.</td></tr>
<tr><td>«السيرفر شغّال من قبل»</td><td>نافذة سيرفر أخرى مفتوحة؛ أغلقها ثم شغّل من جديد.</td></tr>
<tr><td>{'«Python 3 is not installed»' if win else 'تعذر إنشاء بيئة بايثون'}</td><td>ثبّت Python كما في الخطوة ١ ثم شغّل مرة ثانية. أول تشغيل يحتاج إنترنت.</td></tr>
<tr><td>نسيت كلمة المرور</td><td>لا تُسترجع من الصفحة. انسخ ما يظهر في نافذة السيرفر واطلب المساعدة.</td></tr>
</table>
<div class="note">عند أي خطأ: صوّر أو انسخ آخر الأسطر في نافذة السيرفر، ومعها الملف <code>backups/backup_log.txt</code> إن كان
الخطأ في النسخ الاحتياطي.</div>

<h2>ما ليس في هذه الحزمة</h2>
<p>ملفات البحث والتجارب، سجلات محادثات التجربة، النسخ الاحتياطية القديمة، وأي مفاتيح. حسابات التجربة حُذفت، وعدّادات
الاستخدام وتقييمات التجربة صُفّرت.</p>
</main></body></html>
"""


def copy_tree(src, dst, skip=()):
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skip and d != "__pycache__"]
        rel = os.path.relpath(base, src)
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for f in files:
            if f.endswith((".pyc", ".part", ".tmp")) or f == ".DS_Store":
                continue
            shutil.copy2(os.path.join(base, f), os.path.join(dst, rel, f))


def write(path, text, crlf=False, executable=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = text.replace("\r\n", "\n")
    if crlf:
        text = text.replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    if executable:
        os.chmod(path, 0o755)


def clean_database(dest, keep_user):
    src = sqlite3.connect(f"file:{os.path.join(ROOT, 'data', 'library.db')}?mode=ro", uri=True)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        src.close()
    try:
        users = [r[0] for r in out.execute("SELECT username FROM staff_users")]
        if keep_user is not None and keep_user not in users:
            raise SystemExit(f"account {keep_user!r} is not in staff_users ({users})")
        tables = {r[0] for r in out.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with out:
            if keep_user is None:  # --no-user: the first start asks for a new manager account
                out.execute("DELETE FROM staff_users")
                if "sqlite_sequence" in tables:
                    out.execute("DELETE FROM sqlite_sequence WHERE name = 'staff_users'")
            else:
                out.execute("DELETE FROM staff_users WHERE username <> ?", (keep_user,))
                out.execute("UPDATE staff_users SET last_login_at = NULL WHERE username = ?", (keep_user,))
            for t in EMPTY_TABLES:
                if t in tables:
                    out.execute(f'DELETE FROM "{t}"')
                    if "sqlite_sequence" in tables:
                        out.execute("DELETE FROM sqlite_sequence WHERE name = ?", (t,))
        with out:   # rows the library creates get ids from 1,000,000,000 up (see apply_update.py)
            apply_update.raise_sequences(out)
        out.execute("VACUUM")
        ok = out.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise SystemExit(f"integrity check failed on the release database: {ok}")
        row = out.execute("SELECT role, active FROM staff_users").fetchall()
        if row != ([] if keep_user is None else [("manager", 1)]):
            raise SystemExit(f"unexpected accounts in the release database: {row}")
        return {"books": out.execute("SELECT COUNT(*) FROM books").fetchone()[0],
                "authors": out.execute("SELECT COUNT(*) FROM authors").fetchone()[0],
                "evidence": out.execute("SELECT COUNT(*) FROM attribute_evidence").fetchone()[0],
                "user": keep_user}
    finally:
        out.close()


def build_common(stage, version, keep_user, model, info=None):
    if os.path.exists(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    copy_tree(os.path.join(ROOT, "backend"), os.path.join(stage, "backend"), skip=BACKEND_SKIP)
    copy_tree(os.path.join(ROOT, "frontend"), os.path.join(stage, "frontend"))
    copy_tree(os.path.join(ROOT, "admin"), os.path.join(stage, "admin"))
    os.makedirs(os.path.join(stage, "scripts"))
    for name in SCRIPTS:
        shutil.copy2(os.path.join(ROOT, "scripts", name), os.path.join(stage, "scripts", name))
    os.makedirs(os.path.join(stage, "data"))
    counts = clean_database(os.path.join(stage, "data", "library.db"), keep_user)
    copy_tree(os.path.join(ROOT, "data", "covers"), os.path.join(stage, "data", "covers"))
    counts["covers"] = len(os.listdir(os.path.join(stage, "data", "covers")))
    # pictures of the library's events (2026-10-08): the events themselves stay in the database
    if os.path.isdir(os.path.join(ROOT, "data", "event_images")):
        copy_tree(os.path.join(ROOT, "data", "event_images"), os.path.join(stage, "data", "event_images"))
    # the catalog as shipped: the base of the library's next update
    apply_update.build_catalog(os.path.join(stage, "data", "library.db"), os.path.join(stage, "data", "catalog_base.db"),
                               dict(info or {}, version=version), os.path.join(stage, "data", "covers"))
    write(os.path.join(stage, "data", "llm_settings.json"),
          json.dumps({"provider": "ollama", "model": model, "test_log": False}, ensure_ascii=False, indent=1) + "\n")
    write(os.path.join(stage, "VERSION"), version + "\n")
    return counts


def check_clean(stage):
    """Nothing that looks like a key, a log of visitors or working data may be in the package."""
    bad = []
    for base, _dirs, files in os.walk(stage):
        for f in files:
            p = os.path.join(base, f)
            rel = os.path.relpath(p, stage)
            if f in ("keys.json", "secret_key", ".env") or f.endswith(".key") or rel.startswith(("research", "tests", "backups")):
                bad.append(rel)
            elif not rel.startswith((os.path.join("data", "covers"), os.path.join("data", "event_images"))) and os.path.getsize(p) < 2_000_000 \
                    and not f.endswith((".db", ".ttf", ".png", ".jpg", ".svg")):
                with open(p, "rb") as fh:
                    blob = fh.read()
                if b"AIza" in blob or b"sk-ant-" in blob:
                    bad.append(rel + " (key-like text)")
    if bad:
        raise SystemExit("release stopped, unexpected files: " + ", ".join(bad))


def zip_stage(stage, zip_path, top):
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for base, dirs, files in os.walk(stage):
            dirs.sort()
            for f in sorted(files):
                p = os.path.join(base, f)
                arc = os.path.join(top, os.path.relpath(p, stage)).replace(os.sep, "/")
                info = zipfile.ZipInfo.from_file(p, arc)
                mode = stat.S_IMODE(os.stat(p).st_mode)
                info.external_attr = ((0o100000 | mode) & 0xFFFF) << 16
                stored = f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
                info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
                with open(p, "rb") as fh, z.open(info, "w") as out:
                    shutil.copyfileobj(fh, out, 1024 * 1024)
    return os.path.getsize(zip_path)


CODE_PATHS = ["backend", "frontend", "admin", "scripts", "VERSION"]


def git_ref(root=ROOT):
    """-> (short commit or "nogit", True when the app's code has uncommitted changes)."""
    try:
        ref = subprocess.run(["git", "-C", root, "rev-parse", "--short=7", "HEAD"],
                             capture_output=True, text=True, timeout=20)
        if ref.returncode != 0 or not ref.stdout.strip():
            return "nogit", False
        dirty = subprocess.run(["git", "-C", root, "status", "--porcelain", "--"] + CODE_PATHS,
                               capture_output=True, text=True, timeout=60)
        return ref.stdout.strip(), bool(dirty.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return "nogit", False


def riyadh_now():
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)


def version_tag(version):
    return version.replace(" beta", "b").replace(" ", "")


def release_name(version, platform, when, ref, dirty):
    """File name of one transfer copy: version, build time (Riyadh), Git commit, platform."""
    return "ThalathSmart-%s-%s-%s%s-%s.zip" % (version_tag(version), when.strftime("%Y%m%d-%H%M"), ref,
                                               "-dirty" if dirty else "", platform)


def build_info(version, when, ref, dirty):
    return {"version": version, "built_at_riyadh": when.strftime("%Y-%m-%d %H:%M"), "git_commit": ref,
            "uncommitted_code_changes": bool(dirty)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help='for example "0.9 beta"')
    ap.add_argument("--keep-user", default="ibrahim")
    ap.add_argument("--no-user", action="store_true",
                    help="keep no account at all (copies published on GitHub); the first start asks for a manager account")
    ap.add_argument("--update-from", action="append", default=[], metavar="ZIP",
                    help="an earlier full package; also build the small update package for installations of it (repeatable)")
    ap.add_argument("--model", default=None, help="Ollama model written to data/llm_settings.json (default: the current one)")
    args = ap.parse_args()
    model = args.model
    if not model:
        with open(os.path.join(ROOT, "data", "llm_settings.json"), encoding="utf-8") as f:
            cur = json.load(f)
        if cur.get("provider") != "ollama" or not cur.get("model"):
            raise SystemExit("the current provider is not Ollama; pass --model")
        model = cur["model"]
    tag = version_tag(args.version)
    when = riyadh_now()
    ref, dirty = git_ref()
    stage = os.path.join(OUT, "_stage")
    info = build_info(args.version, when, ref, dirty)
    counts = build_common(stage, args.version, None if args.no_user else args.keep_user, model, info)
    write(os.path.join(stage, "BUILD.json"), json.dumps(info, ensure_ascii=False, indent=1) + "\n")
    made, update = {}, None
    if args.update_from:
        name = "ThalathSmart-update-%s-%s-%s%s.zip" % (tag, when.strftime("%Y%m%d-%H%M"), ref, "-dirty" if dirty else "")
        update = build_update(stage, args.version, info, counts, args.update_from, os.path.join(OUT, name))
    for platform in ("windows", "mac"):
        for f in os.listdir(stage):  # start files of the other platform are removed before each zip
            if f.endswith((".bat", ".command", ".html")):
                os.remove(os.path.join(stage, f))
        if platform == "windows":
            for name, text in (("start_library.bat", WIN_START), ("start_this_computer_only.bat", WIN_LOCAL),
                               ("install_autostart.bat", WIN_AUTOSTART), ("remove_autostart.bat", WIN_REMOVE),
                               ("backup_now.bat", WIN_BACKUP)):
                write(os.path.join(stage, name), text, crlf=True)
            with open(os.path.join(ROOT, "scripts", "ensure_venv.bat"), encoding="utf-8") as f:
                write(os.path.join(stage, "scripts", "ensure_venv.bat"), f.read(), crlf=True)
            write(os.path.join(stage, "scripts", "daily_backup.bat"), WIN_DAILY, crlf=True)
            for gone in ("ensure_venv.sh", "install_daily_backup.command"):
                if os.path.exists(os.path.join(stage, "scripts", gone)):
                    os.remove(os.path.join(stage, "scripts", gone))
        else:
            for name, text in (("start_library.command", MAC_START), ("start_this_computer_only.command", MAC_LOCAL),
                               ("backup_now.command", MAC_BACKUP)):
                write(os.path.join(stage, name), text, executable=True)
            shutil.copy2(os.path.join(ROOT, "scripts", "ensure_venv.sh"), os.path.join(stage, "scripts", "ensure_venv.sh"))
            shutil.copy2(os.path.join(ROOT, "scripts", "install_daily_backup.command"),
                         os.path.join(stage, "scripts", "install_daily_backup.command"))
            for x in ("ensure_venv.sh", "install_daily_backup.command"):
                os.chmod(os.path.join(stage, "scripts", x), 0o755)
            if os.path.exists(os.path.join(stage, "scripts", "ensure_venv.bat")):
                os.remove(os.path.join(stage, "scripts", "ensure_venv.bat"))
            if os.path.exists(os.path.join(stage, "scripts", "daily_backup.bat")):
                os.remove(os.path.join(stage, "scripts", "daily_backup.bat"))
        write(os.path.join(stage, "README-AR.html"), guide(platform, args.version, model, counts))
        check_clean(stage)
        zip_path = os.path.join(OUT, release_name(args.version, platform, when, ref, dirty))
        made[platform] = (zip_path, zip_stage(stage, zip_path, f"ThalathSmart-{tag}"))
    shutil.rmtree(stage)
    print(json.dumps({"version": args.version, "model": model, "build": info, **counts,
                      "files": {k: {"path": os.path.relpath(v[0], ROOT), "mb": round(v[1] / 1e6, 1)} for k, v in made.items()},
                      "update": update},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
