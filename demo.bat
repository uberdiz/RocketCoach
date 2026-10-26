@echo off
REM Overlay + coaching on SIMULATED telemetry (no Rocket League needed). Ctrl+Alt+Q quits.
cd /d "%~dp0"
.venv\Scripts\python.exe -m rocketcoach run --source mock --speed 1
