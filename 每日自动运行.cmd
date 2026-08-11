@echo off
set "LOG=%~dp0自动运行日志.txt"
echo [%date% %time%] ====== 开始自动运行 ====== >> "%LOG%"
call "%~dp0_python.cmd"
if errorlevel 1 (
  echo [%date% %time%] 错误: 找不到 Python >> "%LOG%"
  exit /b 1
)
echo [%date% %time%] 生成每日量化报告... >> "%LOG%"
"%PYEXE%" "%~dp0daily_report.py" >> "%LOG%" 2>&1
echo [%date% %time%] 运行组合模拟盘... >> "%LOG%"
"%PYEXE%" "%~dp0paper_trade.py" >> "%LOG%" 2>&1
echo [%date% %time%] ====== 自动运行完成 ====== >> "%LOG%"