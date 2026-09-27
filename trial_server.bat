@echo off
rem Double-click to start Library Keeper in TRIAL mode on Windows: phones on the same Wi-Fi can open it
rem by scanning the QR code (admin page - Settings). Mac: trial_server.command
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
netstat -ano | findstr /R /C:":8802 .*LISTENING" >nul && (
  echo The server is already running. Close its window first, then start trial mode again.
  pause & exit /b 1
)
call "%ROOT%scripts\ensure_venv.bat" || (pause & exit /b 1)
set "PYW=%ROOT%backend\venv-win\Scripts\python.exe"
if not exist "%ROOT%data\library.db" "%PYW%" "%ROOT%scripts\catalog_snapshot.py" build || if not exist "%ROOT%data\library.db" (pause & exit /b 1)
"%PYW%" "%ROOT%scripts\backup_db.py" --reason trial-start || echo Warning: the backup failed (see backups\backup_log.txt).
echo Library Keeper - TRIAL MODE (phones on the same Wi-Fi)
echo The phone address is printed below when the server starts, and shown as a QR code in
echo the admin page: Settings - trial QR code.
echo If Windows asks whether Python may communicate on networks, allow it on PRIVATE networks.
echo Keep this computer awake and on the Wi-Fi during the trial. To stop: close this window.
start "" "http://127.0.0.1:8802/admin"
cd /d "%ROOT%backend"
set "LIBRARY_KEEPER_HOST=0.0.0.0"
"%PYW%" app.py
pause
