@echo off
REM Starts the coach on its own. It follows ANY RLBot match, including ones you start from the RLBot window.
REM Starting it again replaces the running coach. Close this window (or press Ctrl+Alt+Q) to quit.
cd /d "%~dp0"
title Rocket Coach (close this window to stop the coach)
echo Rocket Coach is running. Close this window to stop it.
.venv\Scripts\python.exe -m rocketcoach run --source rlbot
