@echo off
rem Manar - run once: start the server automatically when this Windows user signs in,
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
