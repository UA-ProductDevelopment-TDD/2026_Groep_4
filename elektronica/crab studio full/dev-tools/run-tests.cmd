@echo off
rem Runs the automated checks: Python tests for the simulator and API, and a syntax check of every web file.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Run launchers\windows\Setup.cmd first.
  pause
  exit /b 1
)
echo === Python tests ===
".venv\Scripts\python.exe" -m unittest discover -s app\tests -t app -v
if errorlevel 1 goto failed
where node >nul 2>&1
if errorlevel 1 (
  echo ^(Node.js not found: skipping the web syntax check^)
  goto done
)
echo === Web syntax check ===
for /r app\web %%f in (*.js) do (
  node --check "%%f"
  if errorlevel 1 goto failed
)
:done
echo.
echo All checks passed.
pause
exit /b 0
:failed
echo.
echo Some checks FAILED. See the messages above.
pause
exit /b 1