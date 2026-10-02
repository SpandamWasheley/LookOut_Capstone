@echo off
rem Stop LookOut: the detectors, the Django server and the web app. Ollama is left running
rem (other programs may use it); close its window yourself if you want it stopped too.
setlocal
title Stop LookOut

echo.
echo  Stopping LookOut...

rem Detectors first (live monitoring and any test run)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'manage.py watch_' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

rem The Django server (port 8000) and the web app (port 5173), with everything they started
for %%P in (8000 5173) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do (
    taskkill /PID %%I /T /F >nul 2>&1
  )
)

rem Their now-empty windows
taskkill /FI "WINDOWTITLE eq LookOut - Server*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq LookOut - Web*" /F >nul 2>&1

echo  Done.
timeout /t 3 >nul
exit /b 0
