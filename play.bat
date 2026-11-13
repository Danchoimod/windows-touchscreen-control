@echo off
setlocal
cd /d "%~dp0"
set "TOUCHPLAY_PYTHON="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "TOUCHPLAY_PYTHON=py -3"
if not defined TOUCHPLAY_PYTHON (
  for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do (
    if exist "%%~fD\python.exe" set TOUCHPLAY_PYTHON="%%~fD\python.exe"
  )
)
if not defined TOUCHPLAY_PYTHON (
  echo Python 3 was not found. Install Python 3 and try again.
  pause
  exit /b 1
)
%TOUCHPLAY_PYTHON% -c "import PySide6" 2>nul
if errorlevel 1 (
  echo Installing PySide6...
  %TOUCHPLAY_PYTHON% -m pip install -r requirements.txt
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
%TOUCHPLAY_PYTHON% main.py %*
if errorlevel 1 pause
