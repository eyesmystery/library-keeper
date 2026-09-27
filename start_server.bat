@echo off
rem Double-click to start Library Keeper on Windows (this computer only). Mac: start_server.command
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
netstat -ano | findstr /R /C:":8802 .*LISTENING" >nul && (
  echo The server is already running. Opening the browser...
  start "" "http://127.0.0.1:8802" & exit /b 0
)
call "%ROOT%scripts\ensure_venv.bat" || (pause & exit /b 1)
set "PYW=%ROOT%backend\venv-win\Scripts\python.exe"
if not exist "%ROOT%data\library.db" "%PYW%" "%ROOT%scripts\catalog_snapshot.py" build || if not exist "%ROOT%data\library.db" (pause & exit /b 1)
"%PYW%" "%ROOT%scripts\backup_db.py" --reason server-start || echo Warning: the backup failed (see backups\backup_log.txt).
echo Library Keeper - http://127.0.0.1:8802   (this computer only)
echo To stop the server: close this window or press Ctrl+C.
start "" "http://127.0.0.1:8802"
cd /d "%ROOT%backend"
"%PYW%" app.py
pause
