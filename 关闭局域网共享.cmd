@echo off
if exist "%~dp0LAN_SHARE.flag" (
  del "%~dp0LAN_SHARE.flag" >nul
  echo 已关闭LAN_SHARE，同事将无法访问（立即生效）。
) else (
  echo LAN_SHARE当前已是关闭状态。
)
pause
