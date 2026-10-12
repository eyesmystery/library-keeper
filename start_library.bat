@echo off
rem Manar - start the server for the library: this computer and phones on the same Wi-Fi.
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
echo Manar - the server is starting.
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
