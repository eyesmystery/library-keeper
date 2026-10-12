@echo off
rem Manar - make a database backup now (safe while the server is running).
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PYTHONUTF8=1"
call "%ROOT%scripts\ensure_venv.bat" || (pause & exit /b 1)
if not defined LIBRARY_KEEPER_BACKUP_MIRROR if exist "%USERPROFILE%\OneDrive\" set "LIBRARY_KEEPER_BACKUP_MIRROR=%USERPROFILE%\OneDrive\Thalath Smart Backups"
"%ROOT%backend\venv-win\Scripts\python.exe" "%ROOT%scripts\backup_db.py" --reason manual
pause
