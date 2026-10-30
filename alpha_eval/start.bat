@echo off
rem Start alpha_eval: checks Ollama, starts the detection queue runner, then opens the web UI.
setlocal
cd /d "%~dp0"
title alpha_eval
set KMP_DUPLICATE_LIB_OK=TRUE
set PYTHONUTF8=1

echo.
echo  alpha_eval - LookOut alpha-testing tool
echo.

rem --- Ollama (the Qwen AI checker). Detection runs are blocked on the Setup page while it is down. ---
curl -s -m 3 http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
  echo  [!] Ollama is NOT reachable at http://localhost:11434
  echo      Start it first:   ollama serve      ^(or run "Start LookOut.bat" one folder up^)
  echo      The tool still opens, but the Setup check will be red and runs stay blocked.
  echo.
) else (
  echo  Ollama is running.
)

rem --- Queue runner: runs queued clips one at a time. If one is already active it exits by itself. ---
start "alpha_eval queue runner" /min cmd /c "python worker.py daemon"

echo  Opening the app at http://localhost:8501 ...
python -m streamlit run app.py --server.address localhost --server.port 8501 --browser.gatherUsageStats false
endlocal
