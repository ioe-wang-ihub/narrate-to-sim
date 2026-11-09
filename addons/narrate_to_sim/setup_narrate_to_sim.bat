@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_stock_acceptance.ps1" %*
if errorlevel 1 goto failed
echo Ready. Start your CARLA server, then run start_narrate_to_sim.bat.
pause
exit /b 0
:failed
pause
exit /b 1
