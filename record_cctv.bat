@echo off
REM ============================================================
REM  LookOut - automatic CCTV recorder (self-restarting)
REM  Launched at login (hidden) by start_cctv_hidden.vbs in the
REM  Windows Startup folder. Records continuously while the camera
REM  is connected, into
REM    C:\Users\LENOVO THINKPAD T14s\Desktop\Capstone\cctv records\CCTV\
REM
REM  To change stream / retention, edit the record_camera line below:
REM    /102 = sub-stream (640x360, lighter)   /101 = main (2560x1440)
REM    --retention-days 7  = delete footage older than 7 days (0 = keep all)
REM  NOTE: %% in the password are doubled because this is a .bat file.
REM ============================================================

cd /d "C:\Users\LENOVO THINKPAD T14s\Desktop\Capstone\lookout_backend"

:loop
echo [%date% %time%] starting recorder >> "..\cctv records\recorder.log"
"..\venv\Scripts\python.exe" manage.py record_camera --source "rtsp://admin:%%5BMTA%%5D2358@192.168.1.64:554/Streaming/Channels/102" --camera CCTV --retention-days 7  >> "..\cctv records\recorder.log" 2>&1
REM recorder only exits on a crash (it reconnects on camera drop); wait then restart.
timeout /t 5 /nobreak >nul
goto loop
