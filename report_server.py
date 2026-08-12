# -*- coding: utf-8 -*-
"""
每日量化报告本地服务
  - 本机访问: http://127.0.0.1:8765/
  - 局域网共享由开关控制（双击 开启局域网共享.cmd / 关闭局域网共享.cmd，即时生效）：
    关闭时同事访问会收到 403，只有本机可用；开启后同事可通过 http://<本机IP>:8765/ 访问。
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
# 共享开关：存在 LAN_SHARE.flag 时允许非本机访问；关闭时逐请求拦截（即时生效）
# 兼容旧文件名 局域网共享.flag（早期版本创建）
LAN_FLAG = os.path.join(BASE, "LAN_SHARE.flag")
LEGACY_LAN_FLAG = os.path.join(BASE, "局域网共享.flag")
HOST = "0.0.0.0"
PORT = 8765
INTERVAL = 300   # 自动刷新间隔（秒），默认 5 分钟
LOCK = threading.Lock()   # 多人同时刷新/保存/编辑自选股时互斥，避免写坏文件
PID_FILE = os.path.join(BASE, "网页服务.pid")


def _local_ips():
    """本机所有网卡 IP + 回环地址：共享关闭时仅这些来源可访问"""
    ips = {"127.0.0.1", "::1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if ip and not ip.startswith("169.254"):
                ips.add(ip)
    except Exception:
        pass
    return ips


LOCAL_IPS = _local_ips()


def sharing_allowed(client_ip):
    """共享开关实时判断：flag 存在=共享；否则仅本机 IP 可访问"""
    if os.path.exists(LAN_FLAG) or os.path.exists(LEGACY_LAN_FLAG):
        return True
    return client_ip in LOCAL_IPS


def _html_escape(s):
    import html as _h
    return _h.escape(str(s), quote=False)


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
    d = os.path.join(BASE, "报告归档", "每日")
    if not os.path.isdir(d):
        return None
    files = [f for f in os.listdir(d)
             if f.startswith("每日量化选股报告_") and f.endswith(".html")]
    if not files:
        return None
    return os.path.join(d, sorted(files)[-1])


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
        if not sharing_allowed(self.client_address[0]):
            self._deny_sharing()
            return
        if self.path.startswith("/refresh"):
            self._refresh()
            return
        if self.path.startswith("/watchlist"):
            self._json({"ok": True, "codes": read_watchlist()})
            return
        if self.path.startswith("/researchfile"):
            self._research_file()
            return
        if self.path.startswith("/research"):
            self._research()
            return
        if self.path.startswith("/bottleneck"):
            self._bottleneck()
            return
        if self.path in ("/", "/index.html"):
            self._serve_report()
            return
        self.send_error(404, "Not Found")

    def do_POST(self):
        if not sharing_allowed(self.client_address[0]):
            self._deny_sharing()
            return
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

    def _deny_sharing(self):
        """局域网共享关闭时对非本机请求返回 403（对同事显示中性提示）"""
        body = "网络连接中，请稍后".encode("utf-8")
        self.send_response(403)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self):
        from urllib.parse import parse_qs, urlsplit
        return parse_qs(urlsplit(self.path).query)

    def _research(self):
        """GET /research?code=600519 → 生成并展示研究任务包 + 已生成报告入口"""
        try:
            from urllib.parse import unquote
            code = (self._query().get("code") or [""])[0].strip()
            if not (code.isdigit() and len(code) == 6):
                self._json({"ok": False, "msg": "请输入6位股票代码，如 /research?code=600519"}, status=400)
                return
            import research_data
            pack = research_data.get_pack(code)
            task = research_data.build_task_pack(pack)
        except Exception as e:
            print("[服务] 研究任务包生成失败:", e)
            self._json({"ok": False, "msg": f"数据获取失败：{e}"}, status=500)
            return
        d = os.path.join(BASE, "报告归档", "研究", code)
        reports = []
        if os.path.isdir(d):
            reports = sorted(
                f for f in os.listdir(d)
                if f.endswith(".html") and "研究报告" in f)
        report_links = "".join(
            f"<li><a href='/researchfile?code={code}&file={unquote(f)}'>{f}</a></li>"
            for f in reports) or "<li>暂无已生成报告（把任务包交给 Codex 执行后生成）</li>"
        body = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>研究任务包 {code}</title>
<style>
body{{background:#f5f6f8;color:#1f2937;font-family:"Microsoft YaHei",sans-serif;padding:28px;}}
.wrap{{max-width:1100px;margin:0 auto;}}
h1{{font-size:22px;}} .sub{{color:#6b7280;font-size:13px;margin:8px 0 14px;}}
.card{{background:#fff;border:1px solid #e6e9ef;border-radius:12px;padding:16px 20px;margin-bottom:16px;}}
button{{background:#2563eb;color:#fff;border:none;border-radius:999px;padding:8px 18px;cursor:pointer;font-family:inherit;}}
textarea{{width:100%;height:640px;border:1px solid #dbe2ea;border-radius:10px;padding:12px;font-size:12px;font-family:Consolas,monospace;box-sizing:border-box;}}
ul{{line-height:1.9;}} a{{color:#2563eb;}}
</style></head><body><div class="wrap">
<h1>研究任务包：{_html_escape(code)}</h1>
<div class="sub">把下面内容复制给 Codex，并附言“按 investment-team + investment-research 执行深度研究”。任务包数据自动缓存当天，不重复抓取。</div>
<div class="card"><button onclick="var t=document.getElementById('task');t.select();document.execCommand('copy');this.textContent='已复制 ✓';">复制任务包</button></div>
<div class="card"><textarea id="task" readonly>{_html_escape(task)}</textarea></div>
<div class="card"><b>已生成研究报告</b><ul>{report_links}</ul>
<div class="sub" style="margin-top:6px;">报告存放：报告归档\\研究\\{code}\\</div></div>
</div></body></html>"""
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _research_file(self):
        """GET /researchfile?code=600519&file=xx.html → 查看已生成研究报告"""
        try:
            from urllib.parse import unquote
            q = self._query()
            code = (q.get("code") or [""])[0].strip()
            fname = (q.get("file") or [""])[0].strip()
            fname = os.path.basename(unquote(fname))
            path = os.path.join(BASE, "报告归档", "研究", code, fname)
            if not (code.isdigit() and len(code) == 6) or not fname.endswith(".html") \
                    or not os.path.isfile(path):
                self.send_error(404, "Not Found")
                return
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            self.send_error(500, "Server Error")

    def _bottleneck(self):
        """GET /bottleneck[?file=xx.html] → 查看紫苏叶瓶颈机会看板（默认最新一份）"""
        try:
            from urllib.parse import unquote
            d = os.path.join(BASE, "紫苏叶选股", "输出")
            if not os.path.isdir(d):
                self._json({"ok": False, "msg": "暂无紫苏叶看板（先运行 bottleneck_picker.py）"}, status=404)
                return
            files = sorted(f for f in os.listdir(d)
                           if f.endswith(".html") and "瓶颈机会看板" in f)
            if not files:
                self._json({"ok": False, "msg": "暂无紫苏叶看板（先运行 bottleneck_picker.py）"}, status=404)
                return
            q = self._query()
            fname = (q.get("file") or [""])[0].strip()
            if fname:
                fname = os.path.basename(unquote(fname))
                if fname not in files:
                    self.send_error(404, "Not Found")
                    return
            else:
                fname = files[-1]
            with open(os.path.join(d, fname), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            self.send_error(500, "Server Error")

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


class Server(ThreadingHTTPServer):
    """关闭 SO_REUSEADDR，避免 Windows 下多个服务实例同时绑定同一端口。"""
    allow_reuse_address = False


def port_in_use(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def main():
    parse_port()
    no_browser = "--no-browser" in sys.argv
    if port_in_use(PORT):
        print(f"端口 {PORT} 已被占用，服务可能已在运行。")
        if not no_browser:
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return
    try:
        server = Server((HOST, PORT), Handler)
    except OSError:
        print(f"端口 {PORT} 已被占用，服务可能已在运行。")
        if not no_browser:
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return
    ip = lan_ip()
    print("=" * 52)
    print(" 每日量化报告服务已启动")
    print(" 本机访问:  http://127.0.0.1:" + str(PORT) + "/")
    sharing = "开" if os.path.exists(LAN_FLAG) else "关"
    print(f" 局域网共享: {sharing}（双击 开启局域网共享.cmd / 关闭局域网共享.cmd 即时切换）")
    if sharing == "开" and ip:
        print(" 同事访问: http://" + ip + ":" + str(PORT) + "/")
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
