@echo off
echo 局域网共享已常开：报告服务没有关闭开关，同事随时可访问。
echo 本脚本只负责放行 Windows 防火墙的 8765 端口（同事打不开时才需要跑一次）。
echo.
echo 正在检查防火墙规则...
netsh advfirewall firewall show rule name=StockPickerWeb8765 >nul 2>&1
if not errorlevel 1 (
  echo 防火墙规则已存在，同事现在即可通过 http://本机IP:8765/ 访问。
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
echo.
echo 本机局域网地址：启动报告服务后，服务窗口里会显示「同事访问: http://本机IP:8765/」。
pause
