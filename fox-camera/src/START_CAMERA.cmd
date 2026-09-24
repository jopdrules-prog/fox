@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title FOX Camera Assist
if not exist "bootstrap.py" goto missing
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" bootstrap.py
  goto end
)
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  py -3.12 bootstrap.py
  goto end
)
py -3 -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,14)" >nul 2>&1
if not errorlevel 1 (
  py -3 bootstrap.py
  goto end
)
python -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,14)" >nul 2>&1
if not errorlevel 1 (
  python bootstrap.py
  goto end
)
echo Python 3.12 was not found. Please send a photo of this window.
goto end
:missing
echo Extract ALL files from this ZIP before running START_CAMERA.cmd.
:end
pause
