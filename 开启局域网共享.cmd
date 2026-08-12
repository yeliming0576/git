@echo off
if exist "%~dp0LAN_SHARE.flag" (
  echo LAN_SHARE已是开启状态。
) else (
  type nul > "%~dp0LAN_SHARE.flag"
  echo 已开启LAN_SHARE。
)
echo 正在检查防火墙规则...
netsh advfirewall firewall show rule name=StockPickerWeb8765 >nul 2>&1
if not errorlevel 1 (
  echo 防火墙规则已存在。
) else (
  echo 需要添加防火墙放行规则（8765端口），将弹出管理员授权窗口，请点【是】。
  powershell -NoProfile -Command "Start-Process cmd -ArgumentList '/c','netsh advfirewall firewall add rule name=StockPickerWeb8765 dir=in action=allow protocol=TCP localport=8765' -Verb RunAs -Wait"
  netsh advfirewall firewall show rule name=StockPickerWeb8765 >nul 2>&1
  if not errorlevel 1 (
    echo 防火墙放行成功。
  ) else (
    echo 防火墙规则添加失败：请右键本脚本以管理员身份运行，或手动放行 8765 端口。
  )
)
echo 提示：共享立即生效，同事现在即可通过 http://本机IP:8765/ 访问。
pause
