@echo off
rem Manar - start for THIS computer only (no phones). Use it for checking after an update.
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
