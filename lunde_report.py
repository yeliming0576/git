# -*- coding: utf-8 -*-
"""
联德精密(605060) 量化交易分析报告（重构版）
实时抓取行情 + 近一年日K，计算技术指标，回测三种策略，
输出浅色科技风单页 HTML 报告：联德股份_量化交易分析报告.html

运行: 双击 运行每日量化报告.cmd 即会生成；或直接  python lunde_report.py
"""
import json
import os

import quant_engine as Q

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "报告归档", "个股", "联德股份_量化交易分析报告.html")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
CODE = "605060"


def ma_signals(rows, ma5, ma20):
    """双均线策略：MA5上穿MA20且收盘站上MA20买入，下穿卖出"""
    sig = [None] * len(rows)
    for i in range(1, len(rows)):
        if ma5[i - 1] is None or ma20[i - 1] is None:
            continue
        if ma5[i - 1] <= ma20[i - 1] and ma5[i] > ma20[i] and rows[i]["close"] > ma20[i]:
            sig[i] = "B"
        elif ma5[i - 1] >= ma20[i - 1] and ma5[i] < ma20[i]:
            sig[i] = "S"
    return sig


def macd_signals(rows, dif, dea, vol_ma5):
    """MACD+量能策略：DIF上穿DEA且当日量≥5日均量买入，下穿卖出"""
    sig = [None] * len(rows)
    for i in range(1, len(rows)):
        if dif[i - 1] is None or vol_ma5[i] is None:
            continue
        if rows[i]["volume"] >= vol_ma5[i]:
            if dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]:
                sig[i] = "B"
            elif dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]:
                sig[i] = "S"
    return sig


def main():
    print("正在抓取 605060 行情与近一年日K线...")
    a = Q.analyze(CODE)
    rows = a["rows"]
    quote = a["quote"]
    win = a["win"]

    closes = [r["close"] for r in rows]
    ma5 = Q.sma(closes, 5)
    ma10 = Q.sma(closes, 10)
    ma20 = Q.sma(closes, 20)
    ma60 = Q.sma(closes, 60)
    dif, dea, hist = Q.macd(closes)
    vol_ma5 = Q.sma([r["volume"] for r in rows], 5)

    sig_ma = ma_signals(rows, ma5, ma20)
    sig_macd = macd_signals(rows, dif, dea, vol_ma5)
    sig_all = Q.trend_signals(rows, ma5, ma10, ma20, dif, dea, vol_ma5)
    bt_ma = Q.backtest(rows, sig_ma)
    bt_macd = Q.backtest(rows, sig_macd)
    bt_all = a["bt"]
    buyhold_full = (rows[-1]["close"] / rows[0]["open"] - 1) * 100

    chart_rows = [[r["date"], r["open"], r["close"], r["low"], r["high"],
                   r["volume"] / 1e4, r["ma5"], r["ma10"], r["ma20"], r["ma60"],
                   r["dif"], r["dea"], r["hist"], r["rsi"], r["k"], r["d"], r["j"],
                   r["turnover"]] for r in rows]
    data_json = json.dumps(chart_rows, ensure_ascii=False)
    sig_json = json.dumps(a["signals"], ensure_ascii=False)
    eq_json = json.dumps(a["eq_curve"], ensure_ascii=False)

    html = render_html(a, quote, rows, win, bt_ma, bt_macd, bt_all,
                       buyhold_full, data_json, sig_json, eq_json)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("报告已生成:", OUT)


def render_html(a, quote, rows, win, bt_ma, bt_macd, bt_all,
                buyhold_full, data_json, sig_json, eq_json):
    lv = a["levels"]
    last = a["last"]
    w0, w1 = a["w0"], a["w1"]
    q_px = quote["price"]
    targets = lv["targets"]
    t1 = targets[1] if len(targets) > 1 else targets[0]
    tgt_s = " / ".join(f"{t:.2f}({(t / q_px - 1) * 100:+.1f}%)" for t in targets[:3])
    reasons = "、".join(a["reasons"]) if a["reasons"] else "无明显趋势特征"
    med_vol = sorted([r["volume"] for r in win])[len(win) // 2]
    vol_up = sum(1 for r in win if r["close"] > r["open"])

    def pf_s(bt):
        return "∞" if bt["profit_factor"] is None else f"{bt['profit_factor']:.2f}"

    def trade_rows(bt):
        return "".join(
            f"<tr><td>{t['entry_date']}</td><td>{t['entry_price']}</td>"
            f"<td>{t['exit_date']}</td><td>{t['exit_price']}</td>"
            f"<td class=\"{'up' if t['ret'] > 0 else 'down'}\">{t['ret']:+.2f}%</td></tr>"
            for t in bt["trades"][:20])

    up_rows = "".join(
        f"<tr><td>{r['date']}</td><td>{r['close']:.2f}</td>"
        f"<td>{r['pct_change']:+}%</td><td>{r['volume'] / 1e4:.0f}万股</td>"
        f"<td>{r['amount_yi']:.2f}亿</td><td>{r['turnover']:.2f}%</td>"
        f"<td>{r['vol_ratio']:.2f}</td><td>{'放量上涨' if r['close'] > r['open'] else '放量下跌'}</td></tr>"
        for r in a["up_list"])
    top_rows = "".join(
        f"<tr><td>{r['date']}</td><td>{r['close']:.2f}</td>"
        f"<td>{r['pct_change']:+}%</td><td>{r['volume'] / 1e4:.0f}万股</td>"
        f"<td>{r['amount_yi']:.2f}亿</td><td>{r['turnover']:.2f}%</td>"
        f"<td>{r['vol_ratio']:.2f}</td></tr>"
        for r in a["top_vol"])
    month_rows = "".join(
        f"<tr><td>{k}</td><td>{v['open']:.2f}</td><td>{v['close']:.2f}</td>"
        f"<td>{v['high']:.2f}</td><td>{v['low']:.2f}</td><td>{v['pct']:+.2f}%</td>"
        f"<td>{v['vol'] / 1e8:.2f}亿股</td><td>{v['amt']:.2f}亿</td></tr>"
        for k, v in sorted(a["monthly"].items()))
    sig_state = "持仓中" if bt_all["trades"] and bt_all["trades"][-1].get("open") else (
        f"最近{('买入' if a['last_sig'][1] == 'B' else '卖出')}信号 @ {a['last_sig'][0]}"
        if a["last_sig"] else "无信号")
    now = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>联德股份(605060) 量化交易分析报告</title>
<script src="echarts.min.js"></script>
<script>if (typeof echarts === 'undefined') {{ document.write('<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"><\\/script>'); }}</script>
<style>
  :root {{ --bg:#f5f6f8; --card:#ffffff; --line:#e6e9ef; --up:#e11d48; --down:#059669; --txt:#1f2937; --sub:#6b7280; --accent:#2563eb; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:radial-gradient(circle at 12% -5%, rgba(37,99,235,.08), transparent 38%), radial-gradient(circle at 88% -5%, rgba(6,182,212,.07), transparent 36%), var(--bg); color:var(--txt); font-family:"Microsoft YaHei","PingFang SC",sans-serif; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:34px 28px 64px; }}
  h1 {{ font-size:26px; font-weight:700; letter-spacing:.5px; }}
  h2 {{ font-size:21px; margin:12px 0 4px; font-weight:700; }}
  h3 {{ font-size:15px; margin:30px 0 10px; color:var(--accent); font-weight:600; padding-left:10px; border-left:3px solid var(--accent); }}
  .sub {{ color:var(--sub); font-size:13px; margin-top:4px; line-height:1.7; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(142px,1fr)); gap:12px; margin-top:16px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 12px; text-align:center; box-shadow:0 1px 3px rgba(15,23,42,.05); }}
  .card .num {{ font-size:20px; font-weight:700; margin-bottom:4px; }}
  .card .lbl {{ font-size:12px; color:var(--sub); }}
  .up {{ color:var(--up); }} .down {{ color:var(--down); }}
  .chart {{ background:var(--card); border:1px solid var(--line); border-radius:14px; height:460px; margin-top:12px; box-shadow:0 1px 3px rgba(15,23,42,.05); }}
  .panel {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 20px; margin-top:12px; box-shadow:0 1px 3px rgba(15,23,42,.05); }}
  .zone {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; margin-top:12px; }}
  .zone .item {{ background:#fbfcfe; border:1px solid var(--line); border-radius:12px; padding:16px; }}
  .zone .item h3 {{ font-size:14px; margin:0 0 10px; color:#374151; border:none; padding:0; }}
  .zone .item p {{ font-size:13px; color:var(--sub); line-height:1.9; }}
  .zone .item .big {{ font-size:16px; color:var(--txt); font-weight:600; }}
  .tag {{ display:inline-block; padding:3px 12px; border-radius:999px; font-size:12px; margin-left:8px; border:1px solid; vertical-align:middle; font-weight:600; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card); border-radius:12px; overflow:hidden; margin-top:12px; font-size:13px; box-shadow:0 1px 3px rgba(15,23,42,.05); }}
  th,td {{ padding:9px 12px; text-align:right; border-bottom:1px solid #eef1f6; white-space:nowrap; }}
  th {{ background:#f1f5f9; color:#475569; font-weight:600; }} td:first-child,th:first-child {{ text-align:left; }}
  tr:hover td {{ background:#f8fafc; }}
  .flex {{ display:flex; gap:14px; flex-wrap:wrap; }}
  .half {{ flex:1; min-width:340px; }}
  .warn {{ background:#fffbeb; border:1px solid #fde68a; border-radius:12px; padding:13px 18px; margin-top:14px; font-size:13px; color:#92400e; line-height:1.8; }}
  @media (max-width:700px) {{ h1 {{ font-size:20px; }} .wrap {{ padding:22px 14px 44px; }} }}
</style>
</head>
<body><div class="wrap">
<h1>联德股份（605060.SH · 联德精密）量化交易分析报告</h1>
<div class="sub">数据截至 {w1['date']} 收盘 · 数据来源：新浪财经/腾讯证券 · 分析窗口：近半年（{w0['date']} ~ {w1['date']}） · 前复权日K线 · 生成时间 {now}</div>

<h3>一、核心数据</h3>
<div class="grid">
  <div class="card"><div class="num">{q_px:.2f}</div><div class="lbl">现价（元）</div></div>
  <div class="card"><div class="num {'up' if quote['change_pct'] >= 0 else 'down'}">{quote['change_pct']:+.2f}%</div><div class="lbl">当日涨跌</div></div>
  <div class="card"><div class="num {'up' if a['half_ret'] >= 0 else 'down'}">{a['half_ret']:+.2f}%</div><div class="lbl">近半年涨幅</div></div>
  <div class="card"><div class="num">{a['hi']:.2f}</div><div class="lbl">区间最高</div></div>
  <div class="card"><div class="num">{a['lo']:.2f}</div><div class="lbl">区间最低</div></div>
  <div class="card"><div class="num down">-{a['mdd']}%</div><div class="lbl">最大回撤</div></div>
  <div class="card"><div class="num">{a['ann_vol']}%</div><div class="lbl">年化波动率</div></div>
  <div class="card"><div class="num">{quote['turnover_pct']:.2f}%</div><div class="lbl">当日换手率</div></div>
</div>
<div class="grid">
  <div class="card"><div class="num">{quote['vol_ratio']:.2f}</div><div class="lbl">当日量比</div></div>
  <div class="card"><div class="num">{quote['amount_wan'] / 1e4:.2f} 亿</div><div class="lbl">当日成交额</div></div>
  <div class="card"><div class="num">{quote['pe']:.1f}</div><div class="lbl">市盈率(TTM)</div></div>
  <div class="card"><div class="num">{quote['pb']:.2f}</div><div class="lbl">市净率</div></div>
  <div class="card"><div class="num">{quote['total_mv_yi']:.1f} 亿</div><div class="lbl">总市值</div></div>
  <div class="card"><div class="num">{a['avg_vol'] / 1e4:.0f} 万股</div><div class="lbl">日均成交量</div></div>
  <div class="card"><div class="num">{med_vol / 1e4:.0f} 万股</div><div class="lbl">成交量中位数</div></div>
  <div class="card"><div class="num">{a['avg_turn']:.2f}%</div><div class="lbl">日均换手率</div></div>
</div>
<div class="panel"><b>趋势判断：{a['verdict']}（评分 {a['score']}/6，维度：趋势{a['dims']['trend']}/动量{a['dims']['momentum']}/量能{a['dims']['volume']}；RSI {a['rsi_state']}，KDJ {a['kdj_state']}）</b>
  <div class="sub" style="line-height:1.9;margin-top:5px;">依据：{reasons}；
  MA5 {last['ma5']:.2f} / MA10 {last['ma10']:.2f} / MA20 {last['ma20']:.2f} / MA60 {last['ma60']:.2f}；
  DIF {last['dif']:.2f} / DEA {last['dea']:.2f}；RSI {last['rsi']:.0f}；KDJ K {last['k']:.0f} / D {last['d']:.0f} / J {last['j']:.0f}；
  布林 {last['blo']:.2f} ~ {last['bup']:.2f}；ATR {last['atr']:.2f} 元（日内波动约±{last['atr']:.1f}元）。</div>
</div>

<h3>二、近一年K线与技术指标</h3>
<div class="chart" id="kline"></div>
<div class="sub">红三角=趋势动量策略买点，绿三角=卖点；成交量单位：万股；MA5/MA10/MA20/MA60</div>
<div class="chart" id="indicators" style="margin-top:14px;"></div>

<h3>三、量化策略回测（{rows[0]['date']} ~ {w1['date']}，信号次日开盘执行，未计交易成本）</h3>
<div class="panel"><b>策略说明</b>
  <div class="sub" style="line-height:1.9;margin-top:5px;">
  ① 双均线：MA5上穿MA20且收盘站上MA20买入，下穿卖出；<br>
  ② MACD+量能：DIF上穿DEA且当日量≥5日均量买入，下穿卖出；<br>
  ③ 趋势动量（推荐）：收盘站上上行的MA20 + DIF位于DEA上方 + 成交量≥5日均量0.9倍 + 当日收涨，四条件同时满足买入；
  收盘跌破MA10，或自持仓以来最高收盘价回撤12%，任一条件满足即卖出。
  </div>
</div>
<table>
  <tr><th>策略</th><th>交易次数</th><th>胜率</th><th>总收益</th><th>最大回撤</th><th>盈亏比</th></tr>
  <tr><td>双均线 (MA5/MA20)</td><td>{bt_ma['n']}</td><td>{bt_ma['win_rate']}%</td><td>{bt_ma['total_ret']}%</td><td>-{bt_ma['max_drawdown']}%</td><td>{pf_s(bt_ma)}</td></tr>
  <tr><td>MACD+量能</td><td>{bt_macd['n']}</td><td>{bt_macd['win_rate']}%</td><td>{bt_macd['total_ret']}%</td><td>-{bt_macd['max_drawdown']}%</td><td>{pf_s(bt_macd)}</td></tr>
  <tr><td>趋势动量策略</td><td>{bt_all['n']}</td><td>{bt_all['win_rate']}%</td><td>{bt_all['total_ret']}%</td><td>-{bt_all['max_drawdown']}%</td><td>{pf_s(bt_all)}</td></tr>
</table>
<div class="flex">
  <div class="half"><table><tr><th colspan="3">趋势动量策略逐笔交易（最多20笔）</th></tr>
  <tr><th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th><th>收益率</th></tr>{trade_rows(bt_all)}</table></div>
  <div class="half"><div class="chart" style="height:340px" id="equity"></div></div>
</div>
<div class="panel small" style="line-height:1.8;">
  对照：同一回测区间买入持有收益 <b>{buyhold_full:+.2f}%</b>；当前策略状态：<b>{sig_state}</b>。
  回测未计交易成本，历史表现不代表未来收益。
</div>

<h3>四、详细交易量分析</h3>
<div class="grid">
  <div class="card"><div class="num">{a['max_vol'] / 1e4:.0f} 万股</div><div class="lbl">半年最大单日量</div></div>
  <div class="card"><div class="num">{a['min_vol'] / 1e4:.0f} 万股</div><div class="lbl">半年最小单日量</div></div>
  <div class="card"><div class="num">{a['up_days']}</div><div class="lbl">放量日(≥1.5倍)</div></div>
  <div class="card"><div class="num">{a['dn_days']}</div><div class="lbl">缩量日(≤0.6倍)</div></div>
  <div class="card"><div class="num">{vol_up}/{len(win)}</div><div class="lbl">上涨天数/总天数</div></div>
</div>
<div class="panel"><b>量价规律小结</b>
  <div class="sub" style="line-height:1.9;margin-top:5px;">
  该股近半年日均换手率 {a['avg_turn']:.2f}%、年化波动率 {a['ann_vol']}%，属于高波动活跃品种。
  放量日以“放量上涨加速”和“放量下跌见顶”两种形态为主；缩量回调后若重新温和放量（≥5日均量）并站上MA20，是较典型的趋势买点信号。
  最新（{w1['date']}）成交 {w1['volume'] / 1e4:.0f} 万股、量比 {quote['vol_ratio']:.2f}，
  说明当前处于{('放量' if quote['vol_ratio'] >= 1.2 else '缩量')}运行状态。
  </div>
</div>
<div class="flex">
  <div class="half"><table><tr><th colspan="8">放量日明细（前15，量比≥1.5）</th></tr>
  <tr><th>日期</th><th>收盘</th><th>涨跌幅</th><th>成交量</th><th>成交额</th><th>换手率</th><th>量比</th><th>性质</th></tr>{up_rows}</table></div>
  <div class="half"><table><tr><th colspan="7">成交额前10日</th></tr>
  <tr><th>日期</th><th>收盘</th><th>涨跌幅</th><th>成交量</th><th>成交额</th><th>换手率</th><th>量比</th></tr>{top_rows}</table></div>
</div>

<h3>五、月度表现</h3>
<table><tr><th>月份</th><th>开盘</th><th>收盘</th><th>最高</th><th>最低</th><th>月涨跌</th><th>月成交量</th><th>月成交额</th></tr>{month_rows}</table>

<h3>六、买卖点建议（基于最新技术状态）</h3>
<div class="zone">
  <div class="item"><h3>支撑位</h3><p><span class="big">S1 {lv['s1']:.2f}</span> 近5日低点<br>S2 {lv['s2']:.2f} 近20日低点<br>S3 {lv['s3']:.2f} 近60日低点</p></div>
  <div class="item"><h3>买点 / 止损</h3><p><span class="big">{lv['buy'][0]:.2f} ~ {lv['buy'][1]:.2f}</span> 稳健买点（MA5支撑带）<br>止损 {lv['stop']:.2f} · 硬止损 {lv['hard_stop']:.2f}</p></div>
  <div class="item"><h3>目标位</h3><p><span class="big">{tgt_s}</span><br>MA5 {last['ma5']:.2f} · MA20 {last['ma20']:.2f} · MA60 {last['ma60']:.2f}</p></div>
  <div class="item"><h3>仓位与纪律</h3><p>单票仓位建议≤15%，分2~3批建仓；到第一目标减半仓；破止损无条件执行，禁止重仓追高。</p></div>
</div>

<h3>七、后续涨幅预期（情景测算，非收益承诺）</h3>
<div class="panel">
  <div class="sub" style="line-height:1.9;">
  基于趋势动量策略回测（胜率{bt_all['win_rate']}%、总收益{bt_all['total_ret']}%）与当前技术结构：
  <br>· <b>保守情景</b>：修复至 {targets[0]:.2f} 元，对应 {(targets[0] / q_px - 1) * 100:+.1f}%；
  <br>· <b>中性情景</b>：趋势延续至 {t1:.2f} 元，对应 {(t1 / q_px - 1) * 100:+.1f}%；
  <br>· <b>乐观情景</b>：突破至 {targets[-1]:.2f} 元，对应 {(targets[-1] / q_px - 1) * 100:+.1f}%。
  <br>判断依据：MA20/MA60 方向、MACD 零轴位置、突破时成交量能否放大（≥5日均量1.2倍）；缩量滞涨则反弹结束概率上升。
  </div>
</div>

<div class="warn">⚠️ 风险提示：本报告由公开行情数据自动生成，仅作量化研究参考，不构成任何投资建议。
该股波动较大（年化波动率 {a['ann_vol']}%），请结合公司基本面、大盘环境与个人风险承受能力独立判断；严格执行止损纪律。</div>

<div class="sub" style="margin-top:16px;">生成时间: {now} · 数据来源: 新浪/腾讯/东方财富行情 · 仅供研究参考</div>
</div>
<script>
const DATA = {data_json};
const SIG = {sig_json};
const EQ = {eq_json};
const dates = DATA.map(d=>d[0]);
const kdata = DATA.map(d=>[d[1],d[2],d[3],d[4]]);
const vols = DATA.map(d=>d[5]);
const ma5 = DATA.map(d=>d[6]), ma10 = DATA.map(d=>d[7]), ma20 = DATA.map(d=>d[8]), ma60 = DATA.map(d=>d[9]);
const dif = DATA.map(d=>d[10]), dea = DATA.map(d=>d[11]), hist = DATA.map(d=>d[12]);
const rsi = DATA.map(d=>d[13]), kk = DATA.map(d=>d[14]), dd = DATA.map(d=>d[15]), jj = DATA.map(d=>d[16]);
const buys = SIG.filter(s=>s[2]==='B').map(s=>[s[0], s[1]]);
const sells = SIG.filter(s=>s[2]==='S').map(s=>[s[0], s[1]]);
function mk(id, opt) {{ const c = echarts.init(document.getElementById(id)); c.setOption(opt); return c; }}
mk('kline', {{
  tooltip:{{trigger:'axis', axisPointer:{{type:'cross'}}}},
  legend:{{data:['K线','MA5','MA10','MA20','MA60','买点','卖点'], textStyle:{{color:'#475569'}}}},
  grid:[{{left:60,right:16,top:30,height:'56%'}},{{left:60,right:16,top:'74%',height:'18%'}}],
  xAxis:[{{type:'category',data:dates,axisLabel:{{color:'#94a3b8'}}}},
         {{type:'category',gridIndex:1,data:dates,axisLabel:{{show:false}}}}],
  yAxis:[{{scale:true,axisLabel:{{color:'#94a3b8'}}}},{{gridIndex:1,axisLabel:{{color:'#94a3b8'}}}}],
  dataZoom:[{{type:'inside',xAxisIndex:[0,1],start:55,end:100}}],
  series:[
    {{name:'K线',type:'candlestick',data:kdata,itemStyle:{{color:'#e11d48',color0:'#059669',borderColor:'#e11d48',borderColor0:'#059669'}}}},
    {{name:'MA5',type:'line',data:ma5,symbol:'none',lineStyle:{{width:1,color:'#f0b90b'}}}},
    {{name:'MA10',type:'line',data:ma10,symbol:'none',lineStyle:{{width:1,color:'#5dade2'}}}},
    {{name:'MA20',type:'line',data:ma20,symbol:'none',lineStyle:{{width:1,color:'#af7ac5'}}}},
    {{name:'MA60',type:'line',data:ma60,symbol:'none',lineStyle:{{width:1,color:'#f1948a'}}}},
    {{name:'成交量',type:'bar',xAxisIndex:1,yAxisIndex:1,data:vols,itemStyle:{{color:function(p){{return DATA[p.dataIndex][2]>=DATA[p.dataIndex][1]?'#e11d4888':'#05966988'}}}}}},
    {{name:'买点',type:'scatter',data:buys,symbol:'triangle',symbolSize:12,itemStyle:{{color:'#e11d48'}}}},
    {{name:'卖点',type:'scatter',data:sells,symbol:'triangle',symbolRotate:180,symbolSize:12,itemStyle:{{color:'#059669'}}}}
  ]
}});
mk('indicators', {{
  tooltip:{{trigger:'axis'}},
  legend:{{data:['DIF','DEA','MACD','RSI','K','D','J'],textStyle:{{color:'#475569'}}}},
  grid:[{{left:60,right:16,top:30,height:'26%'}},{{left:60,right:16,top:'42%',height:'22%'}},{{left:60,right:16,top:'76%',height:'18%'}}],
  xAxis:[{{type:'category',data:dates,axisLabel:{{color:'#94a3b8'}}}},
         {{type:'category',gridIndex:1,data:dates,axisLabel:{{show:false}}}},
         {{type:'category',gridIndex:2,data:dates,axisLabel:{{show:false}}}}],
  yAxis:[{{scale:true,axisLabel:{{color:'#94a3b8'}}}},{{gridIndex:1,min:0,max:100,axisLabel:{{color:'#94a3b8'}}}},{{gridIndex:2,axisLabel:{{color:'#94a3b8'}}}}],
  dataZoom:[{{type:'inside',xAxisIndex:[0,1,2],start:55,end:100}}],
  series:[
    {{name:'DIF',type:'line',data:dif,symbol:'none',lineStyle:{{width:1,color:'#f0b90b'}}}},
    {{name:'DEA',type:'line',data:dea,symbol:'none',lineStyle:{{width:1,color:'#5dade2'}}}},
    {{name:'MACD',type:'bar',data:hist,itemStyle:{{color:function(p){{return p.data>=0?'#e11d4888':'#05966988'}}}}}},
    {{name:'RSI',type:'line',xAxisIndex:1,yAxisIndex:1,data:rsi,symbol:'none',lineStyle:{{width:1,color:'#58d68d'}}}},
    {{name:'K',type:'line',xAxisIndex:2,yAxisIndex:2,data:kk,symbol:'none',lineStyle:{{width:1,color:'#f0b90b'}}}},
    {{name:'D',type:'line',xAxisIndex:2,yAxisIndex:2,data:dd,symbol:'none',lineStyle:{{width:1,color:'#5dade2'}}}},
    {{name:'J',type:'line',xAxisIndex:2,yAxisIndex:2,data:jj,symbol:'none',lineStyle:{{width:1,color:'#af7ac5'}}}}
  ]
}});
mk('equity', {{
  tooltip:{{trigger:'axis'}},
  legend:{{data:['趋势动量策略资金曲线'],textStyle:{{color:'#475569'}}}},
  grid:{{left:60,right:16,top:30,bottom:30}},
  xAxis:{{type:'category',data:EQ.map(e=>e[0]),axisLabel:{{color:'#94a3b8'}}}},
  yAxis:{{scale:true,axisLabel:{{color:'#94a3b8'}}}},
  dataZoom:[{{type:'inside',start:0,end:100}}],
  series:[{{name:'趋势动量策略资金曲线',type:'line',data:EQ.map(e=>+(e[1]*100).toFixed(2)),symbol:'none',
    areaStyle:{{color:'rgba(37,99,235,.10)'}},lineStyle:{{color:'#2563eb',width:2}}}}]
}});
</script>
</body></html>"""


if __name__ == "__main__":
    main()
