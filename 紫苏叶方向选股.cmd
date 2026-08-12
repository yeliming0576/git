@echo off
call "%~dp0_python.cmd"
if errorlevel 1 exit /b 1
set /p DIR=请输入研究方向（如 AI算力 / 白酒 / 固态电池）:
"%PYEXE%" "%~dp0紫苏叶选股\direction_picker.py" %DIR%
echo.
echo 提示：已有底稿会直接生成看板；没有底稿会生成研究任务单，发给 Codex 生成后再跑一次即可。
pause
