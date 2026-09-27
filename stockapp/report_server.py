# -*- coding: utf-8 -*-
"""
每日量化报告本地服务
  - 本机访问: http://127.0.0.1:8765/
  - 局域网共享常开：同事可直接通过 http://<本机IP>:8765/ 访问，没有关闭开关。
    （若同事打不开，多半是防火墙没放行 8765，双击 局域网放行防火墙.cmd 放行即可。）
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

from stockapp import config as C

from stockapp.app_paths import BASE
HOST = C.HOST
PORT = C.PORT
INTERVAL = C.INTERVAL   # 自动刷新间隔（秒），默认 5 分钟
LOCK = threading.Lock()   # 多人同时刷新/保存/编辑自选股时互斥，避免写坏文件
PID_FILE = os.path.join(BASE, "网页服务.pid")


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
    """后台线程：每 interval 秒自动重新抓取行情并生成最新报告（不区分交易时段）"""
    def worker():
        while True:
            time.sleep(interval)
            try:
                with LOCK:
                    from stockapp import daily_report
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


LOWVAL_DIR = os.path.join(BASE, "报告归档", "低值复苏")


def _pct_txt(v):
    return "—" if v is None else f"{v:+.2f}%"


def _save_lowval_report(html_body, result):
    """低值复苏选股：生成即存档（报告归档/低值复苏/低值复苏选股_日期.html + .json）"""
    try:
        os.makedirs(LOWVAL_DIR, exist_ok=True)
        stem = "低值复苏选股_" + datetime.date.today().strftime("%Y%m%d")
        with open(os.path.join(LOWVAL_DIR, stem + ".html"), "w", encoding="utf-8") as f:
            f.write(html_body)
        with open(os.path.join(LOWVAL_DIR, stem + ".json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        try:
            from stockapp import archive
            archive.archive_old_reports(LOWVAL_DIR)
        except Exception:
            pass
        return os.path.join(LOWVAL_DIR, stem + ".html")
    except Exception as e:
        print("[服务] 低值复苏存档失败:", e)
        return None


def _lowval_page(result, lo, hi, presets):
    """低值复苏选股单页 HTML；result 为 None 时只渲染说明与表单"""
    result = result or {}
    meta = result.get("meta") or {}
    picks = result.get("picks") or []
    has_run = bool(meta.get("generated_at"))
    inp = "、".join(meta.get("sectors_input") or []) or "（未指定，全市场活跃池）"
    board_txt = "、".join(b["name"] for b in (meta.get("boards") or [])) or "—"
    preset_links = "".join(
        f"<a class='pre' href='/lowval?sectors={_html_escape(s)}'>{_html_escape(s)}</a>"
        for s in presets)
    rows = ""
    for i, p in enumerate(picks, 1):
        tr = p.get("trend") or {}
        ma = "—"
        if tr.get("above_ma20") is not None:
            ma = "站上MA20" if tr["above_ma20"] else "MA20下方"
            if tr.get("ma20_above_ma60") is not None:
                ma += "·多头" if tr["ma20_above_ma60"] else "·空头"
        ret60 = "—" if tr.get("ret60") is None else f"{tr['ret60']:+.1f}%"
        cls = "up" if p["change_pct"] >= 0 else "down"
        pb_txt = "—" if p.get("pb") is None else f"{p['pb']:.2f}"
        rows += (
            f"<tr><td>{i}</td>"
            f"<td><b>{_html_escape(p['name'])}</b><br>"
            f"<span class='sub'>{_html_escape(p['code'])}</span></td>"
            f"<td>{_html_escape(p.get('sector', ''))}</td>"
            f"<td>{p['price']:.2f}<br><span class='sub {cls}'>{p['change_pct']:+.2f}%</span></td>"
            f"<td>{p['turnover']:.2f}%</td>"
            f"<td>{p['pe']:.1f}<br><span class='sub'>PB {pb_txt}</span></td>"
            f"<td>{p['total_mv']:.0f}亿</td>"
            f"<td class='{'up' if (p.get('rev_growth') or 0) >= 0 else 'down'}'>"
            f"{_pct_txt(p.get('rev_growth'))}</td>"
            f"<td class='{'up' if (p.get('profit_growth') or 0) >= 0 else 'down'}'>"
            f"{_pct_txt(p.get('profit_growth'))}</td>"
            f"<td>{_html_escape(p.get('report_name', ''))}</td>"
            f"<td>{ret60}<br><span class='sub'>{ma}</span></td>"
            f"<td><b>{p['score']}</b><br><span class='sub'>复苏{p['score_parts']['recovery']}"
            f"/估值{p['score_parts']['value']}/换手{p['score_parts']['turnover']}</span></td></tr>")
    notes = "".join(f"<li>{_html_escape(n)}</li>" for n in
                    (meta.get("notes") or []) + (meta.get("warnings") or []))
    risk = ""
    if meta.get("market_ok") is False:
        risk = (f"<div class='warn'><b>大盘环境偏弱</b>："
                f"{_html_escape(meta.get('market_note', ''))}<br>"
                "本页仍按要求输出候选，但弱势环境下建议降低仓位、分批建仓。</div>")
    elif meta.get("market_ok") is True:
        risk = (f"<div class='okbar'>大盘环境正常："
                f"{_html_escape(meta.get('market_note', ''))}</div>")
    fund = result.get("fundamental") or {}
    fund_html = ""
    if has_run and (fund.get("quick_html") or fund.get("master_html")):
        fund_html = ("<div style='margin-top:16px;'>"
                     + (fund.get("quick_html") or "")
                     + (fund.get("master_html") or "") + "</div>")
    if has_run:
        result_card = f"""<div class="card">
<b>结果：{len(picks)} 只</b>（输入板块：{_html_escape(inp)}｜命中东财行业：{_html_escape(board_txt)}）
<div class="sub">候选池 {meta.get('pool_size', 0)} 只 → 核验 {meta.get('checked', 0)} 只 →
PE 基准 {_html_escape(meta.get('pe_median_scope', '—'))} {meta.get('pe_median') or '—'}｜
放宽级别 {meta.get('relax_level', 0)}</div>
<table><tr><th>#</th><th>股票</th><th>板块</th><th>现价</th><th>换手</th><th>PE</th>
<th>市值</th><th>营收同比</th><th>净利同比</th><th>报告期</th><th>60日涨幅</th><th>评分</th></tr>
{rows or "<tr><td colspan='12'>本次无符合条件的股票</td></tr>"}</table>
<ul>{notes}</ul>{fund_html}</div>"""
        stamp = f"<br><span class='sub'>生成时间：{_html_escape(meta.get('generated_at', ''))}</span>"
    else:
        result_card = f"""<div class="card"><b>怎么用</b>
<ul style="font-size:13px;color:#374151;">
<li>输入板块后点「开始选股」，先取该板块成分股并卡换手 {lo:.0f}%~{hi:.0f}%；</li>
<li>再逐只核验最新报告期财务，要求营收与净利同比双正且较上期改善；</li>
<li>然后按 PE 低于板块中位数筛出便宜货，最后按「复苏 45% + 估值 35% + 换手 20%」打分取前 5；</li>
<li>不足 5 只时按顺序放宽：换手区间 → 复苏口径 → 如实少出，过程都会写在结果里。</li>
</ul>
<div class="sub">可直接使用的关键词：{_html_escape('、'.join(presets))}；
也支持东财行业名（如 化学制药、养殖业、半导体设备）。</div></div>"""
        stamp = ""
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>低值复苏选股</title>
<style>
body{{background:#f5f6f8;color:#1f2937;font-family:"Microsoft YaHei",sans-serif;padding:26px;}}
.wrap{{max-width:1180px;margin:0 auto;}} h1{{font-size:22px;margin:0 0 6px;}}
.card{{background:#fff;border:1px solid #e6e9ef;border-radius:12px;padding:16px 20px;margin-bottom:16px;}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;font-size:13px;}}
th,td{{padding:8px 10px;border-bottom:1px solid #eef1f5;text-align:center;}}
th{{background:#f2f5fa;color:#475569;font-weight:600;}}
.sub{{color:#6b7280;font-size:12px;}} .up{{color:#dc2626;}} .down{{color:#059669;}}
input{{padding:9px 12px;border:1px solid #cbd5e1;border-radius:9px;font-size:14px;}}
button{{background:#2563eb;color:#fff;border:none;border-radius:999px;padding:9px 20px;
font-size:14px;cursor:pointer;}}
a{{color:#2563eb;}} .row{{display:flex;gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap;}}
.presets{{margin-top:12px;}} a.pre{{display:inline-block;background:#eef4ff;border:1px solid #d3e0ff;
border-radius:999px;padding:5px 12px;margin:0 8px 8px 0;text-decoration:none;font-size:13px;}}
.warn{{background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;border-radius:10px;
padding:12px 16px;margin-bottom:14px;font-size:13px;}}
.okbar{{background:#f0fdf4;border:1px solid #bbf7d0;color:#15803d;border-radius:10px;
padding:10px 16px;margin-bottom:14px;font-size:13px;}}
ul{{margin:10px 0 0 18px;padding:0;font-size:12px;color:#6b7280;}}
h2{{font-size:17px;margin:18px 0 6px;}}
.panel{{margin-top:6px;}} .hint{{color:#6b7280;font-size:12px;margin-top:6px;}}
</style></head><body><div class="wrap">
<h1>低值复苏选股</h1>
<div class="sub">条件：换手 {lo:.0f}%~{hi:.0f}% ｜ PE 低于板块内中位数 ｜
最新报告期营收与净利同比双正且较上期改善 ｜ 叠加大盘环境提示</div>
<div class="card"><form method="get" action="/lowval">
<b>输入板块</b>（多个用逗号分隔，如：科技,医药；留空 = 全市场活跃池）
<div class="row"><input name="sectors" style="flex:1;min-width:240px;"
value="{_html_escape('，'.join(meta.get('sectors_input') or []))}"
placeholder="科技、农业、医药、半导体、白酒……">
<button>开始选股</button></div>
<div class="row sub">换手区间：<input name="tmin" value="{lo:.0f}" style="width:60px">% ~
<input name="tmax" value="{hi:.0f}" style="width:60px">%　默认 3%~8%
<label style="margin-left:14px;"><input type="checkbox" name="force" value="1"> 强制刷新缓存</label></div>
</form>
<div class="presets">快捷板块：{preset_links}</div>
<div class="sub" style="margin-top:8px;">首次生成约 30 秒~1 分钟（逐只核验财务，板块越多越久）；
当日二次运行走缓存会快很多。</div></div>
{risk}
{result_card}
<div class="card"><a href="/">← 返回主报告</a>
{' ｜ <a href="/lowval">重新生成</a>' if has_run else ''}
{stamp}<br><span class="sub">数据来自公开免费接口（东财/新浪/腾讯），
单源未经双源核验；仅量化研究参考，不构成投资建议。</span></div>
</div></body></html>"""


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
        if self.path.startswith("/researchfile"):
            self._research_file()
            return
        if self.path.startswith("/research"):
            self._research()
            return
        if self.path.startswith("/bottleneck"):
            self._bottleneck()
            return
        if self.path.startswith("/direction"):
            self._direction()
            return
        if self.path.startswith("/lowval"):
            self._lowval()
            return
        if self.path.startswith("/quant"):
            self._quant()
            return
        if self.path.startswith("/echarts.min.js"):
            self._static(os.path.join(BASE, "echarts.min.js"), "application/javascript")
            return
        if self.path in ("/", "/index.html"):
            self._serve_report()
            return
        self.send_error(404, "Not Found")

    def do_POST(self):
        if self.path.startswith("/manualbuy"):
            self._manual_buy()
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
                from stockapp import daily_report
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
            from stockapp import research_data
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

    def _manual_buy(self):
        """POST /manualbuy?code=600519&shares=100&side=BUY|SELL → 任性操作（模拟盘T+1）"""
        try:
            q = self._query()
            code = (q.get("code") or [""])[0].strip()
            if not (code.isdigit() and len(code) == 6):
                self._json({"ok": False, "msg": "请输入6位股票代码"}, status=400)
                return
            shares = None
            if q.get("shares") and q["shares"][0].strip().isdigit():
                shares = int(q["shares"][0])
            side = "SELL" if (q.get("side") or [""])[0].strip().upper() == "SELL" else "BUY"
            from stockapp import manual_trade
            info = manual_trade.plan(code, shares=shares, side=side)
            order = manual_trade.execute(info, reason="任性" + ("卖出" if side == "SELL" else "买入"))
            act = "卖出" if side == "SELL" else "买入"
            self._json({
                "ok": True, "code": code, "name": info["name"], "side": side,
                "shares": order.shares,
                "entry": round(info["entry"], 2) if info["entry"] else None,
                "stop": round(info["stop"], 2) if info["stop"] else None,
                "msg": f"已生成 T+1 模拟{act}单：{code} {order.shares} 股"
                       f"（{order.reason}，明日开盘执行）",
            })
        except Exception as e:
            print("[服务] 任性操作失败:", e)
            self._json({"ok": False, "msg": str(e)}, status=400)

    def _direction(self):
        """GET /direction[?name=AI算力] → 紫苏叶方向选股：有底稿出看板，无底稿出任务单"""
        try:
            if os.path.join(BASE, "紫苏叶选股") not in sys.path:
                sys.path.insert(0, os.path.join(BASE, "紫苏叶选股"))
            import direction_picker
            q = self._query()
            name = (q.get("name") or [""])[0].strip()
            avail = direction_picker.available_directions()
            if name:
                r = direction_picker.run_direction(name)
                if r["ok"]:
                    self.send_response(302)
                    self.send_header("Location", "/bottleneck")
                    self.end_headers()
                    return
                with open(r["task_order"], encoding="utf-8") as f:
                    task_text = f.read()
                body = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>研究方向任务单：{_html_escape(name)}</title>
<style>body{{background:#f5f6f8;color:#1f2937;font-family:"Microsoft YaHei",sans-serif;padding:28px;}}
.wrap{{max-width:1000px;margin:0 auto;}} h1{{font-size:22px;}}
.card{{background:#fff;border:1px solid #e6e9ef;border-radius:12px;padding:16px 20px;margin-bottom:16px;}}
button{{background:#2563eb;color:#fff;border:none;border-radius:999px;padding:8px 18px;cursor:pointer;}}
textarea{{width:100%;height:420px;border:1px solid #dbe2ea;border-radius:10px;padding:12px;font-size:12px;font-family:Consolas,monospace;box-sizing:border-box;}}
a{{color:#2563eb;}}</style></head><body><div class="wrap">
<h1>研究方向任务单：{_html_escape(name)}</h1>
<div class="card"><b>该方向还没有研究底稿</b>，把下面内容复制给 Codex，附言"按 bottleneck-hunter skill 生成研究底稿"。
完成后把底稿保存到 <b>紫苏叶选股\\研究方向\\{_html_escape(direction_picker._slug(name))}_研究底稿.json</b>，再回来输入方向即可出看板。</div>
<div class="card"><button onclick="var t=document.getElementById('task');t.select();document.execCommand('copy');this.textContent='已复制 ✓';">复制任务单</button>
<textarea id="task" readonly>{_html_escape(task_text)}</textarea></div>
<div class="card"><a href="/direction">← 返回方向选股</a></div>
</div></body></html>"""
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            items = "".join(
                f"<li><a href='/direction?name={_html_escape(d)}'>{_html_escape(d)}</a></li>"
                for d in avail) or "<li>（暂无，输入新方向生成任务单）</li>"
            rec = direction_picker.recommend_direction()
            rec_hint = "（已有底稿，可直接出看板）" if rec["mode"] == "ready" \
                else "（暂无底稿，将生成研究任务单）"
            preset_links = "".join(
                f"<a href='/direction?name={_html_escape(p['name'])}' "
                f"style='display:inline-block;margin:0 10px 8px 0;'>{_html_escape(p['name'])}</a>"
                for p in direction_picker.PRESET_DIRECTIONS)
            body = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>紫苏叶方向选股</title>
<style>body{{background:#f5f6f8;color:#1f2937;font-family:"Microsoft YaHei",sans-serif;padding:28px;}}
.wrap{{max-width:1000px;margin:0 auto;}} h1{{font-size:22px;}}
.card{{background:#fff;border:1px solid #e6e9ef;border-radius:12px;padding:16px 20px;margin-bottom:16px;}}
input{{flex:1;min-width:260px;padding:10px 14px;border:1px solid #cbd5e1;border-radius:10px;font-size:14px;}}
button{{background:#2563eb;color:#fff;border:none;border-radius:999px;padding:10px 22px;font-size:14px;cursor:pointer;}}
a{{color:#2563eb;}}</style></head><body><div class="wrap">
<h1>紫苏叶方向选股</h1>
<div class="card"><form method="get" action="/direction">
<b>输入行业方向</b>（如 AI算力 / 白酒 / 固态电池）：
<div style="display:flex;gap:10px;margin-top:10px;"><input name="name" placeholder="例如：AI算力"><button>开始选股</button></div>
</form>
<div style="margin-top:14px;"><a href="/direction?name={_html_escape(rec['name'])}"><button>⚡ 系统推荐：{_html_escape(rec['name'])}</button></a>
<span class="sub">{_html_escape(rec['reason'])}{rec_hint}</span></div>
</div>
<div class="card"><b>推荐方向快捷入口：</b><div style="margin-top:10px;">{preset_links}</div></div>
<div class="card"><b>已有研究方向：</b><ul>{items}</ul>
<div style="margin-top:6px;"><a href="/">← 返回主报告</a> ｜ <a href="/bottleneck">查看最近看板</a></div></div>
</div></body></html>"""
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            print("[服务] 方向选股失败:", e)
            self._json({"ok": False, "msg": str(e)}, status=500)

    def _lowval(self):
        """GET /lowval[?sectors=科技,医药&tmin=3&tmax=8&force=1] → 低值复苏选股单页
        不带参数进入时只显示表单；提交后实时选股并展示结果（同时存档）。"""
        try:
            from stockapp import selection as S
            q = self._query()
            raw_sectors = (q.get("sectors") or [None])[0]
            sectors = (raw_sectors or "").strip()
            force = (q.get("force") or [""])[0] in ("1", "true", "on")
            fmt = (q.get("format") or [""])[0].strip().lower()

            def _arg(key, default):
                try:
                    return max(1.0, min(15.0, float((q.get(key) or [""])[0])))
                except Exception:
                    return default

            tmin = _arg("tmin", S.LOWVAL_TURNOVER_MIN)
            tmax = _arg("tmax", S.LOWVAL_TURNOVER_MAX)
            if tmin >= tmax:
                tmin, tmax = S.LOWVAL_TURNOVER_MIN, S.LOWVAL_TURNOVER_MAX

            result = None
            if raw_sectors is not None or q.get("tmin"):
                with LOCK:
                    result = S.pick_lowval(sectors=sectors, tmin=tmin, tmax=tmax,
                                           force=force)
            page = _lowval_page(result, tmin, tmax, S.LOWVAL_PRESET_SECTORS)
            if result is not None:
                _save_lowval_report(page, result)
            if fmt == "json":                    # 报告页「低值复苏股」页签用
                if result is None:
                    self._json({"ok": False, "msg": "缺少板块参数"}, status=400)
                    return
                self._json({"ok": True, "result": result})
                return
            body = page
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            print("[服务] 低值复苏选股失败:", e)
            self._json({"ok": False, "msg": str(e)}, status=500)

    def _quant(self):
        """GET /quant?code=600519 → 单股量化分析页（低值复苏股页签「量化页」转入）"""
        try:
            q = self._query()
            code = (q.get("code") or [""])[0].strip()
            if not (code.isdigit() and len(code) == 6):
                self._json({"ok": False, "msg": "请输入6位股票代码"}, status=400)
                return
            from stockapp import quant_engine as Q
            from stockapp.daily_report import build_report_html
            with LOCK:
                a = Q.analyze(code)
                now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                name = a["quote"]["name"]
                page = build_report_html(
                    [(code, "低值复苏转入", a, None)],
                    f"{name} {code} 量化分析",
                    "由「低值复苏股」页签转入的单股量化分析：趋势判断 / 买卖点 / 策略回测 / 成交量。",
                    now, watch_codes=[])
            data = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            print("[服务] 单股量化失败:", e)
            self._quant_error(locals().get("code") or "", e)

    def _quant_error(self, code, err):
        """单股量化失败时给一张可读的中文说明页，而不是丢一串英文异常"""
        msg = str(err)
        if any(k in msg for k in ("qt.gtimg.cn", "10013", "Max retries", "ConnectionError",
                                  "timed out", "Timeout")):
            reason = ("行情源（腾讯/新浪）连接不上。常见原因：网络中断、公司防火墙拦截，"
                      "或用改过网络权限的方式启动过服务。")
            advice = ("先重试一次；仍不行就双击 停止网页服务.cmd 再双击 启动报告服务.cmd "
                      "重启服务（用你平时的方式启动，网络权限才是完整的）。")
        else:
            reason = "该股票的行情或K线数据暂时取不到。"
            advice = "可能是停牌、新股或代码有误，换一只再试，或稍后重试。"
        body = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>量化页生成失败</title>
<style>body{{background:#f5f6f8;color:#1f2937;font-family:"Microsoft YaHei",sans-serif;padding:30px;}}
.wrap{{max-width:720px;margin:0 auto;background:#fff;border:1px solid #e6e9ef;border-radius:12px;
padding:22px 26px;}} h1{{font-size:20px;margin:0 0 10px;}}
.warn{{background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;border-radius:10px;
padding:12px 16px;margin:12px 0;font-size:14px;}}
.sub{{color:#6b7280;font-size:13px;line-height:1.8;}} code{{background:#f3f4f6;padding:2px 6px;
border-radius:6px;font-size:12px;word-break:break-all;}} a{{color:#2563eb;}}</style></head><body>
<div class="wrap">
<h1>量化页生成失败{'：' + _html_escape(code) if code else ''}</h1>
<div class="warn">{_html_escape(reason)}</div>
<div class="sub"><b>怎么办</b>：{_html_escape(advice)}</div>
<div class="sub" style="margin-top:10px;">原始错误：<code>{_html_escape(msg[:300])}</code></div>
<div class="sub" style="margin-top:14px;"><a href="/">← 返回主报告</a></div>
</div></body></html>"""
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path, ctype):
        """服务静态文件（echarts.min.js），让页面在离线时也能出图"""
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self.send_error(404, "Not Found")

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
                        from stockapp import daily_report
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
                        from stockapp import db
                        db.purge_code(code)
                        print(f"[服务] 已清理 {code} 的本地缓存数据")
                    except Exception:
                        pass
                    try:
                        # 重新生成报告，让移除后的量化页立即消失
                        from stockapp import daily_report
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


def _cleanup_legacy_flags():
    """局域网共享已改为常开：顺手清掉历史版本留下的开关文件（旧进程靠它判断，重启后即可删除）"""
    for name in ("LAN_SHARE.flag", "局域网共享.flag"):
        path = os.path.join(BASE, name)
        try:
            if os.path.exists(path):
                os.remove(path)
                print(f"[服务] 已清理历史开关文件 {name}（局域网共享现在常开，无需开关）")
        except Exception:
            pass


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
    _cleanup_legacy_flags()
    print("=" * 52)
    print(" 每日量化报告服务已启动")
    print(" 本机访问:  http://127.0.0.1:" + str(PORT) + "/")
    print(" 局域网共享: 常开（没有关闭开关）")
    if ip:
        print(" 同事访问: http://" + ip + ":" + str(PORT) + "/")
        print(" 提示: 同事打不开时，双击 局域网放行防火墙.cmd 放行 8765 端口")
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
