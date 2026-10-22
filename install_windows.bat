@echo off
rem Double-click this to install or update RemiDex on Windows. It runs install_windows.py with Python (see README.md).
cd /d "%~dp0"
where py >nul 2>nul && goto py
where python >nul 2>nul && goto python
echo Python isn't installed. Get Python 3 from python.org (tick "Add python.exe to PATH"), then run this again.
goto end
:py
py -3 install_windows.py %*
goto end
:python
python install_windows.py %*
:end
pause
