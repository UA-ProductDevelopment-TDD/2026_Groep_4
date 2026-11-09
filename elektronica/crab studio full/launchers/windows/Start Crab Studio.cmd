@echo off
rem Starts Crab Studio: the crab simulator, crab trainer and robot connection.
rem Uses Bittle Studio's Python environment (no second install needed).
cd /d "%~dp0..\.."
set "PYEXE="
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
if not defined PYEXE if exist "..\Bittle_Studio-master\.venv\Scripts\python.exe" set "PYEXE=..\Bittle_Studio-master\.venv\Scripts\python.exe"
if not defined PYEXE (
  echo Bittle Studio's Python environment was not found. Run Setup in Bittle_Studio-master first.
  pause
  exit /b 1
)
echo Starting Crab Studio at http://127.0.0.1:8775
echo Keep this window open. Press Ctrl+C here to stop.
"%PYEXE%" app\launcher.py
pause
