# -*- coding: utf-8 -*-
"""
通用个股量化分析引擎
抓取行情 -> 技术指标 -> 趋势动量策略回测 -> 买卖点 -> HTML片段
"""
import json
import math
import re
import time
import datetime

import requests
import v2  # noqa: E402  v2 规范分析模块

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126",
    "Referer": "https://finance.sina.com.cn",
}

# 交易成本（佣金+印花税+过户费+滑点），往返 0.30%，单边取 0.15%
COST_RATE = 0.0015


# ---------------- 数据抓取 ----------------
def _sina_symbol(code):
    c = code.strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "").upper()
    if c.startswith(("6", "9")):
        return f"sh{c}"
    if c.startswith(("4", "8")):
        return f"bj{c}"
    return f"sz{c}"


def _tencent_symbol(code):
    return _sina_symbol(code)


def fetch_quote(code):
    """腾讯实时行情: 名称/现价/涨跌/量比/换手/PE/市值"""
    sym = _tencent_symbol(code)
    r = requests.get(f"https://qt.gtimg.cn/q={sym}", headers=HEADERS, timeout=20)
    r.encoding = "gbk"
    f = r.text.strip().split("~")
    if len(f) < 46:
        raise RuntimeError(f"行情解析失败: {code}")
    price = float(f[3])
    return {
        "name": f[1], "code": f[2], "price": price,
        "change_pct": float(f[32]), "amount_wan": float(f[37]),
        "turnover_pct": float(f[38]), "pe": float(f[39]),
        "total_mv_yi": float(f[45]), "circ_mv_yi": float(f[44]),
        "vol_ratio": float(f[49]), "pb": float(f[46]),
        "date_time": f[30],
    }


def fetch_kline(code, datalen=260):
    """新浪日K线(前复权), volume单位=股; 带重试"""
    sym = _sina_symbol(code)
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_/"
           "CN_MarketDataService.getKLineData"
           f"?symbol={sym}&scale=240&ma=no&datalen={datalen}")
    last_err = None
    for _ in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            m = re.search(r"\((\[.*\])\)\s*;?\s*$", r.text, re.S)
            if not m:
                raise RuntimeError("K线格式异常")
            rows = json.loads(m.group(1))
            if not rows:
                raise RuntimeError("K线为空")
            return [{
                "date": x["day"],
                "open": float(x["open"]), "high": float(x["high"]),
                "low": float(x["low"]), "close": float(x["close"]),
                "volume": float(x["volume"]),
            } for x in rows]
        except Exception as e:
            last_err = e
            time.sleep(2.5)
    raise RuntimeError(f"K线抓取失败: {last_err}")


# ---------------- 指标 ----------------
def sma(vals, n):
    out = [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(vals, n):
    out = [None] * len(vals)
    k = 2.0 / (n + 1)
    e = None
    for i, v in enumerate(vals):
        e = v if e is None else v * k + e * (1 - k)
        out[i] = e
    return out


def macd(closes):
    e12, e26 = ema(closes, 12), ema(closes, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    hist = [(a - b) * 2 for a, b in zip(dif, dea)]
    return dif, dea, hist


def rsi(closes, n=14):
    out = [None] * len(closes)
    gain = loss = 0.0
    for i in range(1, len(closes)):
        chg = closes[i] - closes[i - 1]
        g, l = max(chg, 0.0), max(-chg, 0.0)
        if i <= n:
            gain += g
            loss += l
            if i == n:
                gain /= n
                loss /= n
                out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
        else:
            gain = (gain * (n - 1) + g) / n
            loss = (loss * (n - 1) + l) / n
            out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def kdj(rows, n=9):
    k = d = 50.0
    ks, ds, js = [], [], []
    for i, r in enumerate(rows):
        lo = min(x["low"] for x in rows[max(0, i - n + 1):i + 1])
        hi = max(x["high"] for x in rows[max(0, i - n + 1):i + 1])
        rsv = 50.0 if hi == lo else (r["close"] - lo) / (hi - lo) * 100
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
        ks.append(k)
        ds.append(d)
        js.append(3 * k - 2 * d)
    return ks, ds, js


def boll(closes, n=20, m=2):
    mid = sma(closes, n)
    up, lo = [None] * len(closes), [None] * len(closes)
    for i in range(n - 1, len(closes)):
        seg = closes[i - n + 1:i + 1]
        mean = sum(seg) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in seg) / n)
        up[i], lo[i] = mean + m * sd, mean - m * sd
    return up, mid, lo


def atr(rows, n=14):
    out = [None] * len(rows)
    trs = [0.0]
    for i in range(1, len(rows)):
        tr = max(rows[i]["high"] - rows[i]["low"],
                 abs(rows[i]["high"] - rows[i - 1]["close"]),
                 abs(rows[i]["low"] - rows[i - 1]["close"]))
        trs.append(tr)
    a = sum(trs[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(rows)):
        a = (a * (n - 1) + trs[i]) / n
        out[i] = a
    return out


# ---------------- 策略回测 ----------------
def trend_signals(rows, ma5, ma10, ma20, dif, dea, vol_ma5, trail=0.12, vol_ratio=0.9):
    """趋势动量: 站上上行MA20+DIF>DEA+量能+收涨买入;
    破MA10或自持仓以来盘中最高价回撤trail卖出（盘中高点，当日收盘时已知，无未来数据）"""
    sig = [None] * len(rows)
    in_pos, maxc = False, 0.0
    for i in range(27, len(rows)):
        r = rows[i]
        if in_pos:
            maxc = max(maxc, rows[i]["high"])
            if (ma10[i] is not None and
                    (r["close"] < maxc * (1 - trail) or r["close"] < ma10[i])):
                sig[i] = "S"
                in_pos = False
        else:
            ok = (vol_ma5[i] is not None and ma20[i] is not None
                  and ma20[i - 5] is not None and dif[i] is not None
                  and dea[i] is not None
                  and r["volume"] >= vol_ma5[i] * vol_ratio and r["close"] > ma20[i]
                  and ma20[i] > ma20[i - 5] and dif[i] > dea[i]
                  and r["close"] > rows[i - 1]["close"])
            if ok:
                sig[i] = "B"
                in_pos = True
                maxc = r["close"]
    return sig


def backtest(rows, signals):
    trades = []
    pos = 0.0
    entry_i = None
    cash, shares = 1.0, 0.0
    eq = []
    for i in range(1, len(rows)):
        sig = signals[i - 1]
        if sig == "B" and pos == 0:
            entry_i = i
            pos = rows[i]["open"] * (1 + COST_RATE)      # 含成本实际买入价
            shares = cash / pos
            cash = 0.0
        elif sig == "S" and pos > 0:
            exit_px = rows[i]["open"] * (1 - COST_RATE)  # 含成本实际卖出价
            trades.append({
                "entry_date": rows[entry_i]["date"],
                "entry_price": round(pos, 2),
                "exit_date": rows[i]["date"],
                "exit_price": round(exit_px, 2),
                "ret": round((exit_px - pos) / pos * 100, 2),
            })
            cash = shares * exit_px
            shares = 0.0
            pos = 0.0
            entry_i = None
        eq.append(cash + shares * rows[i]["close"])
    if pos > 0:
        mark_px = rows[-1]["close"] * (1 - COST_RATE)
        trades.append({
            "entry_date": rows[entry_i]["date"],
            "entry_price": round(pos, 2),
            "exit_date": "持仓中",
            "exit_price": round(mark_px, 2),
            "ret": round((mark_px - pos) / pos * 100, 2),
            "open": True,
        })
    wins = [t for t in trades if t["ret"] > 0]
    losses = [t for t in trades if t["ret"] <= 0]
    gross_win = sum(t["ret"] for t in wins)
    gross_loss = abs(sum(t["ret"] for t in losses))
    total_ret = math.prod(1 + t["ret"] / 100 for t in trades) - 1
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    max_e, mdd = 0.0, 0.0
    for v in eq:
        max_e = max(max_e, v)
        mdd = max(mdd, (max_e - v) / max_e)
    return {
        "trades": trades, "n": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0,
        "profit_factor": round(pf, 2) if pf != float("inf") else None,
        "total_ret": round(total_ret * 100, 2),
        "max_drawdown": round(mdd * 100, 2),
        "eq": eq,
    }


# ---------------- 综合分析 ----------------
def analyze(code):
    """返回完整分析结果 dict"""
    quote = fetch_quote(code)
    rows = fetch_kline(code)
    closes = [r["close"] for r in rows]
    ma5 = sma(closes, 5)
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)
    dif, dea, hist = macd(closes)
    rsi14 = rsi(closes)
    ks, ds, js = kdj(rows)
    bup, bmid, blo = boll(closes)
    atrs = atr(rows)
    vol_ma5 = sma([r["volume"] for r in rows], 5)

    float_shares = quote["circ_mv_yi"] * 1e8 / quote["price"]
    for i, r in enumerate(rows):
        r["ma5"], r["ma10"], r["ma20"], r["ma60"] = ma5[i], ma10[i], ma20[i], ma60[i]
        r["dif"], r["dea"], r["hist"] = dif[i], dea[i], hist[i]
        r["rsi"], r["k"], r["d"], r["j"] = rsi14[i], ks[i], ds[i], js[i]
        r["atr"], r["vol_ma5"] = atrs[i], vol_ma5[i]
        r["bup"], r["bmid"], r["blo"] = bup[i], bmid[i], blo[i]
        r["vol_ratio"] = round(r["volume"] / vol_ma5[i], 2) if vol_ma5[i] else None
        r["turnover"] = r["volume"] / float_shares * 100
        r["amount_yi"] = r["volume"] * (r["open"] + r["high"] + r["low"] + r["close"]) / 4 / 1e8
    for i, r in enumerate(rows):
        prev = rows[i - 1]["close"] if i else r["open"]
        r["pct_change"] = round((r["close"] / prev - 1) * 100, 2)

    # 近半年窗口
    start = (datetime.date.today() - datetime.timedelta(days=183)).strftime("%Y-%m-%d")
    win = [r for r in rows if r["date"] >= start] or rows[-120:]
    w0, w1 = win[0], win[-1]
    half_ret = (w1["close"] / w0["open"] - 1) * 100
    hi = max(r["high"] for r in win)
    lo = min(r["low"] for r in win)
    peak, mdd = 0.0, 0.0
    for r in win:
        peak = max(peak, r["close"])
        mdd = max(mdd, (peak - r["close"]) / peak)
    daily = [r["close"] / r["open"] - 1 for r in win]
    sd = math.sqrt(sum((x - sum(daily) / len(daily)) ** 2 for x in daily) / len(daily))
    ann_vol = sd * math.sqrt(252) * 100
    vols = [r["volume"] for r in win]

    # 回测
    sig = trend_signals(rows, ma5, ma10, ma20, dif, dea, vol_ma5)
    bt = backtest(rows, sig)
    eq_curve = [[rows[i + 1]["date"], round(bt["eq"][i], 4)]
                for i in range(len(bt["eq"]))]
    last_sig = None
    for i in range(len(sig) - 1, 0, -1):
        if sig[i]:
            last_sig = (rows[i]["date"], sig[i])
            break

    # 三维度6分制趋势评分（RSI/KDJ 只做状态标签，不参与评分）
    last = rows[-1]
    score = 0
    reasons = []
    d1 = d2 = d3 = 0
    if last["close"] > last["ma20"]:
        d1 += 1
        reasons.append("站上MA20")
    if last["ma20"] > last["ma60"]:
        d1 += 1
        reasons.append("MA20>MA60")
    if last["dif"] > last["dea"]:
        d2 += 1
        reasons.append("MACD金叉")
    if last["dif"] > 0:
        d2 += 1
        reasons.append("MACD零轴上方")
    if last["volume"] > last["vol_ma5"]:
        d3 += 1
        reasons.append("量>5日均量")
    if last["close"] > rows[-2]["close"]:
        d3 += 1
        reasons.append("当日收阳")
    score = d1 + d2 + d3
    dims = {"trend": d1, "momentum": d2, "volume": d3}
    if score >= 5:
        verdict, vcolor = "多头趋势", "#dc2626"
    elif score >= 3:
        verdict, vcolor = "偏多/震荡向上", "#ea580c"
    elif score >= 2:
        verdict, vcolor = "震荡", "#ca8a04"
    elif score >= 1:
        verdict, vcolor = "偏空/弱反弹", "#059669"
    else:
        verdict, vcolor = "空头趋势", "#0d9488"
    # RSI/KDJ 状态标签
    rsi_state = ("超买" if last["rsi"] >= 70 else
                 ("超卖" if last["rsi"] <= 30 else
                  ("高位" if last["rsi"] >= 60 else
                   ("低位" if last["rsi"] <= 40 else "中性"))))
    kdj_state = ("超买" if last["j"] >= 100 else
                 ("超卖" if last["j"] <= 0 else
                  ("金叉向上" if last["k"] > last["d"] else "死叉向下")))

    # 买卖点
    ref60 = rows[-60:]
    s1 = round(min(r["low"] for r in rows[-5:]), 2)
    s2 = round(min(r["low"] for r in ref60[-20:]), 2)
    s3 = round(min(r["low"] for r in ref60), 2)
    r1 = round(max(r["high"] for r in ref60[-20:]), 2)
    r3 = round(max(r["high"] for r in ref60), 2)
    buy_zone = [round(max(s1, last["ma5"] - 0.2), 2), round(last["ma5"] + 0.8, 2)]
    last_atr = last.get("atr") or 0
    stop = round(buy_zone[0] - 2 * last_atr, 2) if last_atr > 0 else round(s2 * 0.97, 2)
    hard_stop = round(buy_zone[0] - 3 * last_atr, 2) if last_atr > 0 else round(s3 * 0.96, 2)
    if stop <= 0:
        stop = round(s2 * 0.97, 2)
    if hard_stop <= 0:
        hard_stop = round(s3 * 0.96, 2)
    cand = sorted({round(x, 2) for x in [last["ma60"], r1, r3]})
    targets = [x for x in cand if x > quote["price"] * 1.005]
    if len(targets) < 2:
        targets = cand[-3:]
    r2 = targets[1] if len(targets) > 1 else targets[0]

    # 交易量统计
    vol_list = [r["volume"] for r in win]
    up_days = sum(1 for r in win if r["vol_ratio"] and r["vol_ratio"] >= 1.5)
    dn_days = sum(1 for r in win if r["vol_ratio"] and r["vol_ratio"] <= 0.6)
    float_shares = quote["circ_mv_yi"] * 1e8 / quote["price"]
    avg_turn = sum(r["volume"] / float_shares * 100 for r in win) / len(win)

    # 月度/放量明细/成交额榜
    monthly = {}
    for r in win:
        m = r["date"][:7]
        monthly.setdefault(m, {"open": r["open"], "close": r["close"],
                               "high": r["high"], "low": r["low"],
                               "vol": 0.0, "amt": 0.0})
        monthly[m]["close"] = r["close"]
        monthly[m]["high"] = max(monthly[m]["high"], r["high"])
        monthly[m]["low"] = min(monthly[m]["low"], r["low"])
        monthly[m]["vol"] += r["volume"]
        monthly[m]["amt"] += r["amount_yi"]
    for k in monthly:
        monthly[k]["pct"] = round((monthly[k]["close"] / monthly[k]["open"] - 1) * 100, 2)
    up_list = sorted([r for r in win if r["vol_ratio"] and r["vol_ratio"] >= 1.5],
                     key=lambda r: r["vol_ratio"], reverse=True)[:15]
    top_vol = sorted(win, key=lambda r: r["volume"], reverse=True)[:10]

    return {
        "code": code, "quote": quote, "rows": rows, "win": win,
        "w0": w0, "w1": w1, "half_ret": round(half_ret, 2),
        "hi": hi, "lo": lo, "mdd": round(mdd * 100, 2),
        "ann_vol": round(ann_vol, 1), "avg_vol": sum(vol_list) / len(vol_list),
        "max_vol": max(vol_list), "min_vol": min(vol_list),
        "up_days": up_days, "dn_days": dn_days, "avg_turn": avg_turn,
        "monthly": monthly, "up_list": up_list, "top_vol": top_vol,
        "eq_curve": eq_curve,
        "bt": bt, "last_sig": last_sig, "score": score,
        "verdict": verdict, "vcolor": vcolor, "reasons": reasons,
        "dims": dims, "rsi_state": rsi_state, "kdj_state": kdj_state,
        "last": last, "levels": {"s1": s1, "s2": s2, "s3": s3,
                                 "r1": r1, "r2": r2, "r3": r3,
                                 "buy": buy_zone, "stop": stop,
                                 "hard_stop": hard_stop, "targets": targets},
        "signals": [[rows[i]["date"], rows[i]["close"], s]
                    for i, s in enumerate(sig) if s],
    }


# ---------------- HTML片段 ----------------
def section_html(a, idx, tag=""):
    q = a["quote"]
    lv = a["levels"]
    bt = a["bt"]
    last = a["last"]
    q_px = q["price"]
    tgt_s = " / ".join(f"{t:.2f}({(t / q_px - 1) * 100:+.1f}%)" for t in lv["targets"])
    sig_state = "持仓中" if bt["trades"] and bt["trades"][-1].get("open") else (
        f"最近{('买入' if a['last_sig'][1] == 'B' else '卖出')}信号 @ {a['last_sig'][0]}" if a["last_sig"] else "无信号")
    pf = "∞" if bt["profit_factor"] is None else f"{bt['profit_factor']:.2f}"
    reasons = "、".join(a["reasons"]) if a["reasons"] else "无明显趋势特征"
    return f"""
<div class="stock">
  <h2>{q['name']}（{a['code']}）{tag} <span class="tag v" style="background:{a['vcolor']}22;color:{a['vcolor']};border-color:{a['vcolor']}">{a['verdict']}</span></h2>
  <div class="sub">数据截至 {a['w1']['date']} · 现价 {q_px:.2f} 元（{q['change_pct']:+.2f}%）· 换手 {q['turnover_pct']:.1f}% · 量比 {q['vol_ratio']:.2f} · PE {q['pe']:.1f} · 市值 {q['total_mv_yi']:.0f} 亿</div>
  <div class="grid">
    <div class="card"><div class="num {'up' if a['half_ret'] >= 0 else 'down'}">{a['half_ret']:+.2f}%</div>近半年涨幅</div>
    <div class="card"><div class="num">{a['hi']:.2f}</div>半年最高</div>
    <div class="card"><div class="num">{a['lo']:.2f}</div>半年最低</div>
    <div class="card"><div class="num down">-{a['mdd']}%</div>半年最大回撤</div>
    <div class="card"><div class="num">{a['ann_vol']}%</div>年化波动率</div>
    <div class="card"><div class="num">{a['avg_vol'] / 1e4:.0f}万股</div>日均成交量</div>
    <div class="card"><div class="num">{a['avg_turn']:.2f}%</div>日均换手率</div>
    <div class="card"><div class="num">{a['up_days']}/{a['dn_days']}</div>放量日/缩量日</div>
  </div>
  <div class="panel"><b>趋势判断：{a['verdict']}（评分 {a['score']}/6）</b>
    <div class="sub" style="line-height:1.8;margin-top:4px;">{reasons}；
    MA5 {last['ma5']:.2f} / MA10 {last['ma10']:.2f} / MA20 {last['ma20']:.2f} / MA60 {last['ma60']:.2f}；
    DIF {last['dif']:.2f} / DEA {last['dea']:.2f}；RSI {last['rsi']:.0f}；ATR {last['atr']:.2f} 元。</div>
  </div>
  <div class="zone">
    <div class="item"><h3>支撑位</h3><p>S1 {lv['s1']:.2f}（近5日低点）<br>S2 {lv['s2']:.2f}（近20日低点）<br>S3 {lv['s3']:.2f}（近60日低点）</p></div>
    <div class="item"><h3>买点</h3><p>稳健买点 {lv['buy'][0]:.2f} ~ {lv['buy'][1]:.2f} 元<br>止损 {lv['stop']:.2f} 元<br>硬止损 {lv['hard_stop']:.2f} 元</p></div>
    <div class="item"><h3>卖点/目标</h3><p>目标位：{tgt_s}<br>趋势卖点：破MA10且MACD收缩，或回撤超12%</p></div>
    <div class="item"><h3>策略回测(约1年)</h3><p>{bt['n']}笔 · 胜率{bt['win_rate']}% · 收益{bt['total_ret']}% · 回撤-{bt['max_drawdown']}% · 盈亏比{pf}<br>当前状态：{sig_state}</p></div>
  </div>
  <div class="chart" id="chk_{idx}"></div>
</div>"""


def chart_json(a):
    rows = a["rows"]
    return {
        "code": a["code"],
        "name": a["quote"]["name"],
        "dates": [r["date"] for r in rows],
        "k": [[r["open"], r["close"], r["low"], r["high"]] for r in rows],
        "vol": [r["volume"] / 1e4 for r in rows],
        "ma5": [r["ma5"] for r in rows],
        "ma20": [r["ma20"] for r in rows],
        "ma60": [r["ma60"] for r in rows],
        "sig": a["signals"],
    }


# ---------------- 全量详细版 HTML 片段 ----------------
def full_section_html(a, idx, tag=""):
    q = a["quote"]
    lv = a["levels"]
    bt = a["bt"]
    last = a["last"]
    q_px = q["price"]
    tgt_s = " / ".join(f"{t:.2f}({(t / q_px - 1) * 100:+.1f}%)" for t in lv["targets"])
    sig_state = "持仓中" if bt["trades"] and bt["trades"][-1].get("open") else (
        f"最近{('买入' if a['last_sig'][1] == 'B' else '卖出')}信号 @ {a['last_sig'][0]}"
        if a["last_sig"] else "无信号")
    pf = "∞" if bt["profit_factor"] is None else f"{bt['profit_factor']:.2f}"
    reasons = "、".join(a["reasons"]) if a["reasons"] else "无明显趋势特征"
    med_vol = sorted([r["volume"] for r in a["win"]])[len(a["win"]) // 2]
    vol_up = sum(1 for r in a["win"] if r["close"] > r["open"])

    def trade_rows(bt):
        rows = "".join(
            f"<tr><td>{t['entry_date']}</td><td>{t['entry_price']}</td>"
            f"<td>{t['exit_date']}</td><td>{t['exit_price']}</td>"
            f"<td class=\"{'up' if t['ret'] > 0 else 'down'}\">{t['ret']:+.2f}%</td></tr>"
            for t in bt["trades"][:15])
        if len(bt["trades"]) > 15:
            rows += f"<tr><td colspan='5'>…共 {len(bt['trades'])} 笔交易，仅显示前15笔</td></tr>"
        return rows

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

    return f"""
<div class="slide" id="slide{idx}">
  <h2>{q['name']}（{a['code']}）{tag}
    <span class="tag v" style="background:{a['vcolor']}22;color:{a['vcolor']};border-color:{a['vcolor']}">{a['verdict']}</span></h2>
  <div class="sub">数据截至 {a['w1']['date']} 收盘 · 现价 {q_px:.2f} 元（{q['change_pct']:+.2f}%）· 前复权日K线 · 分析窗口：近半年（{a['w0']['date']} ~ {a['w1']['date']}）</div>

  <div class="grid">
    <div class="card"><div class="num">{q_px:.2f}</div>现价</div>
    <div class="card"><div class="num {'up' if a['half_ret'] >= 0 else 'down'}">{a['half_ret']:+.2f}%</div>近半年涨幅</div>
    <div class="card"><div class="num">{a['hi']:.2f}</div>区间最高</div>
    <div class="card"><div class="num">{a['lo']:.2f}</div>区间最低</div>
    <div class="card"><div class="num down">-{a['mdd']}%</div>最大回撤</div>
    <div class="card"><div class="num">{a['ann_vol']}%</div>年化波动率</div>
    <div class="card"><div class="num">{q['turnover_pct']:.2f}%</div>当日换手率</div>
    <div class="card"><div class="num">{q['vol_ratio']:.2f}</div>当日量比</div>
  </div>
  <div class="grid">
    <div class="card"><div class="num">{q['amount_wan'] / 1e4:.2f} 亿</div>当日成交额</div>
    <div class="card"><div class="num">{q['pe']:.1f}</div>市盈率(TTM)</div>
    <div class="card"><div class="num">{q['pb']:.2f}</div>市净率</div>
    <div class="card"><div class="num">{q['total_mv_yi']:.1f} 亿</div>总市值</div>
    <div class="card"><div class="num">{a['avg_vol'] / 1e4:.0f} 万股</div>半年日均成交量</div>
    <div class="card"><div class="num">{med_vol / 1e4:.0f} 万股</div>成交量中位数</div>
    <div class="card"><div class="num">{a['avg_turn']:.2f}%</div>半年日均换手率</div>
    <div class="card"><div class="num">{vol_up}/{len(a['win'])}</div>上涨天数/总天数</div>
  </div>

  <h3>一、近半年走势与K线（含均线与买卖信号）</h3>
  <div class="chart" id="chk_{idx}_kline"></div>
  <div class="sub">红三角=趋势动量策略买点，绿三角=卖点；成交量单位：万股；MA5/MA10/MA20/MA60</div>

  <h3>二、技术指标（MACD / RSI / KDJ）</h3>
  <div class="chart" id="chk_{idx}_ind"></div>

  <h3>三、量化策略回测（{a['rows'][0]['date']} ~ {a['w1']['date']}，信号次日开盘执行）</h3>
  <div class="panel"><b>策略说明</b>
    <div class="sub" style="line-height:1.9;margin-top:5px;">
    趋势动量策略：收盘站上上行的MA20 + DIF位于DEA上方 + 成交量≥5日均量0.9倍 + 当日收涨，四条件同时满足买入；
    收盘跌破MA10，或自持仓以来最高收盘价回撤12%，任一条件满足即卖出（回测未计交易成本）。
    </div>
  </div>
  <div class="cards4">
    <div class="card"><div class="num">{bt['n']}</div>交易次数</div>
    <div class="card"><div class="num">{bt['win_rate']}%</div>胜率</div>
    <div class="card"><div class="num up">+{bt['avg_win']}%</div>平均盈利</div>
    <div class="card"><div class="num down">{bt['avg_loss']}%</div>平均亏损</div>
    <div class="card"><div class="num">{pf}</div>盈亏比</div>
    <div class="card"><div class="num">{bt['total_ret']}%</div>总收益</div>
    <div class="card"><div class="num down">-{bt['max_drawdown']}%</div>最大回撤</div>
    <div class="card"><div class="num">{sig_state}</div>当前状态</div>
  </div>
  <div class="flex">
    <div class="half"><table><tr><th colspan="3">逐笔交易（最多显示15笔）</th></tr>
    <tr><th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th><th>收益率</th></tr>{trade_rows(bt)}</table></div>
    <div class="half"><div class="chart" style="height:330px" id="chk_{idx}_eq"></div></div>
  </div>

  <h3>四、详细交易量分析</h3>
  <div class="grid">
    <div class="card"><div class="num">{a['max_vol'] / 1e4:.0f} 万股</div>半年最大单日量</div>
    <div class="card"><div class="num">{a['min_vol'] / 1e4:.0f} 万股</div>半年最小单日量</div>
    <div class="card"><div class="num">{a['up_days']}</div>放量日(≥1.5倍)</div>
    <div class="card"><div class="num">{a['dn_days']}</div>缩量日(≤0.6倍)</div>
  </div>
  <div class="panel"><b>量价规律小结</b>
    <div class="sub" style="line-height:1.9;margin-top:5px;">
    半年日均换手率 {a['avg_turn']:.2f}%。放量日以“放量上涨加速”和“放量下跌见顶”两种形态为主；
    缩量回调后若重新温和放量（≥5日均量）并站上MA20，是较典型的趋势买点信号。
    最新（{a['w1']['date']}）成交 {a['w1']['volume'] / 1e4:.0f} 万股、量比 {q['vol_ratio']:.2f}，
    说明当前 {('放量' if q['vol_ratio'] >= 1.2 else '缩量')} 运行。
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
  <div class="panel"><b>最新技术状态（{a['w1']['date']}收盘）</b>
    <div class="sub" style="line-height:1.9;margin-top:5px;">
    收盘 {last['close']:.2f} 元 · MA5 {last['ma5']:.2f} / MA10 {last['ma10']:.2f} / MA20 {last['ma20']:.2f} / MA60 {last['ma60']:.2f}；
    DIF {last['dif']:.2f} / DEA {last['dea']:.2f}；RSI {last['rsi']:.0f}；KDJ K {last['k']:.0f} / D {last['d']:.0f} / J {last['j']:.0f}；
    布林 {last['blo']:.2f} ~ {last['bup']:.2f}；ATR {last['atr']:.2f} 元（日内波动约±{last['atr']:.1f}元）。
    </div>
  </div>
  <div class="zone">
    <div class="item"><h3>支撑位</h3><p>S1 {lv['s1']:.2f}（近5日低点）<br>S2 {lv['s2']:.2f}（近20日低点）<br>S3 {lv['s3']:.2f}（近60日低点）</p></div>
    <div class="item"><h3>买点</h3><p>稳健买点 {lv['buy'][0]:.2f} ~ {lv['buy'][1]:.2f} 元（MA5支撑带）<br>止损 {lv['stop']:.2f} 元<br>硬止损 {lv['hard_stop']:.2f} 元</p></div>
    <div class="item"><h3>卖点/目标位</h3><p>目标位：{tgt_s}<br>趋势卖点：破MA10且MACD红柱收缩，或自高点回撤12%</p></div>
    <div class="item"><h3>仓位与纪律</h3><p>单票仓位建议≤15%，分2~3批建仓；到第一目标减半仓；破止损无条件执行，禁止重仓追高。</p></div>
  </div>

  <h3>七、后续涨幅预期</h3>
  <div class="panel">
    <div class="sub" style="line-height:1.9;">
    基于趋势动量策略回测（胜率{bt['win_rate']}%、总收益{bt['total_ret']}%）与当前技术结构，给出情景测算（非收益承诺）：
    <br>· <b>保守情景</b>：修复至 {lv['targets'][0]:.2f} 元，对应 {(lv['targets'][0] / q_px - 1) * 100:+.1f}%；
    <br>· <b>中性情景</b>：趋势延续至 {lv['targets'][1]:.2f} 元，对应 {(lv['targets'][1] / q_px - 1) * 100:+.1f}%；
    <br>· <b>乐观情景</b>：突破至 {lv['targets'][-1]:.2f} 元，对应 {(lv['targets'][-1] / q_px - 1) * 100:+.1f}%。
    <br>判断依据：MA20/MA60 方向、MACD 零轴位置、突破时成交量能否放大（≥5日均量1.2倍）；缩量滞涨则反弹结束概率上升。
    </div>
  </div>
</div>"""


def full_chart_json(a):
    rows = a["rows"]
    return {
        "code": a["code"],
        "name": a["quote"]["name"],
        "dates": [r["date"] for r in rows],
        "k": [[r["open"], r["close"], r["low"], r["high"]] for r in rows],
        "vol": [r["volume"] / 1e4 for r in rows],
        "ma5": [r["ma5"] for r in rows],
        "ma10": [r["ma10"] for r in rows],
        "ma20": [r["ma20"] for r in rows],
        "ma60": [r["ma60"] for r in rows],
        "dif": [r["dif"] for r in rows],
        "dea": [r["dea"] for r in rows],
        "hist": [r["hist"] for r in rows],
        "rsi": [r["rsi"] for r in rows],
        "kd": [r["k"] for r in rows],
        "dd": [r["d"] for r in rows],
        "jj": [r["j"] for r in rows],
        "sig": a["signals"],
        "eq": a["eq_curve"],
    }


# ---------------- 精简版(只股票数据) HTML 片段 ----------------
def lite_section_html(a, slide_idx, chart_idx, tag="", a2=None):
    q = a["quote"]
    lv = a["levels"]
    last = a["last"]
    q_px = q["price"]
    tgt_s = " / ".join(f"{t:.2f}" for t in lv["targets"])
    med_vol = sorted([r["volume"] for r in a["win"]])[len(a["win"]) // 2]
    vol_up = sum(1 for r in a["win"] if r["close"] > r["open"])
    chg_cls = "up" if q["change_pct"] >= 0 else "down"
    chg_txt = f"{q['change_pct']:+.2f}%"
    if a2:
        status = a2["status"]
        vcol = {"强势可入": "#dc2626", "强势观望": "#ea580c",
                "弱势": "#059669", "风险警示": "#7c3aed"}.get(status, "#6b7280")
        vtag = (f'<span class="tag v" style="background:{vcol}18;color:{vcol};'
                f'border-color:{vcol}">{status}</span>')
        v2_html = v2.render_block(a2, v1_levels=lv, v1=a)
        zone_html = '<div class="sub" style="margin-top:8px;">下方 v1 数据区仅供参考，交易决策以 v2 结论为准。</div>'
    else:
        vtag = (f'<span class="tag v" style="background:{a["vcolor"]}18;'
                f'color:{a["vcolor"]};border-color:{a["vcolor"]}">{a["verdict"]}</span>')
        v2_html = ""
        zone_html = f"""<h3>关键价位</h3>
  <div class="zone">
    <div class="item"><h3>支撑位</h3><p><span class="big">S1 {lv['s1']:.2f}</span> 近5日低点<br>S2 {lv['s2']:.2f} 近20日低点<br>S3 {lv['s3']:.2f} 近60日低点</p></div>
    <div class="item"><h3>买点 / 止损</h3><p><span class="big">{lv['buy'][0]:.2f} ~ {lv['buy'][1]:.2f}</span> 稳健买点<br>止损 {lv['stop']:.2f} · 硬止损 {lv['hard_stop']:.2f}</p></div>
    <div class="item"><h3>目标位</h3><p><span class="big">{tgt_s}</span><br>MA5 {last['ma5']:.2f} · MA20 {last['ma20']:.2f} · MA60 {last['ma60']:.2f}</p></div>
    <div class="item"><h3>动量状态</h3><p><span class="big">DIF {last['dif']:.2f}</span> / DEA {last['dea']:.2f}<br>RSI {last['rsi']:.0f} · KDJ K {last['k']:.0f} D {last['d']:.0f} J {last['j']:.0f} · ATR {last['atr']:.2f}</p></div>
  </div>"""
    return f"""
<div class="slide" id="slide{slide_idx}">
  <div class="stock-head">
    <div>
      <h2>{q['name']}（{a['code']}）{tag}
        {vtag}</h2>
      <div class="sub">数据截至 {a['w1']['date']} 收盘 · 前复权 · 近半年 {a['w0']['date']} ~ {a['w1']['date']}</div>
    </div>
    <div class="price-box">
      <div class="price">{q_px:.2f}<span class="unit"> 元</span></div>
      <div class="chg {chg_cls}">{chg_txt}</div>
    </div>
  </div>
{v2_html}

  <div class="grid">
    <div class="card"><div class="num {'up' if a['half_ret'] >= 0 else 'down'}">{a['half_ret']:+.2f}%</div><div class="lbl">近半年涨幅</div></div>
    <div class="card"><div class="num">{a['hi']:.2f}</div><div class="lbl">区间最高</div></div>
    <div class="card"><div class="num">{a['lo']:.2f}</div><div class="lbl">区间最低</div></div>
    <div class="card"><div class="num down">-{a['mdd']}%</div><div class="lbl">最大回撤</div></div>
    <div class="card"><div class="num">{a['ann_vol']}%</div><div class="lbl">年化波动率</div></div>
    <div class="card"><div class="num">{q['turnover_pct']:.2f}%</div><div class="lbl">当日换手率</div></div>
    <div class="card"><div class="num">{q['vol_ratio']:.2f}</div><div class="lbl">当日量比</div></div>
    <div class="card"><div class="num">{a['avg_turn']:.2f}%</div><div class="lbl">半年日均换手</div></div>
  </div>
  <div class="grid">
    <div class="card"><div class="num">{q['amount_wan'] / 1e4:.2f} 亿</div><div class="lbl">当日成交额</div></div>
    <div class="card"><div class="num">{q['pe']:.1f}</div><div class="lbl">市盈率(TTM)</div></div>
    <div class="card"><div class="num">{q['pb']:.2f}</div><div class="lbl">市净率</div></div>
    <div class="card"><div class="num">{q['total_mv_yi']:.1f} 亿</div><div class="lbl">总市值</div></div>
    <div class="card"><div class="num">{a['avg_vol'] / 1e4:.0f} 万股</div><div class="lbl">日均成交量</div></div>
    <div class="card"><div class="num">{med_vol / 1e4:.0f} 万股</div><div class="lbl">成交量中位数</div></div>
    <div class="card"><div class="num">{a['max_vol'] / 1e4:.0f} 万股</div><div class="lbl">半年最大单日量</div></div>
    <div class="card"><div class="num">{vol_up}/{len(a['win'])}</div><div class="lbl">上涨天数/总天数</div></div>
  </div>

  <h3>行情走势（K线 / 均线 / 成交量）</h3>
  <div class="chart" id="chk_{chart_idx}_kline"></div>

  <h3>技术指标（MACD / RSI / KDJ）</h3>
  <div class="chart" id="chk_{chart_idx}_ind"></div>

{zone_html}
</div>"""
