@echo off
chcp 936 >nul
set "PIDFILE=%~dp0网页服务.pid"
if not exist "%PIDFILE%" (
  echo 网页服务未在运行（或已停止）
  pause
  exit /b 0
)
set /p SERVER_PID=<"%PIDFILE%"
taskkill /PID %SERVER_PID% /F >nul 2>&1
del "%PIDFILE%" >nul 2>&1
echo 网页服务已停止
pause