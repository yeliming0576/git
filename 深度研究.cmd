@echo off
chcp 65001 >nul
call "%~dp0_python.cmd"
if errorlevel 1 exit /b 1
if "%~1"=="" (
  set /p CODE=请输入6位股票代码（如 600519）:
) else (
  set "CODE=%~1"
)
"%PYEXE%" "%~dp0research_data.py" %CODE%
echo.
echo 任务包已生成：报告归档\研究\%CODE%\研究任务包_%CODE%_*.md
echo 请把任务包内容发给 Codex，并附言：按 investment-team + investment-research 执行深度研究。
echo 或打开网页后访问：http://127.0.0.1:8765/research?code=%CODE%
pause
