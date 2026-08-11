@echo off
chcp 65001 >nul
if exist "%~dp0局域网共享.flag" (
  echo 局域网共享已是开启状态。
) else (
  type nul > "%~dp0局域网共享.flag"
  echo 已开启局域网共享。
)
echo 提示：立即生效，无需重启服务。同事现在即可通过 http://本机IP:8765/ 访问。
pause
