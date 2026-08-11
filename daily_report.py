# -*- coding: utf-8 -*-
"""
每日量化选股报告(单HTML, 顶部按钮切换):
  ① 联德精密(605060) 量化趋势(固定关注)
  ② 今日热门股3只(30元以内) 量化分析
  ③ 用户自选股(自选股.txt) 量化分析
每只股票均输出与"联德股份_量化交易分析报告"同等详细程度的量化分析
"""
import os
import sys
import json
import time
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
LAUNCHER = os.path.join(BASE, "启动报告服务.cmd").replace(os.sep, "/")
SKILL_ROOT = os.path.join(BASE, "cnfinancialscraper")
import eastmoney  # noqa: E402  内置备用数据源（技能缺失时兜底）
try:
    sys.path.insert(0, SKILL_ROOT)
    sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts"))
    from scripts import eastmoney_scraper  # noqa: E402
    get_hot_stocks = eastmoney_scraper.get_hot_stocks
except ImportError:
    get_hot_stocks = eastmoney.get_hot_stocks

import quant_engine as Q  # noqa: E402
import selection  # noqa: E402
import tracking  # noqa: E402
import v2  # noqa: E402

OUT_DIR = BASE
WATCH_FILE = os.path.join(BASE, "自选股.txt")
FIXED = [("605060", "联德精密(固定关注)")]
CACHE_FILE = os.path.join(BASE, "热门股缓存.json")


def _prog(pct, msg):
    try:
        import progress
        progress.report("每日报告", pct, msg)
    except Exception:
        pass


def fetch_hot_picks():
    """调用新选股引擎（Z-score热度/多日持续性/自适应过滤/市场环境/行业分散）
    返回 (picks_tuple_list, meta)"""
    result = selection.pick_hot_stocks(3)
    meta = result["meta"]
    picks = result["picks"]
    if not picks:
        return [], meta
    return [(p["code"], p["name"], p["price"], p["change_pct"],
             p["amount"], p["turnover"], p.get("score", 0)) for p in picks], meta


def read_watchlist():
    if not os.path.exists(WATCH_FILE):
        return []
    codes = []
    with open(WATCH_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = line.split()[0]
            if m.isdigit() and len(m) == 6:
                codes.append(m)
    return codes


def build_report_html(analyses, title, note, gen_time, watch_codes=None, tracking=None,
                      quick_html="", master_html=""):
    """单HTML + 顶部按钮切换 + 每只股票全量详情"""
    watch_codes = watch_codes or []
    name_of = {}
    for code, label, a, a2 in analyses:
        if a is not None:
            name_of[code] = a["quote"]["name"]
    tabs, pages, charts = [], [], []
    # 第0页: 自选股管理
    ov = []
    for n, (code, label, a, a2) in enumerate(analyses, 2):
        if a is None:
            ov.append(f"<tr class='clickable' onclick='go({n})'><td>{code}</td><td colspan='6'>分析失败（代码可能有误或停牌）</td></tr>")
            continue
        cls = 'up' if a['quote']['change_pct'] >= 0 else 'down'
        cls2 = 'up' if a['half_ret'] >= 0 else 'down'
        status = a2["status"] if a2 else a["verdict"]
        vcol = {"强势可入": "#dc2626", "强势观望": "#ea580c",
                "弱势": "#059669", "风险警示": "#7c3aed"}.get(status, "#6b7280") if a2 else a["vcolor"]
        ov.append(
            f"<tr class='clickable' onclick='go({n})'><td>{code}</td><td>{a['quote']['name']}</td>"
            f"<td><span class='tag v' style='background:{vcol}18;color:{vcol};border-color:{vcol}'>{status}</span></td>"
            f"<td>{a['quote']['price']:.2f}</td>"
            f"<td class='{cls}'>{a['quote']['change_pct']:+.2f}%</td>"
            f"<td class='{cls2}'>{a['half_ret']:+.2f}%</td>"
            f"<td>{a['bt']['total_ret']}%</td></tr>")
    overview_rows = "".join(ov) or "<tr><td colspan='7'>暂无股票数据</td></tr>"
    watch_rows = "".join(
        f"<tr><td>{c}</td><td>{name_of.get(c, '—')}</td>"
        f"<td>{'已生成量化页' if c in name_of else '代码可能有误'}</td>"
        f"<td><button class=\"rm-btn\" data-code=\"{c}\">移除</button></td></tr>"
        for c in watch_codes) or "<tr><td colspan='4'>暂无自选股，请在下方添加</td></tr>"
    tabs.append('<button class="navbtn active" data-slide="slide0">总览</button>')
    pages.append(f"""<div class="slide manage" id="slide0">
  <h2>今日量化总览</h2>
  <div class="sub">全部股票摘要：结论 / 现价 / 当日涨跌 / 近半年涨幅 / 策略回测总收益。点上方按钮或左右翻页查看每只股票的详细分析。<b>点击任意一行可直接跳转到该股票的量化页</b>。</div>
  <div class="panel">
    <table><tr><th>代码</th><th>名称</th><th>结论</th><th>现价</th><th>当日涨跌</th><th>近半年涨幅</th><th>回测总收益</th></tr>{overview_rows}</table>
  </div>
  <h2>自选股管理</h2>
  <div class="sub">输入任意A股代码，系统自动生成该股票的量化数据页（行情/成交量/指标/关键价位）；移除后对应页面消失。</div>
  <div class="panel">
    <div class="addrow">
      <input id="stockInput" class="stock-input" placeholder="输入6位股票代码，如 600519 / 000858">
      <button id="btnAdd" class="btn-add">＋ 添加自选</button>
    </div>
    <div class="sub" id="addMsg" style="margin-top:8px;"></div>
    <table><tr><th>代码</th><th>名称</th><th>状态</th><th>操作</th></tr>{watch_rows}</table>
    <div class="sub" style="margin-top:10px;">提示：添加/移除需要联网重新生成（约10~60秒），请通过“启动报告服务”打开本报告操作；直接双击文件打开仅能编辑，不能添加/保存。</div>
  </div>
{quick_html}
{master_html}
</div>""")
    # 第1页: 近7日选股跟踪
    tabs.append('<button class="navbtn" data-slide="slide1">近7日跟踪</button>')
    if tracking:
        track_panels = ""
        for day in tracking:
            rows_html = ""
            for r in day["rows"]:
                if r["today"]:
                    ret_td = "<td>今日</td>"
                    chg_cls = 'up' if (r["change_pct"] or 0) >= 0 else 'down'
                    chg_td = f"<td class='{chg_cls}'>{r['change_pct']:+.2f}%</td>"
                else:
                    chg_td = "<td>—</td>"
                    if r["ret"] is None:
                        ret_td = "<td>—</td>"
                    else:
                        ret_td = f"<td class='{'up' if r['ret'] >= 0 else 'down'}'>{r['ret']:+.2f}%</td>"
                price_td = f"<td>{r['price']:.2f}</td>" if r["price"] is not None else "<td>—</td>"
                ref_td = f"<td>{r['ref']:.2f}</td>" if r["ref"] is not None else "<td>—</td>"
                latest_td = f"<td>{r['latest']:.2f}</td>" if r["latest"] is not None else "<td>—</td>"
                rows_html += (
                    f"<tr><td>{r['code']}</td><td>{r['name']}</td>"
                    f"{price_td}{ref_td}{latest_td}"
                    f"{ret_td}{chg_td}<td>{r['score']}</td></tr>")
            avg_txt = f"{day['avg_ret']:+.2f}%" if day["avg_ret"] is not None else "—"
            avg_cls = 'up' if (day["avg_ret"] or 0) >= 0 else 'down'
            track_panels += f"""<div class="panel">
  <b>{day['date']} · 当日选股 {len(day['rows'])} 只 · 平均区间涨跌 <span class="{avg_cls}">{avg_txt}</span></b>
  <table><tr><th>代码</th><th>名称</th><th>选股日价格</th><th>选出日收盘</th><th>最新价</th><th>区间涨跌幅</th><th>当日涨跌</th><th>热度分</th></tr>{rows_html}</table>
</div>"""
        pages.append(f"""<div class="slide" id="slide1">
  <h2>近7日选股跟踪</h2>
  <div class="sub">每天的热门选股都会存入数据库（选股数据.db），这里显示最近7天的选股和“选出日收盘→最新收盘”的区间涨跌幅，方便持续观察选股效果。</div>
  {track_panels}
</div>""")
    else:
        pages.append(f"""<div class="slide" id="slide1">
  <h2>近7日选股跟踪</h2>
  <div class="panel"><b>暂无历史记录</b>：选股数据从今天开始积累，运行几天后这里会自动生成跟踪表。</div>
</div>""")
    for idx, (code, label, a, a2) in enumerate(analyses, 2):
        if a is None:
            tabs.append(f'<button class="navbtn" data-slide="slide{idx}">{code} 失败</button>')
            pages.append(f'<div class="slide" id="slide{idx}"><div class="panel"><b>{code}</b> 分析失败（代码可能有误或停牌），已跳过。</div></div>')
            continue
        tab_name = f"{a['quote']['name']}"
        tabs.append(f'<button class="navbtn" data-slide="slide{idx}">{tab_name}</button>')
        tag = (f"<span class='tag' style='background:#244;color:#7fd'>{label}</span>" if label else "")
        pages.append(Q.lite_section_html(a, idx, len(charts), tag=tag, a2=a2))
        charts.append(Q.full_chart_json(a))
    charts_json = json.dumps(charts, ensure_ascii=False)
    tabs_html = "\n    ".join(tabs)
    pages_html = "\n".join(pages)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script src="echarts.min.js"></script>
<script>if (typeof echarts === 'undefined') {{ document.write('<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"><\\/script>'); }}</script>
<style>
  :root {{ --bg:#f5f6f8; --card:#ffffff; --line:#e6e9ef; --up:#e11d48; --down:#059669; --txt:#1f2937; --sub:#6b7280; --accent:#2563eb; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:radial-gradient(circle at 12% -5%, rgba(37,99,235,.08), transparent 38%), radial-gradient(circle at 88% -5%, rgba(6,182,212,.07), transparent 36%), var(--bg); color:var(--txt); font-family:"Microsoft YaHei","PingFang SC",sans-serif; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:34px 28px 64px; }}
  .headrow {{ display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:6px; }}
  h1 {{ font-size:26px; font-weight:700; letter-spacing:.5px; }}
  h2 {{ font-size:21px; margin:12px 0 4px; font-weight:700; }}
  h3 {{ font-size:15px; margin:32px 0 10px; color:var(--accent); font-weight:600; padding-left:10px; border-left:3px solid var(--accent); }}
  .sub {{ color:var(--sub); font-size:13px; margin-top:4px; line-height:1.7; }}
  .nav {{ display:flex; justify-content:center; gap:8px; flex-wrap:wrap; margin:20px 0 24px; }}
  .navbtn {{ background:#fff; color:#374151; border:1px solid #e2e6ee; border-radius:999px; padding:8px 18px; cursor:pointer; font-size:14px; font-family:inherit; box-shadow:0 1px 2px rgba(15,23,42,.04); }}
  .navbtn.active {{ background:var(--accent); color:#fff; border-color:var(--accent); font-weight:600; box-shadow:0 2px 8px rgba(37,99,235,.25); }}
  .slide {{ display:none; }} .slide.active {{ display:block; animation:fadeIn .35s ease; }}
  @keyframes fadeIn {{ from {{ opacity:0; transform:translateY(10px); }} to {{ opacity:1; transform:none; }} }}
  .pager {{ display:flex; align-items:center; justify-content:center; gap:16px; margin:30px 0 6px; }}
  .pager button {{ background:#fff; border:1px solid #e2e6ee; border-radius:999px; padding:9px 22px; cursor:pointer; font-size:14px; font-family:inherit; box-shadow:0 1px 2px rgba(15,23,42,.05); }}
  .pager button:hover {{ border-color:var(--accent); color:var(--accent); }}
  .dots {{ display:flex; gap:7px; }}
  .dot {{ width:9px; height:9px; border-radius:50%; background:#d4d9e2; cursor:pointer; border:none; padding:0; }}
  .dot.active {{ background:var(--accent); }}
  .pagelabel {{ color:var(--sub); font-size:13px; min-width:64px; text-align:center; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(142px,1fr)); gap:12px; margin-top:16px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 12px; text-align:center; box-shadow:0 1px 3px rgba(15,23,42,.05); }}
  .card .num {{ font-size:20px; font-weight:700; margin-bottom:4px; }}
  .card .lbl {{ font-size:12px; color:var(--sub); }}
  .cards4 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-top:14px; }}
  .up {{ color:var(--up); }} .down {{ color:var(--down); }}
  .chart {{ background:var(--card); border:1px solid var(--line); border-radius:14px; height:460px; margin-top:12px; box-shadow:0 1px 3px rgba(15,23,42,.05); }}
  .panel {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 20px; margin-top:12px; box-shadow:0 1px 3px rgba(15,23,42,.05); }}
  .zone {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; margin-top:12px; }}
  .zone .item {{ background:#fbfcfe; border:1px solid var(--line); border-radius:12px; padding:16px; }}
  .zone .item h3 {{ font-size:14px; margin:0 0 10px; color:#374151; border:none; padding:0; }}
  .zone .item p {{ font-size:13px; color:var(--sub); line-height:1.9; }}
  .zone .item .big {{ font-size:16px; color:var(--txt); font-weight:600; }}
  .tag {{ display:inline-block; padding:3px 12px; border-radius:999px; font-size:12px; margin-left:8px; border:1px solid; vertical-align:middle; font-weight:600; }}
  .warn {{ background:#fffbeb; border:1px solid #fde68a; border-radius:12px; padding:13px 18px; margin-top:14px; font-size:13px; color:#92400e; line-height:1.8; }}
  .btn-refresh {{ background:var(--accent); color:#fff; border:none; border-radius:999px; padding:10px 22px; font-size:14px; font-weight:600; cursor:pointer; font-family:inherit; box-shadow:0 2px 8px rgba(37,99,235,.25); }}
  .btn-refresh:disabled {{ opacity:.6; cursor:wait; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card); border-radius:12px; overflow:hidden; margin-top:12px; font-size:13px; box-shadow:0 1px 3px rgba(15,23,42,.05); }}
  th,td {{ padding:9px 12px; text-align:right; border-bottom:1px solid #eef1f6; white-space:nowrap; }}
  th {{ background:#f1f5f9; color:#475569; font-weight:600; }} td:first-child,th:first-child {{ text-align:left; }}
  tr:hover td {{ background:#f8fafc; }}
  tr.clickable {{ cursor:pointer; }}
  tr.clickable:hover td {{ background:#eff6ff; }}
  .flex {{ display:flex; gap:14px; flex-wrap:wrap; }}
  .half {{ flex:1; min-width:340px; }}
  .hint {{ color:#9aa4b2; font-size:12px; margin-top:10px; }}
  details {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 16px; margin-top:12px; box-shadow:0 1px 3px rgba(15,23,42,.05); }}
  summary {{ cursor:pointer; font-size:14px; font-weight:600; color:var(--accent); }}
  summary:hover {{ opacity:.8; }}
  .toolbar {{ display:flex; gap:8px; flex-wrap:wrap; }}
  .btn-edit {{ background:#fff; color:#374151; border:1px solid #cbd5e1; border-radius:999px; padding:10px 18px; font-size:14px; font-weight:600; cursor:pointer; font-family:inherit; }}
  .btn-save {{ background:#059669; color:#fff; border:none; border-radius:999px; padding:10px 18px; font-size:14px; font-weight:600; cursor:pointer; font-family:inherit; box-shadow:0 2px 8px rgba(5,150,105,.25); }}
  .btn-save:disabled {{ opacity:.6; cursor:wait; }}
  .addrow {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }}
  .stock-input {{ flex:1; min-width:220px; padding:10px 14px; border:1px solid #cbd5e1; border-radius:10px; font-size:14px; font-family:inherit; }}
  .btn-add {{ background:var(--accent); color:#fff; border:none; border-radius:999px; padding:10px 20px; font-size:14px; font-weight:600; cursor:pointer; font-family:inherit; }}
  .btn-add:disabled {{ opacity:.6; cursor:wait; }}
  .rm-btn {{ background:#fff; color:#e11d48; border:1px solid #fecaca; border-radius:999px; padding:4px 12px; font-size:12px; cursor:pointer; font-family:inherit; }}
  .rm-btn:hover {{ background:#fef2f2; }}
  .editing .card:hover, .editing .panel:hover, .editing .zone .item:hover, .editing .stock-head:hover {{ outline:1.5px dashed #93c5fd; outline-offset:3px; }}
  [contenteditable="true"] {{ cursor:text; }}
  .stock-head {{ display:flex; justify-content:space-between; align-items:flex-end; gap:16px; flex-wrap:wrap; margin-top:8px; }}
  .price-box {{ text-align:right; }}
  .price {{ font-size:34px; font-weight:700; color:var(--txt); line-height:1.1; }}
  .price .unit {{ font-size:16px; color:var(--sub); font-weight:400; }}
  .chg {{ font-size:17px; font-weight:600; margin-top:2px; }}
  @media (max-width:700px) {{ h1 {{ font-size:20px; }} .wrap {{ padding:22px 14px 44px; }} }}
</style>
</head>
<body><div class="wrap">
<div class="headrow">
  <h1>{title}</h1>
  <div class="toolbar">
    <button id="btnEdit" class="btn-edit">✏️ 编辑</button>
    <button id="btnSave" class="btn-save">💾 保存修改</button>
    <button id="btnRefresh" class="btn-refresh">⟳ 刷新数据</button>
  </div>
</div>
<div class="sub">{note}</div>

<div class="nav">
    {tabs_html}
</div>
<div class="slides">
{pages_html}
</div>
<div class="pager">
  <button id="prevSlide">‹ 上一页</button>
  <div class="dots" id="dots"></div>
  <span class="pagelabel" id="pageLabel"></span>
  <button id="nextSlide">下一页 ›</button>
</div>
<div class="sub" style="margin-top:16px;">生成时间: {gen_time} · 数据来源: 东方财富/新浪/腾讯行情 · 快捷键 ← → 翻页 · 内容可实时编辑</div>
<div class="sub" style="margin-top:8px;">⏱ 自动刷新：每 5 分钟重新抓取数据 <span id="autoCount" style="font-weight:700;">05:00</span>
  <button id="btnAuto" class="btn-edit" style="padding:4px 12px;font-size:12px;">自动刷新: 开</button>
  <span id="autoHint" class="hint"></span>
</div>
</div>
<script>
const STOCKS = {charts_json};
const charts = {{}};
function fmt(v) {{ return v == null ? '-' : (+v).toFixed(2); }}
function mkKline(s, i) {{
  const c = echarts.init(document.getElementById('chk_' + i + '_kline'));
  charts['k' + i] = c;
  c.setOption({{
    tooltip: {{trigger:'axis', axisPointer: {{type:'cross'}}}},
    legend: {{data:['K线','MA5','MA10','MA20','MA60','买点','卖点'], textStyle: {{color:'#475569'}}}},
    grid: [{{left:56,right:14,top:30,height:'56%'}}, {{left:56,right:14,top:'74%',height:'18%'}}],
    xAxis: [{{type:'category', data:s.dates, axisLabel:{{color:'#94a3b8'}}}},
            {{type:'category', gridIndex:1, data:s.dates, axisLabel:{{show:false}}}}],
    yAxis: [{{scale:true, axisLabel:{{color:'#94a3b8'}}}}, {{gridIndex:1, axisLabel:{{color:'#94a3b8'}}}}],
    dataZoom: [{{type:'inside', xAxisIndex:[0,1], start:50, end:100}}],
    series: [
      {{name:'K线', type:'candlestick', data:s.k, itemStyle:{{color:'#e74c3c',color0:'#2ecc71',borderColor:'#e74c3c',borderColor0:'#2ecc71'}}}},
      {{name:'MA5', type:'line', data:s.ma5, symbol:'none', lineStyle:{{width:1,color:'#f0b90b'}}}},
      {{name:'MA10', type:'line', data:s.ma10, symbol:'none', lineStyle:{{width:1,color:'#5dade2'}}}},
      {{name:'MA20', type:'line', data:s.ma20, symbol:'none', lineStyle:{{width:1,color:'#af7ac5'}}}},
      {{name:'MA60', type:'line', data:s.ma60, symbol:'none', lineStyle:{{width:1,color:'#f1948a'}}}},
      {{name:'成交量', type:'bar', xAxisIndex:1, yAxisIndex:1, data:s.vol,
        itemStyle:{{color:function(p){{return s.k[p.dataIndex][1]>=s.k[p.dataIndex][0]?'#e74c3c77':'#2ecc7177'}}}}}},
      {{name:'买点', type:'scatter', data:s.sig.filter(x=>x[2]==='B').map(x=>[x[0],x[1]]),
        symbol:'triangle', symbolSize:11, itemStyle:{{color:'#e74c3c'}}}},
      {{name:'卖点', type:'scatter', data:s.sig.filter(x=>x[2]==='S').map(x=>[x[0],x[1]]),
        symbol:'triangle', symbolRotate:180, symbolSize:11, itemStyle:{{color:'#2ecc71'}}}}
    ]
  }});
}}
function mkInd(s, i) {{
  const c = echarts.init(document.getElementById('chk_' + i + '_ind'));
  charts['i' + i] = c;
  c.setOption({{
    tooltip: {{trigger:'axis'}},
    legend: {{data:['DIF','DEA','MACD','RSI','K','D','J'], textStyle: {{color:'#475569'}}}},
    grid: [{{left:56,right:14,top:30,height:'26%'}}, {{left:56,right:14,top:'42%',height:'22%'}}, {{left:56,right:14,top:'76%',height:'18%'}}],
    xAxis: [{{type:'category', data:s.dates, axisLabel:{{color:'#94a3b8'}}}},
            {{type:'category', gridIndex:1, data:s.dates, axisLabel:{{show:false}}}},
            {{type:'category', gridIndex:2, data:s.dates, axisLabel:{{show:false}}}}],
    yAxis: [{{scale:true, axisLabel:{{color:'#94a3b8'}}}}, {{gridIndex:1, min:0, max:100, axisLabel:{{color:'#94a3b8'}}}}, {{gridIndex:2, axisLabel:{{color:'#94a3b8'}}}}],
    dataZoom: [{{type:'inside', xAxisIndex:[0,1,2], start:50, end:100}}],
    series: [
      {{name:'DIF', type:'line', data:s.dif, symbol:'none', lineStyle:{{width:1,color:'#f0b90b'}}}},
      {{name:'DEA', type:'line', data:s.dea, symbol:'none', lineStyle:{{width:1,color:'#5dade2'}}}},
      {{name:'MACD', type:'bar', data:s.hist, itemStyle:{{color:function(p){{return p.data>=0?'#e74c3c88':'#2ecc7188'}}}}}},
      {{name:'RSI', type:'line', xAxisIndex:1, yAxisIndex:1, data:s.rsi, symbol:'none', lineStyle:{{width:1,color:'#58d68d'}}}},
      {{name:'K', type:'line', xAxisIndex:2, yAxisIndex:2, data:s.kd, symbol:'none', lineStyle:{{width:1,color:'#f0b90b'}}}},
      {{name:'D', type:'line', xAxisIndex:2, yAxisIndex:2, data:s.dd, symbol:'none', lineStyle:{{width:1,color:'#5dade2'}}}},
      {{name:'J', type:'line', xAxisIndex:2, yAxisIndex:2, data:s.jj, symbol:'none', lineStyle:{{width:1,color:'#af7ac5'}}}}
    ]
  }});
}}
// 图表渲染移到页面末尾执行，避免图表库加载失败导致翻页功能失效
let editing = true;
function setEdit(on) {{
  editing = on;
  document.querySelectorAll('.slide:not(.manage)').forEach(s => s.setAttribute('contenteditable', on ? 'true' : 'false'));
  document.getElementById('btnEdit').textContent = on ? '🔒 锁定' : '✏️ 编辑';
  document.body.classList.toggle('editing', on);
}}
document.getElementById('btnEdit').addEventListener('click', () => setEdit(!editing));
document.getElementById('btnSave').addEventListener('click', function () {{
  if (location.protocol !== 'http:' && location.protocol !== 'https:') {{
    alert('当前是本地文件方式，无法保存。\\n请双击 {LAUNCHER} 打开本报告，即可保存修改。');
    return;
  }}
  var btn = this;
  btn.disabled = true;
  btn.textContent = '保存中…';
  fetch('/save', {{
    method: 'POST',
    headers: {{'Content-Type': 'text/html; charset=utf-8'}},
    body: '<!DOCTYPE html>\\n' + document.documentElement.outerHTML
  }})
    .then(function (r) {{ return r.json(); }})
    .then(function (d) {{
      btn.disabled = false;
      btn.textContent = '💾 保存修改';
      alert(d.ok ? '已保存 ✓（刷新数据会覆盖修改）' : '保存失败：' + d.msg);
    }})
    .catch(function () {{
      btn.disabled = false;
      btn.textContent = '💾 保存修改';
      alert('保存失败：请确认“启动报告服务”窗口仍在运行。');
    }});
}});
document.getElementById('btnAdd').addEventListener('click', function () {{
  if (location.protocol !== 'http:' && location.protocol !== 'https:') {{
    alert('请双击 {LAUNCHER} 打开本报告后，才能添加自选股。');
    return;
  }}
  var code = document.getElementById('stockInput').value.trim();
  if (!/^\\d{{6}}$/.test(code)) {{ alert('请输入6位数字股票代码'); return; }}
  var btn = this;
  btn.disabled = true;
  btn.textContent = '添加中（约10-60秒）…';
  document.getElementById('addMsg').textContent = '正在联网获取 ' + code + ' 的数据并生成量化页…';
  fetch('/addstock', {{method: 'POST', body: code}})
    .then(function (r) {{ return r.json(); }})
    .then(function (d) {{
      if (d.ok) {{ location.reload(); }}
      else {{
        alert('添加失败：' + d.msg);
        btn.disabled = false;
        btn.textContent = '＋ 添加自选';
        document.getElementById('addMsg').textContent = '';
      }}
    }})
    .catch(function () {{
      btn.disabled = false;
      btn.textContent = '＋ 添加自选';
      document.getElementById('addMsg').textContent = '';
      alert('添加失败：请确认“启动报告服务”窗口仍在运行。');
    }});
}});
document.querySelectorAll('.rm-btn').forEach(function (b) {{
  b.addEventListener('click', function () {{
    if (location.protocol !== 'http:' && location.protocol !== 'https:') {{
      alert('请通过“启动报告服务”打开后移除自选股。');
      return;
    }}
    if (!confirm('确认移除 ' + this.dataset.code + ' 吗？')) return;
    var btn = this;
    btn.disabled = true;
    btn.textContent = '移除中…';
    fetch('/removestock', {{method: 'POST', body: this.dataset.code}})
      .then(function (r) {{ return r.json(); }})
      .then(function (d) {{
        if (d.ok) location.reload();
        else {{ alert('移除失败：' + d.msg); location.reload(); }}
      }})
      .catch(function () {{ alert('移除失败：请确认服务在运行'); location.reload(); }});
  }});
}});
setEdit(false);
document.getElementById('btnRefresh').addEventListener('click', function () {{
  if (location.protocol !== 'http:' && location.protocol !== 'https:') {{
    alert('当前是本地文件方式打开，无法在线刷新。\\n请双击 {LAUNCHER} 打开本报告后点击刷新；\\n或重新双击 运行每日量化报告.cmd 重新生成。');
    return;
  }}
  var btn = this;
  btn.disabled = true;
  btn.textContent = '刷新中（约10-30秒）…';
  fetch('/refresh', {{method:'POST'}})
    .then(function (r) {{ return r.json(); }})
    .then(function () {{ location.reload(); }})
    .catch(function () {{
      btn.disabled = false;
      btn.textContent = '⟳ 刷新数据';
      alert('刷新失败：请确认“启动报告服务”窗口仍在运行，或稍后重试。');
    }});
}});
const total = document.querySelectorAll('.slide').length;
const dotsBox = document.getElementById('dots');
for (let i = 0; i < total; i++) {{
  const d = document.createElement('button');
  d.className = 'dot';
  d.title = '第 ' + (i + 1) + ' 页';
  d.addEventListener('click', () => go(i));
  dotsBox.appendChild(d);
}}
let cur = 0;
function go(i) {{
  cur = (i + total) % total;
  document.querySelectorAll('.slide').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.navbtn').forEach(b => b.classList.remove('active'));
  const slide = document.getElementById('slide' + cur);
  if (slide) slide.classList.add('active');
  const nb = document.querySelector('.navbtn[data-slide="slide' + cur + '"]');
  if (nb) nb.classList.add('active');
  document.querySelectorAll('.dot').forEach((d, j) => d.classList.toggle('active', j === cur));
  document.getElementById('pageLabel').textContent = (cur + 1) + ' / ' + total;
  window.scrollTo({{top: 0, behavior: 'smooth'}});
  setTimeout(() => Object.values(charts).forEach(c => c.resize()), 80);
}}
document.querySelectorAll('.navbtn').forEach(b => b.addEventListener('click', () => go(+b.dataset.slide.slice(5))));
document.getElementById('prevSlide').addEventListener('click', () => go(cur - 1));
document.getElementById('nextSlide').addEventListener('click', () => go(cur + 1));
document.addEventListener('keydown', e => {{
  if (e.key === 'ArrowRight') go(cur + 1);
  if (e.key === 'ArrowLeft') go(cur - 1);
}});
go(0);
// 页面交互全部就绪后，最后渲染图表（单个图表失败不影响翻页）
STOCKS.forEach((s, i) => {{
  try {{ mkKline(s, i); }} catch (e) {{ }}
  try {{ mkInd(s, i); }} catch (e) {{ }}
}});
// 自动刷新：每5分钟重新加载（需通过“启动报告服务”打开；编辑中自动顺延）
const AUTO_INTERVAL = 300000;
let autoRefresh = true;
let nextReload = Date.now() + AUTO_INTERVAL;
const autoCountEl = document.getElementById('autoCount');
const autoHintEl = document.getElementById('autoHint');
document.getElementById('btnAuto').addEventListener('click', function () {{
  autoRefresh = !autoRefresh;
  this.textContent = '自动刷新: ' + (autoRefresh ? '开' : '关');
  nextReload = Date.now() + AUTO_INTERVAL;
}});
setInterval(function () {{
  if (location.protocol !== 'http:' && location.protocol !== 'https:') {{
    autoCountEl.textContent = '--:--';
    autoHintEl.textContent = '（直接双击文件不会自动刷新，请通过 启动报告服务.cmd 打开）';
    return;
  }}
  var left = Math.max(0, nextReload - Date.now());
  autoCountEl.textContent = String(Math.floor(left / 60000)).padStart(2, '0') + ':' + String(Math.floor(left % 60000 / 1000)).padStart(2, '0');
  if (left > 0) return;
  var nowD = new Date();
  var wd = nowD.getDay();
  var hm = ('0' + nowD.getHours()).slice(-2) + ':' + ('0' + nowD.getMinutes()).slice(-2);
  var inSession = wd >= 1 && wd <= 5 && ((hm >= '09:15' && hm <= '11:35') || (hm >= '12:55' && hm <= '15:10'));
  if (!inSession) {{
    autoHintEl.textContent = '（已收盘，自动刷新暂停，下一交易日 09:15 起恢复）';
    nextReload = Date.now() + AUTO_INTERVAL;
    return;
  }}
  if (!autoRefresh) {{ nextReload = Date.now() + AUTO_INTERVAL; return; }}
  if (editing) {{
    autoHintEl.textContent = '（编辑中，自动刷新顺延5分钟；点【锁定】后恢复）';
    nextReload = Date.now() + AUTO_INTERVAL;
    return;
  }}
  autoHintEl.textContent = '正在刷新数据...';
  location.reload();
}}, 1000);
</script>
</body></html>"""


def main():
    today = datetime.date.today().strftime("%Y%m%d")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    _prog(0, "开始生成每日报告")
    picks, meta = fetch_hot_picks()
    _prog(12, f"热门选股完成，{len(picks)} 只")
    from_cache = meta.get("from_cache", False)
    print("热门股TOP3:", [(p[0], p[1]) for p in picks])
    if from_cache:
        print("（注：行情接口暂无数据，热门股使用最近一次成功结果）")
    for w in meta.get("warnings", []):
        print("提示:", w)

    watch = read_watchlist()
    print("自选股:", watch)

    codes = [(FIXED[0][0], FIXED[0][1])]
    codes += [(p[0], "今日热门") for p in picks]
    codes += [(c, "用户自选") for c in watch if c not in {x[0] for x in codes}]

    analyses = []
    for i, (code, label) in enumerate(codes):
        try:
            a = Q.analyze(code)
            a2 = None
            try:
                a2 = v2.analyze_v2(code, a["quote"]["name"])
            except Exception:
                a2 = None
            analyses.append((code, label, a, a2))
            status = a2["status"] if a2 else a["verdict"]
            print(f"  分析完成: {a['quote']['name']}({code}) {status}")
            _prog(12 + 55 * (i + 1) / max(len(codes), 1),
                  f"分析 {a['quote']['name']}({code})：{status}")
        except Exception as e:
            print(f"  [{code}] 分析失败: {e}")
            analyses.append((code, label, None, None))
            _prog(12 + 55 * (i + 1) / max(len(codes), 1), f"{code} 分析失败")

    title = f"每日量化选股报告 {today}"
    note = (f"① 联德精密量化趋势 · ② 今日热门3只: "
            + "、".join(f"{p[1]}({p[2]:.2f}元)" for p in picks)
            + (f" · ③ 用户自选: " + "、".join(watch) if watch else ""))
    if from_cache:
        note += " | ⚠️ 热门股为缓存数据（行情接口暂不可用）"
    extra = (meta.get("notes") or []) + (meta.get("warnings") or [])
    if extra:
        note += " | 选股说明: " + "；".join(extra[:3])
    try:
        tracking_data = tracking.build_tracking(7)
    except Exception:
        tracking_data = None
    _prog(70, "7日跟踪数据完成")
    quick_html = ""
    try:
        import research_data
        quick = []
        for c, _l in codes:
            try:
                quick.append(research_data.get_pack(c))
            except Exception as e:
                print(f"  [基本面] {c} 速评数据获取失败: {e}")
                quick.append(None)
        quick_html = research_data.build_quick_review_html(quick)
        _prog(73, "基本面速评完成")
    except Exception as e:
        print("[基本面] 速评模块不可用:", e)
    master_html = ""
    try:
        import master_score
        quant_map = {}
        for code, _label, a, a2 in analyses:
            if a is None:
                continue
            quant_map[code] = {
                "status": a2["status"] if a2 else a["verdict"],
                "l3": a2["l3"] if a2 else None,
                "trend_verdict": a["verdict"],
                "backtest_total_ret": a["bt"]["total_ret"],
                "price": a["quote"]["price"],
                "change_pct": a["quote"]["change_pct"],
            }
        master_html = master_score.build_master_review_html(quick, quant_map)
        _prog(74, "四大师定性速评完成")
    except Exception as e:
        print("[四大师] 速评模块不可用:", e)
    html = build_report_html(analyses, title, note, now, watch_codes=watch,
                             tracking=tracking_data, quick_html=quick_html,
                             master_html=master_html)
    out = os.path.join(OUT_DIR, f"每日量化选股报告_{today}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("报告已保存:", out)
    _prog(76, "报告已保存")

    lines = [f"每日热门股（选股系统 v2） {today}", "=" * 46]
    for i, p in enumerate(picks, 1):
        lines.append(f"{i}. {p[1]}({p[0]})  {p[2]:.2f}元  {p[3]:+.2f}%  成交额{p[4]:.1f}亿  换手{p[5]:.1f}%")
    lines += ["=" * 46]
    for w in meta.get("warnings", []):
        lines.append("提示: " + w)
    for n in meta.get("notes", [])[:2]:
        lines.append("说明: " + n)
    if tracking_data:
        lines.append("=" * 46)
        lines.append("近7日选股跟踪（选出日收盘→最新收盘）:")
        for day in tracking_data:
            ps = "、".join(
                f"{r['name']}({r['ret']:+.2f}%)" if r["ret"] is not None else f"{r['name']}(今日)"
                for r in day["rows"])
            lines.append(f"{day['date']}: {ps}")
    lines += ["=" * 46, f"生成时间: {now} | 详见每日量化选股报告_{today}.html"]
    with open(os.path.join(OUT_DIR, f"每日热门股_{today}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    _prog(80, "文本输出完成")
    try:
        import archive
        n = archive.archive_old_reports(OUT_DIR)
        if n:
            print(f"已归档 {n} 个旧报告 -> 报告归档 文件夹")
    except Exception:
        pass


if __name__ == "__main__":
    main()
