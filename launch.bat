@echo off
setlocal
title Trading Journal AI Launcher
cd /d "%~dp0"

if not exist "%~dp0backend\main.py" (
    echo Could not find the backend folder next to launch.bat.
    echo.
    echo Extract the whole zip first: right-click the zip, choose "Extract All...",
    echo then open the extracted folder and double-click launch.bat there.
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0frontend\node_modules" (
    echo The app is not installed yet. Double-click setup.bat first, then launch.bat again.
    echo.
    pause
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 if exist "%ProgramFiles%\nodejs\npm.cmd" set "PATH=%ProgramFiles%\nodejs;%PATH%"

set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

echo Starting Trading Journal AI...
echo.

REM /D sets each window's folder, so paths with spaces work
start "Trading Journal AI - Backend" /D "%~dp0backend" cmd /k ""%PY%" -m uvicorn main:app --reload --port 8010"

timeout /t 2 /nobreak >nul
start "Trading Journal AI - Frontend" /D "%~dp0frontend" cmd /k "set PORT=3010&& npm start"

echo Backend starting on http://localhost:8010
echo Frontend starting on http://localhost:3010 (your browser opens it when ready)
echo.
echo Keep the two new windows open while you use the journal. Close them to stop it.
pause
