@echo off
cd /d "%~dp0"
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
  set "PY=py"
  where py >nul 2>nul
  if errorlevel 1 (
    echo Python not found. Please install Python and check "Add to PATH".
    pause
    exit /b 1
  )
)
echo Running revise_nc.py ...
%PY% revise_nc.py
echo.
echo Finished. Press any key to close this window.
pause