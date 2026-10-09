@echo off
rem Thalath Smart - undo install_autostart.bat.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-Item -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('Startup')) 'Thalath Smart.lnk')"
schtasks /Delete /F /TN "ThalathSmartBackup" >nul 2>&1
echo Removed: automatic start and the daily backup task.
pause
