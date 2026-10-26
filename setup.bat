@echo off
REM One-time setup: creates .venv with Python 3.13 and installs dependencies.
cd /d "%~dp0"
py -3.13 -m venv .venv || (echo Python 3.13 not found. Install it from python.org or the Microsoft Store. & pause & exit /b 1)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-optional.txt || (pause & exit /b 1)
echo.
echo Setup complete. Run demo.bat to see the overlay with simulated data.
pause
