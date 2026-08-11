@echo off
chcp 65001 >nul
if exist "%~dp0局域网共享.flag" (
  del "%~dp0局域网共享.flag" >nul
  echo 已关闭局域网共享，同事将无法访问（重启服务后生效）。
) else (
  echo 局域网共享当前已是关闭状态。
)
echo 提示：立即生效，无需重启服务。同事再次访问将提示无法访问。
pause
