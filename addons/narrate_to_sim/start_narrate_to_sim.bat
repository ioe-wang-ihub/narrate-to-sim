@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_narrate_to_sim.ps1" %*
if errorlevel 1 pause
