@echo off
rem Start LookOut: Ollama (if it is not already running), the Django server and the web app,
rem each in its own window, then open the browser once both answer.
rem Run this from the LookOut folder (the one that contains lookout_backend and lookout).
setlocal
cd /d "%~dp0"
title Start LookOut

echo.
echo  Starting LookOut...
echo.

rem --- Ollama (the local AI checker) ---
curl -s -o nul -m 2 http://localhost:11434/api/version
if errorlevel 1 (
  echo  Starting Ollama...
  start "LookOut - Ollama" /min cmd /k "ollama serve"
) else (
  echo  Ollama is already running.
)

rem --- Django (the server). KMP_DUPLICATE_LIB_OK avoids a library clash between PyTorch and NumPy. ---
start "LookOut - Server" /d "%~dp0lookout_backend" cmd /k "set KMP_DUPLICATE_LIB_OK=TRUE&& python manage.py runserver 0.0.0.0:8000"

rem --- The web app ---
start "LookOut - Web" /d "%~dp0lookout" cmd /k "npm run dev"

rem --- Wait until both respond (up to about 2 minutes) ---
set /a tries=0
:wait_server
curl -s -o nul -m 2 http://localhost:8000/api/violation-types/
if not errorlevel 1 goto wait_web
set /a tries+=1
if %tries% GEQ 60 goto timeout
timeout /t 2 /nobreak >nul
goto wait_server

:wait_web
set /a tries=0
:wait_web_loop
curl -s -o nul -m 2 http://localhost:5173/
if not errorlevel 1 goto ready
set /a tries+=1
if %tries% GEQ 60 goto timeout
timeout /t 2 /nobreak >nul
goto wait_web_loop

:ready
echo  LookOut is ready. Opening the browser...
start "" http://localhost:5173/
echo.
echo  Leave the Server and Web windows open while you use LookOut.
echo  To shut everything down, run "Stop LookOut.bat".
timeout /t 5 >nul
exit /b 0

:timeout
echo.
echo  LookOut did not start in time. Check the Server and Web windows for an error message.
pause
exit /b 1
