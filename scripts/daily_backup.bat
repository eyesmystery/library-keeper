@echo off
rem Manar - daily backup run by the scheduled task (03:15). Same mirror folder as the start file.
setlocal
set "ROOT=%~dp0..\"
set "PYTHONUTF8=1"
if not defined LIBRARY_KEEPER_BACKUP_MIRROR if exist "%USERPROFILE%\OneDrive\" set "LIBRARY_KEEPER_BACKUP_MIRROR=%USERPROFILE%\OneDrive\Thalath Smart Backups"
"%ROOT%backend\venv-win\Scripts\python.exe" "%ROOT%scripts\backup_db.py" --reason daily
