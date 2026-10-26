@echo off
REM Starts an offline 2v2 through RLBot v5 with the coach attached (needs RLBot v5 installed, Rocket League closed).
cd /d "%~dp0"
.venv\Scripts\python.exe -m rocketcoach launch-match --match rlbot\match_2v2.toml
pause
