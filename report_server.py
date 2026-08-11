# -*- coding: utf-8 -*-
"""
每日量化报告本地服务（重构版：支持局域网多人同时访问）
  - 本机访问:   http://127.0.0.1:8765/
  - 局域网访问: http://<本机IP>:8765/   （同一局域网内其他人用浏览器打开即可）
  - 报告页的【刷新数据】会重新抓取行情并生成最新报告；
    多人同时刷新/保存时程序会自动排队（互斥锁），不会写坏文件。
用法: 双击 启动报告服务.cmd，或 python report_server.py [--no-browser] [--port 8765]
"""
import os
import sys
import json
import socket
import time
import datetime
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
HOST = "0.0.0.0"          # 监听所有网卡，局域网可访问
PORT = 8765
INTERVAL = 300   # 自动刷新间隔（秒），默认 5 分钟
LOCK = threading.Lock()   # 多人同时刷新/保存/编辑自选股时互斥，避免写坏文件
PID_FILE = os.path.join(BASE, "网页服务.pid")


def parse_port():
    global PORT, INTERVAL
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            try:
                PORT = int(sys.argv[i + 1])
            except ValueError:
                pass
        if arg == "--interval" and i + 1 < len(sys.argv):
            try:
                INTERVAL = int(sys.argv[i + 1])
            except ValueError:
                pass


def schedule_auto_refresh(interval):
    """后台线程：每 interval 秒自动重新抓取行情并生成最新报告"""
    last_state = None

    def market_open():
        """A股交易时段（含收盘缓冲）：工作日 9:15~11:35 或 12:55~15:10"""
        now = datetime.datetime.now()
        if now.weekday() >= 5:
            return False
        t = now.strftime("%H:%M")
        return ("09:15" <= t <= "11:35") or ("12:55" <= t <= "15:10")

    def worker():
        nonlocal last_state
        while True:
            time.sleep(interval)
            open_now = market_open()
            if open_now != last_state:
                print("[服务] 已进入交易时段，自动刷新开启" if open_now
                      else "[服务] 已收盘/休市，自动刷新暂停（下一交易日 09:15 恢复）")
                last_state = open_now
            if not open_now:
                continue
            try:
                with LOCK:
                    import daily_report
                    daily_report.main()
                print(f"[服务] {datetime.datetime.now():%H:%M:%S} 自动刷新完成")
            except Exception as e:
                print("[服务] 自动刷新失败:", e)
    threading.Thread(target=worker, daemon=True).start()


def lan_ip():
    """获取本机局域网 IP（供其他人访问）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def latest_report():
    files = [f for f in os.listdir(BASE)
             if f.startswith("每日量化选股报告_") and f.endswith(".html")]
    if not files:
        return None
    return os.path.join(BASE, sorted(files)[-1])


def watchlist_file():
    return os.path.join(BASE, "自选股.txt")


def read_watchlist():
    path = watchlist_file()
    if not os.path.exists(path):
        return []
    codes = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.isdigit() and len(line) == 6:
                codes.append(line)
    return codes


def write_watchlist(codes):
    with open(watchlist_file(), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(set(codes))) + ("\n" if codes else ""))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[服务]", fmt % args)

    def do_GET(self):
        if self.path.startswith("/refresh"):
            self._refresh()
            return
        if self.path.startswith("/watchlist"):
            self._json({"ok": True, "codes": read_watchlist()})
            return
        if self.path in ("/", "/index.html"):
            self._serve_report()
            return
        self.send_error(404, "Not Found")

    def do_POST(self):
        if self.path.startswith("/refresh"):
            self._refresh()
            return
        if self.path.startswith("/save"):
            self._save()
            return
        if self.path.startswith("/addstock"):
            self._edit_watchlist(add=True)
            return
        if self.path.startswith("/removestock"):
            self._edit_watchlist(add=False)
            return
        self.send_error(404, "Not Found")

    def _refresh(self):
        with LOCK:  # 多人同时刷新时排队执行，避免重复/写坏
            try:
                import daily_report
                daily_report.main()
                self._json({"ok": True, "msg": "刷新完成"})
            except Exception as e:
                print("[服务] 刷新失败:", e)
                self._json({"ok": False, "msg": str(e)}, status=500)

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _save(self):
        with LOCK:
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length).decode("utf-8", errors="replace")
                path = latest_report()
                if not path:
                    self._json({"ok": False, "msg": "尚未生成报告"}, status=404)
                    return
                with open(path, "w", encoding="utf-8") as f:
                    f.write(body)
                print(f"[服务] 已保存修改 -> {os.path.basename(path)} ({len(body)} 字节)")
                self._json({"ok": True, "msg": "已保存"})
            except Exception as e:
                print("[服务] 保存失败:", e)
                self._json({"ok": False, "msg": str(e)}, status=500)

    def _edit_watchlist(self, add):
        with LOCK:
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                code = self.rfile.read(length).decode("utf-8", errors="replace").strip()
                if not (code.isdigit() and len(code) == 6):
                    self._json({"ok": False, "msg": "请输入6位股票代码"}, status=400)
                    return
                codes = read_watchlist()
                if add:
                    if code in codes:
                        self._json({"ok": True, "msg": "该代码已在自选中"})
                        return
                    codes.append(code)
                    write_watchlist(codes)
                    try:
                        # 重新生成报告，让新自选股的量化页立即出现
                        import daily_report
                        daily_report.main()
                        self._json({"ok": True, "msg": "已添加并重新生成"})
                    except Exception as e:
                        print("[服务] 添加后重新生成报告失败:", e)
                        self._json({"ok": True, "msg": f"已添加（报告生成失败：{e}，可稍后刷新）"})
                else:
                    if code not in codes:
                        self._json({"ok": True, "msg": "该代码不在自选中"})
                        return
                    codes.remove(code)
                    write_watchlist(codes)
                    try:
                        import db
                        db.purge_code(code)
                        print(f"[服务] 已清理 {code} 的本地缓存数据")
                    except Exception:
                        pass
                    try:
                        # 重新生成报告，让移除后的量化页立即消失
                        import daily_report
                        daily_report.main()
                        self._json({"ok": True, "msg": "已移除并重新生成"})
                    except Exception as e:
                        print("[服务] 移除后重新生成报告失败:", e)
                        self._json({"ok": True, "msg": f"已移除（报告生成失败：{e}，可稍后刷新）"})
            except Exception as e:
                print("[服务] 自选股更新失败:", e)
                self._json({"ok": False, "msg": str(e)}, status=500)

    def _serve_report(self):
        with LOCK:
            path = latest_report()
            if not path:
                self._json({"ok": False, "msg": "尚未生成报告，请先刷新"}, status=404)
                return
            with open(path, "rb") as f:
                body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parse_port()
    no_browser = "--no-browser" in sys.argv
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        print(f"端口 {PORT} 已被占用，服务可能已在运行。")
        if not no_browser:
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return
    ip = lan_ip()
    print("=" * 52)
    print(" 每日量化报告服务已启动（支持局域网多人同时访问）")
    print(" 本机访问:  http://127.0.0.1:" + str(PORT) + "/")
    if ip:
        print(" 局域网访问: http://" + ip + ":" + str(PORT) + "/")
        print(" 提示: 首次启动若防火墙弹窗，请选择【允许访问】")
    print(f" 自动刷新: 每 {INTERVAL} 秒重新抓取数据（可用 --interval 秒 修改）")
    print(" 在报告页点击【刷新数据】即可重新抓取行情")
    print(" 关闭本窗口即停止服务")
    print("=" * 52)
    schedule_auto_refresh(INTERVAL)
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    if not no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}/")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("服务已停止")
    finally:
        try:
            os.remove(PID_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    main()
