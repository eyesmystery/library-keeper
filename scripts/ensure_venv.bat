@echo off
rem Creates backend\venv-win with the pinned requirements if it does not exist yet (first run on Windows).
rem A separate folder from the Mac venv (backend/venv), so both can live in a shared project folder.
set "ROOT=%~dp0.."
set "VENV=%ROOT%\backend\venv-win"
if exist "%VENV%\Scripts\python.exe" exit /b 0
echo Preparing Python for the first time (backend\venv-win)...
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo Python 3 is not installed. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  exit /b 1
)
%PY% -m venv "%VENV%" || (echo Could not create the Python environment. & exit /b 1)
"%VENV%\Scripts\python.exe" -m pip install --quiet -r "%ROOT%\backend\requirements.txt" || (echo Could not install the requirements - an internet connection is needed the first time. & exit /b 1)
exit /b 0
