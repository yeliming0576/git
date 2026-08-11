# -*- coding: utf-8 -*-
"""
A股个股量化分析系统 v2（按《A股个股量化分析规范_v2.md》实现）
============================================================
- 数据：腾讯 hfq 8年（回测）/ qfq（展示），沪深300 基准
- 特征：F1 趋势斜率、F2 相对强度、F3 动量分位、F4 波动状态、F5 量价配合
- 决策：L1 可交易性 → L2 趋势门槛 → L3 择时评分，输出状态标签
- 交易：ATR 初始/吊灯止损、次日开盘执行、涨跌停不可成交建模、固定分数仓位
- 回测：Walk-forward、参数网格、随机对照、基准对比；N<30 标注样本不足

数据源限制说明（已在报告中声明）：
1) 截面分位需要全市场数据，免费接口不可得，暂以“该股自身8年时序分位”替代；
2) 免费接口无退市股池，存在幸存者偏差；
3) 网易/Tushare 暂不可用，用腾讯 hfq 分页获取。
"""
import datetime
import json
import math
import random
import statistics

import history

COST_PER_SIDE = 0.0015       # 往返 0.30%（佣金+印花税+过户费+滑点，组合规范取值）
RF_ANNUAL = 0.02             # 无风险利率 2%
EQUITY_DEFAULT = 100000.0    # 报告用总权益（元）
RISK_PER_TRADE = 0.01        # 单笔风险预算 1%
MAX_POSITION_PCT = 0.20      # 单票上限 20%
PERM_N = 200                 # 随机对照抽样次数（规范 1000，控制运行时间）
# 规范内部矛盾修正：目标=entry+3ATR、止损=entry-2ATR 时盈亏比恒为1.5<2，
# 故目标位按 4×ATR 实现（=2:1），保证“盈亏比≥2”过滤可执行
TARGET_ATR_MULT = 4.0
CACHE_VERSION = 3            # v2 结果缓存版本（规则/口径变更时+1，自动重算）


# ---------------- 基础指标 ----------------
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


def atr_wilder(rows, n=14):
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


def lin_slope(y, window=20):
    n = len(y)
    x = list(range(n))
    xm = sum(x) / n
    ym = sum(y) / n
    den = sum((x[i] - xm) ** 2 for i in range(n)) or 1.0
    return sum((x[i] - xm) * (y[i] - ym) for i in range(n)) / den


def ts_percentile_rank(series, idx, lookback=None):
    """series[idx] 在自身过去序列中的百分位（0~100），用于替代截面分位"""
    start = max(0, idx - (lookback or idx))
    window = series[start:idx + 1]
    window = [v for v in window if v is not None]
    if len(window) < 10:
        return 50.0
    v = series[idx]
    below = sum(1 for x in window if x < v)
    return below / (len(window) - 1) * 100 if len(window) > 1 else 50.0


def obv_series(rows):
    out = [0.0]
    for i in range(1, len(rows)):
        chg = rows[i]["close"] - rows[i - 1]["close"]
        out.append(out[-1] + (rows[i]["volume"] if chg > 0 else (-rows[i]["volume"] if chg < 0 else 0)))
    return out


# ---------------- 特征 ----------------
def build_features(rows, index_map, ma_period=60):
    n = len(rows)
    closes = [r["close"] for r in rows]
    volumes = [r["volume"] for r in rows]
    ma = sma(closes, ma_period)
    ma20 = sma(closes, 20)
    atr20 = atr_wilder(rows, 20)
    atr22 = atr_wilder(rows, 22)
    atr14 = atr_wilder(rows, 14)
    obv = obv_series(rows)
    rs = []
    prev_idx = None
    for r in rows:
        idx = index_map.get(r["date"], prev_idx)
        if idx is not None:
            prev_idx = idx
        rs.append(closes[len(rs)] / idx if idx else None)
    f = [{} for _ in range(n)]
    for i in range(n):
        d = f[i]
        d["ma"] = ma[i]
        d["ma20"] = ma20[i]
        d["atr20"] = atr20[i]
        d["atr22"] = atr22[i]
        d["atr14"] = atr14[i]
        d["close"] = closes[i]
        if i >= 19 and ma[i] and ma[i - 19]:
            seg = [ma[j] for j in range(i - 19, i + 1) if ma[j] is not None]
            if len(seg) == 20:
                d["slope60"] = lin_slope(seg) / ma[i] * 100
        if i >= 19 and atr20[i] and ma[i]:
            d["dist"] = (closes[i] - ma[i]) / atr20[i]
        if i >= 60 and rs[i] and rs[i - 60]:
            d["rs20"] = rs[i] / rs[i - 20] - 1 if i >= 20 and rs[i - 20] else None
            d["rs60"] = rs[i] / rs[i - 60] - 1
        if i >= 120 and closes[i - 60] and closes[i - 120]:
            d["mom60"] = closes[i] / closes[i - 60] - 1
            d["mom120"] = closes[i] / closes[i - 120] - 1
        if atr14[i]:
            d["atrp"] = atr14[i] / closes[i]
        if i >= 250 and d.get("atrp"):
            d["vol_pct"] = ts_percentile_rank(
                [f[j].get("atrp") for j in range(max(0, i - 249), i + 1)], i - max(0, i - 249))
        if i >= 60 and d.get("mom60") is not None and d.get("mom120") is not None:
            d["mom_score"] = 0.6 * ts_percentile_rank(
                [f[j].get("mom60") for j in range(60, i + 1)], i - 60) + \
                0.4 * ts_percentile_rank(
                [f[j].get("mom120") for j in range(120, i + 1)], i - 120)
        if i >= 19:
            v20 = volumes[i - 19:i + 1]
            if sum(v20) > 0:
                d["obv_slope"] = lin_slope(obv[i - 19:i + 1]) / (sum(v20) / 20)
            up = sum(volumes[j] for j in range(i - 19, i + 1)
                     if j > 0 and closes[j] > closes[j - 1])
            dn = sum(volumes[j] for j in range(i - 19, i + 1)
                     if j > 0 and closes[j] < closes[j - 1])
            d["vol_ratio"] = up / dn if dn > 0 else (10.0 if up > 0 else 1.0)
        if i >= 19:
            d["avg_amount"] = sum(volumes[j] * closes[j] * 100 for j in range(i - 19, i + 1)) / 20
        if i >= 20 and d.get("rs20") is not None and d.get("rs60") is not None:
            d["rs_rank"] = ts_percentile_rank(
                [f[j].get("rs60") for j in range(60, i + 1)], i - 60)
        if i >= 120:
            hi = max(closes[max(0, i - 59):i + 1])
            d["new_high"] = closes[i] >= hi
            d["divergence"] = bool(d.get("new_high") and d.get("obv_slope", 0) < 0)
    return f


# ---------------- 过滤与状态 ----------------
def status_of(f, i, name, ma_period=60, rs_threshold=70, abs_i=None, cs=None):
    d = f[i]
    abs_i = abs_i if abs_i is not None else i
    # L1 可交易性
    l1 = bool(d.get("avg_amount") and d["avg_amount"] >= 5e7 and d["close"] > 3
              and abs_i >= 250 and not name.startswith(("ST", "*ST", "退")))
    l1_reason = [] if l1 else [
        "成交额不足5000万" if not (d.get("avg_amount") or 0) >= 5e7 else None,
        "价格≤3元" if d["close"] <= 3 else None,
        "上市不足250日" if abs_i < 250 else None,
        "ST/退市风险" if name.startswith(("ST", "*ST", "退")) else None,
    ]
    l1_reason = [x for x in l1_reason if x]
    # L2 趋势门槛（相对强度优先用全市场截面 60 日动量百分位，缺失时回退自身时序分位）
    if cs and cs.get("mom60_pct") is not None:
        rs_ok, cs_used = cs["mom60_pct"] >= rs_threshold, True
    elif d.get("rs_rank") is not None:
        rs_ok, cs_used = d["rs_rank"] >= rs_threshold, False
    else:
        rs_ok, cs_used = False, False
    l2 = bool(l1 and d.get("slope60") is not None and d["slope60"] > 0.05
              and d["close"] > (d["ma"] or 0)
              and d.get("rs20") is not None and d["rs20"] > 0
              and rs_ok
              and not d.get("divergence"))
    # L3 择时评分（0~100）
    l3 = 0.0
    if d.get("mom_score") is not None:
        l3 += d["mom_score"] / 100 * 30
    if d.get("slope60") is not None:
        l3 += min(d["slope60"] / 0.2, 1.0) * 25
    if d.get("vol_pct") is not None:
        l3 += (100 - d["vol_pct"]) / 100 * 20
    if d.get("vol_ratio") is not None:
        l3 += min(d["vol_ratio"] / 2, 1.0) * 15
    if d.get("ma20") and d.get("atr20"):
        l3 += max(0.0, 1 - abs(d["close"] - d["ma20"]) / (2 * d["atr20"])) * 10
    if cs and cs.get("chg_pct") is not None:
        l3 += (100 - cs["chg_pct"]) / 100 * 10      # 当日涨幅截面分位越高，追高惩罚越大
    l3 = max(0.0, min(100.0, l3))
    # 风险警示
    risk = bool(d.get("divergence") or (d.get("vol_pct") or 0) > 90
                or (cs and (cs.get("chg_pct") or 0) > 90))
    if not risk and d.get("ma20"):
        cnt = 0
        for j in range(i, max(0, i - 3), -1):
            if f[j].get("ma20") and f[j]["close"] < f[j]["ma20"]:
                cnt += 1
        risk = cnt >= 3
    if risk:
        status = "风险警示"
    elif l2 and l3 >= 65:
        status = "强势可入"
    elif l2 and 45 <= l3 < 65:
        status = "强势观望"
    else:
        status = "弱势"
    return {"l1": l1, "l1_reason": l1_reason, "l2": l2, "l3": round(l3, 1),
            "status": status, "risk": risk, "cs_used": cs_used}


# ---------------- 回测 ----------------
def backtest(rows, f, name, atr_stop_mult=2.0, ma_period=60, rs_threshold=70,
             start_i=250, offset=0):
    """信号次日开盘执行；一字涨跌停建模；初始2×ATR + 吊灯3×ATR22；成本双边"""
    trades = []
    cash, shares, entry_px, stop = 1.0, 0.0, 0.0, 0.0
    entry_i = None
    exit_pending = False
    equity = []
    for i in range(len(rows)):
        r = rows[i]
        if shares > 0:
            # 吊灯止损（只上移）
            if i >= 21 and f[i].get("atr22"):
                hi22 = max(rows[j]["high"] for j in range(i - 21, i + 1))
                chand = hi22 - 3.0 * f[i]["atr22"]
                stop = max(stop, chand)
            # 出场触发（收盘判定）
            st = status_of(f, i, name, ma_period, rs_threshold, abs_i=i + offset)
            d = f[i]
            ma_break = (d.get("ma") is not None and d["close"] < d["ma"] and
                        i >= 1 and f[i - 1].get("ma") is not None and
                        rows[i - 1]["close"] < f[i - 1]["ma"])
            rs_weak = d.get("rs20") is not None and d["rs20"] < -0.05
            if exit_pending or r["close"] <= stop or ma_break or rs_weak:
                exit_pending = True
        if exit_pending and shares > 0:
            k = i + 1
            while k < len(rows) and rows[k]["limit_down"]:
                k += 1
            if k < len(rows):
                px = rows[k]["open"] * (1 - COST_PER_SIDE)
                trades.append({
                    "entry_date": rows[entry_i]["date"], "exit_date": rows[k]["date"],
                    "entry": round(entry_px, 2), "exit": round(px, 2),
                    "ret": round((px - entry_px) / entry_px * 100, 2),
                    "days": k - entry_i,
                })
                cash = shares * px
                shares, stop, entry_px, entry_i, exit_pending = 0.0, 0.0, 0.0, None, False
        if shares == 0 and not exit_pending and i >= start_i and i + 1 < len(rows):
            st = status_of(f, i, name, ma_period, rs_threshold, abs_i=i + offset)
            if st["status"] == "强势可入":
                j = i + 1
                if rows[j]["limit_up"]:
                    gap = rows[j]["open"] / rows[j - 1]["close"] - 1 if j > 0 else 0
                    if gap >= rows[j]["limit_pct"] - 0.005:
                        pass  # 一字涨停无法买入，放弃
                    else:
                        entry_open = rows[j]["open"]
                        atr = f[i]["atr20"] or 0
                        if atr > 0:
                            entry_proxy = entry_open * (1 + COST_PER_SIDE)
                            stop0 = entry_proxy - atr_stop_mult * atr
                            target = entry_proxy + TARGET_ATR_MULT * atr
                            if stop0 > 0 and (target - entry_proxy) / (entry_proxy - stop0) >= 2.0:
                                entry_px = entry_proxy
                                stop = stop0
                                shares = cash / entry_px
                                cash = 0.0
                                entry_i = j
                else:
                    entry_open = rows[j]["open"]
                    atr = f[i]["atr20"] or 0
                    if atr > 0:
                        entry_proxy = entry_open * (1 + COST_PER_SIDE)
                        stop0 = entry_proxy - atr_stop_mult * atr
                        target = entry_proxy + TARGET_ATR_MULT * atr
                        if stop0 > 0 and (target - entry_proxy) / (entry_proxy - stop0) >= 2.0:
                            entry_px = entry_proxy
                            stop = stop0
                            shares = cash / entry_px
                            cash = 0.0
                            entry_i = j
        equity.append(cash + shares * rows[i]["close"])
    if shares > 0:
        trades.append({"entry_date": rows[entry_i]["date"], "exit_date": "持仓中",
                       "entry": round(entry_px, 2), "exit": round(rows[-1]["close"], 2),
                       "ret": round((rows[-1]["close"] - entry_px) / entry_px * 100, 2),
                       "days": len(rows) - 1 - entry_i, "open": True})
    return trades, equity


def metrics(trades, equity, index_close_first, index_close_last):
    n = len([t for t in trades if not t.get("open")])
    wins = [t for t in trades if t["ret"] > 0 and not t.get("open")]
    losses = [t for t in trades if t["ret"] <= 0 and not t.get("open")]
    win_rate = len(wins) / n * 100 if n else None
    avg_win = statistics.mean([t["ret"] for t in wins]) if wins else 0.0
    avg_loss = statistics.mean([t["ret"] for t in losses]) if losses else 0.0
    gross_w = sum(t["ret"] for t in wins)
    gross_l = abs(sum(t["ret"] for t in losses))
    pf = gross_w / gross_l if gross_l > 0 else (float("inf") if wins else None)
    expectancy = (win_rate / 100 * avg_win - (1 - win_rate / 100) * avg_loss) if win_rate is not None else None
    days = len(equity)
    if days > 1 and equity[-1] > 0:
        cagr = (equity[-1] / equity[0]) ** (252 / days) - 1
    else:
        cagr = 0.0
    peak, mdd = 0.0, 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    daily = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]
    sd = statistics.pstdev(daily) if len(daily) > 2 else 0.0
    sharpe = ((statistics.mean(daily) - RF_ANNUAL / 252) / sd * math.sqrt(252)) if sd > 0 else 0.0
    calmar = cagr / mdd if mdd > 0 else 0.0
    bench_cagr = (index_close_last / index_close_first) ** (252 / days) - 1 if index_close_first > 0 and days > 1 else 0.0
    ci = 1.96 * math.sqrt((win_rate / 100) * (1 - win_rate / 100) / n) * 100 if n >= 30 and win_rate is not None else None
    return {
        "n": n, "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "ci": round(ci, 1) if ci is not None else None,
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "pf": pf, "expectancy": round(expectancy, 2) if expectancy is not None else None,
        "cagr": round(cagr * 100, 2), "mdd": round(mdd * 100, 2),
        "sharpe": round(sharpe, 2), "calmar": round(calmar, 2),
        "bench_cagr": round(bench_cagr * 100, 2),
        "excess": round((cagr - bench_cagr) * 100, 2),
        "trades": trades,
    }


def permutation_p(trades, equity):
    """随机对照：保持交易次数与持仓天数分布，随机重排买入日期，返回 p 值"""
    real = (equity[-1] / equity[0] - 1) if equity else 0
    closed = [t for t in trades if not t.get("open")]
    if len(closed) < 5:
        return None
    days = [t["days"] for t in closed]
    n_better = 0
    for _ in range(PERM_N):
        random.shuffle(days)
        cur = 1.0
        idx = 0
        for d in days:
            if idx + d >= len(equity):
                break
            cur *= equity[idx + d] / equity[idx]
            idx += d + 1
        if cur - 1 >= real:
            n_better += 1
    return n_better / PERM_N


def walk_forward(rows, f, name, years=8):
    """训练3年/测试1年前滚：拼接样本外区间的交易"""
    dates = [r["date"] for r in rows]
    start_year = int(dates[0][:4]) + 3
    end_year = int(dates[-1][:4])
    trades_all, eq_all = [], [1.0]
    for test_year in range(start_year, end_year + 1):
        lo = f"{test_year}-01-01"
        hi = f"{test_year}-12-31"
        i0 = next((i for i, d in enumerate(dates) if d >= lo), None)
        i1 = next((i for i, d in enumerate(dates) if d > hi), len(rows))
        if i0 is None or i0 >= i1 or i0 < 250:
            continue
        sub_rows = rows[i0:i1]
        sub_f = f[i0:i1]
        tr, eq = backtest(sub_rows, sub_f, name, start_i=0, offset=i0)
        trades_all += tr
        eq_all.extend([eq_all[-1] * (v / eq[0]) for v in eq[1:]])
    return trades_all, eq_all


def grid_test(rows, f, name):
    """参数敏感性网格：ATR倍数×MA周期×RS阈值"""
    out = []
    index_map = _index_map(rows)
    feat = {ma_p: build_features(rows, index_map, ma_period=ma_p) for ma_p in (40, 60, 80)}
    for atr_m in (1.5, 2.0, 2.5):
        for ma_p in (40, 60, 80):
            for rs_t in (60, 70, 80):
                tr, eq = backtest(rows, feat[ma_p], name, atr_stop_mult=atr_m,
                                  ma_period=ma_p, rs_threshold=rs_t)
                m = metrics(tr, eq, eq[0], eq[-1])
                out.append({"atr": atr_m, "ma": ma_p, "rs": rs_t,
                            "n": m["n"], "cagr": m["cagr"], "mdd": m["mdd"]})
    return out


def _index_map(rows):
    idx = history.get_index("sh000300")
    m = {}
    prev = None
    for d in idx:
        prev = d["close"]
        m[d["date"]] = prev
    for r in rows:
        m.setdefault(r["date"], prev)
    return m


# ---------------- 主入口与报告 ----------------
def analyze_v2(code, name="", equity=EQUITY_DEFAULT):
    cached = _load_cache(code)
    if cached:
        cached["from_cache"] = True
        return cached
    rows = history.get_history(code, "hfq")
    rows_q = history.get_history(code, "qfq")
    if len(rows) < 260:
        return None
    rows = history.mark_limits(rows, name)
    index_map = _index_map(rows)
    f = build_features(rows, index_map)
    last_i = len(rows) - 1
    cs = None
    try:
        import market_snapshot
        cs = market_snapshot.cross_sectional(code)
    except Exception:
        cs = None
    st = status_of(f, last_i, name, cs=cs)
    d = f[last_i]
    # 展示/交易计划用前复权价格（与现价可比），回测特征用后复权（可复现）
    q_closes = [r["close"] for r in rows_q]
    q_ma20 = sma(q_closes, 20)[-1]
    q_atr20 = atr_wilder(rows_q, 20)[-1] or 0
    q_last = rows_q[-1]
    display_last = {
        "close": q_last["close"],
        "atr20": q_atr20,
        "ma20": q_ma20 or q_last["close"],
        "avg_amount": sum(rows_q[j]["volume"] * rows_q[j]["close"] * 100
                          for j in range(max(0, len(rows_q) - 20), len(rows_q))) / 20,
    }
    # 交易计划（仅强势可入）
    plan = None
    if st["status"] == "强势可入" and display_last["atr20"] > 0:
        entry = display_last["ma20"]
        atr = display_last["atr20"]
        stop = entry - 2.0 * atr
        target = entry + TARGET_ATR_MULT * atr
        ratio = (target - entry) / (entry - stop) if entry > stop else 0
        shares = 0
        if ratio >= 2.0:
            shares = min(equity * RISK_PER_TRADE / (entry - stop),
                         equity * MAX_POSITION_PCT / entry)
            shares = int(shares // 100) * 100
        plan = {"entry_lo": round(entry, 2), "entry_hi": round(entry + 0.5 * atr, 2),
                "stop": round(stop, 2), "target": round(target, 2),
                "ratio": round(ratio, 2), "shares": shares,
                "stop_pct": round((entry - stop) / entry * 100, 2),
                "target_pct": round((target - entry) / entry * 100, 2)}
    # 回测
    trades, eq = backtest(rows, f, name)
    m = metrics(trades, eq, index_map.get(rows[0]["date"]) or eq[0],
                index_map.get(rows[-1]["date"]) or eq[-1])
    m["p"] = permutation_p(trades, eq)
    wf_trades, wf_eq = walk_forward(rows, f, name)
    wf = metrics(wf_trades, wf_eq, index_map.get(rows[0]["date"]) or 1.0,
                 index_map.get(rows[-1]["date"]) or 1.0)
    grid = grid_test(rows, f, name)
    result = {
        "code": code, "name": name, "rows_q": rows_q, "rows_hfq": rows,
        "date": rows[-1]["date"], "status": st["status"], "l1": st["l1"],
        "l1_reason": st["l1_reason"], "l2": st["l2"], "l3": st["l3"],
        "f": f, "last": display_last, "plan": plan, "bt": m, "wf": wf, "grid": grid,
        "risk": st["risk"],
        "slope60": round(d.get("slope60") or 0, 3), "rs20": round(d.get("rs20") or 0, 3),
        "rs_rank": round(d.get("rs_rank") or 0, 1),
        "vol_pct": round(d.get("vol_pct") or 0, 1),
        "vol_ratio": round(d.get("vol_ratio") or 0, 2),
        "mom_score": round(d.get("mom_score") or 0, 1),
        "cs_used": bool(st.get("cs_used")),
        "cs_mom60_pct": round(cs.get("mom60_pct") or 0, 1) if cs else None,
        "cs_chg_pct": round(cs.get("chg_pct") or 0, 1) if cs else None,
        "equity": equity,
    }
    result["from_cache"] = False
    _save_cache(code, result)
    return result


def _load_cache(code):
    """当日结果缓存（v2 计算较重，同一交易日只算一次）"""
    try:
        conn = history._connect()
        try:
            row = conn.execute(
                "SELECT payload FROM v2_cache WHERE code=? AND date=?",
                (code, datetime.date.today().strftime("%Y-%m-%d"))).fetchone()
        finally:
            conn.close()
        if row:
            data = json.loads(row[0])
            if data.get("_v") != CACHE_VERSION:
                return None
            data.pop("rows_q", None)
            data.pop("rows_hfq", None)
            return data
    except Exception:
        pass
    return None


def _save_cache(code, result):
    try:
        conn = history._connect()
        try:
            payload = {k: v for k, v in result.items()
                       if k not in ("rows_q", "rows_hfq")}
            payload["_v"] = CACHE_VERSION
            conn.execute(
                "INSERT OR REPLACE INTO v2_cache(code, date, payload) VALUES(?,?,?)",
                (code, datetime.date.today().strftime("%Y-%m-%d"),
                 json.dumps(payload, ensure_ascii=False, default=str)))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _ci_text(m):
    if m["n"] < 30:
        return "样本不足，不报告"
    if m["win_rate"] is None:
        return "无交易"
    return f"{m['win_rate']}% ± {m['ci']}%"


def plain_summary(a):
    """新手能看懂的一句话总结"""
    st = a["status"]
    if st == "强势可入":
        p = a["plan"]
        return (f"这只股票的趋势、动量和量能都通过了筛选，也不过热，是当前少数符合买入条件的。"
                f"建议只在 {p['entry_lo']}~{p['entry_hi']} 元区间分批买入，"
                f"跌破 {p['stop']} 元就止损认错，目标看 {p['target']} 元（约 +{p['target_pct']}%）。"
                f"盈亏比 {p['ratio']}：赚的空间大约是亏的空间的 {p['ratio']:.0f} 倍。")
    if st == "强势观望":
        return (f"大方向已经转好，但择时评分只有 {a['l3']:.0f}/100，还差一点到 65 分入场线。"
                f"现在不用急着买，等它回踩到 MA20 附近、量能重新放大再考虑。")
    if st == "风险警示":
        return (f"这只股票触发了高风险信号（量价背离 / 波动率处于历史高位 / 连续跌破 MA20 中的一种或几种）。"
                f"通俗说就是“看着热闹但风险很大”，现在不适合买入；如果已经持有，按止损纪律处理。")
    return (f"这只股票没通过趋势硬门槛，通常是因为跑不赢沪深300、中期趋势向下、或量价配合不合格。"
            f"按规则不参与；可以放进观察列表，等它重新走强（站上均线且跑赢指数）再说。")


def render_block(a, v1_levels=None, v1=None):
    st = a["status"]
    color = {"强势可入": "#dc2626", "强势观望": "#ea580c", "弱势": "#059669",
             "风险警示": "#7c3aed"}.get(st, "#6b7280")
    l1 = "通过" if a["l1"] else "失败（" + "、".join(a["l1_reason"] or ["条件不满足"]) + "）"
    l2 = "通过" if a["l2"] else "未通过"
    summary = plain_summary(a)
    support_ref = ""
    if v1_levels:
        support_ref = (f"支撑位参考：S1（短期支撑·近5日最低价）{v1_levels['s1']:.2f} 元 / "
                       f"S2（中期支撑·近20日最低价）{v1_levels['s2']:.2f} 元 / "
                       f"S3（长期支撑·近60日最低价）{v1_levels['s3']:.2f} 元。"
                       f"含义：跌到这些位置附近，过去一段时间曾有资金承接；只是参考，跌破即失效，不代表一定反弹。")

    # 交易建议：买卖点 / 止损 / 目标 / 未来预测
    if a["plan"]:
        p = a["plan"]
        trade_html = f"""<div class="zone">
  <div class="item"><h3>买卖点建议</h3><p><span class="big">{p['entry_lo']} ~ {p['entry_hi']}</span><br>买入区间（MA20 附近），分 2~3 批</p></div>
  <div class="item"><h3>止损建议</h3><p><span class="big">{p['stop']}</span><br>下方 {p['stop_pct']}%（入场-2ATR），跌破无条件离场</p></div>
  <div class="item"><h3>目标 / 未来预测</h3><p><span class="big">{p['target']}</span><br>上方 {p['target_pct']}%，到目标先减半仓，不追高</p></div>
  <div class="item"><h3>仓位建议</h3><p><span class="big">{p['shares']} 股</span><br>按 1% 风险预算（总权益 {a['equity']:.0f} 元）· 盈亏比 {p['ratio']}:1</p></div>
</div>"""
        if support_ref:
            trade_html += f'<div class="sub" style="margin-top:8px;">{support_ref}</div>'
    else:
        trade_html = f"""<div class="panel"><b>当前没有买卖点 / 止损 / 目标建议</b>
  <div class="sub" style="line-height:1.9;margin-top:5px;">
  按规则，只有「强势可入」才给出交易计划。现在是「{st}」，所以不给买卖建议，这不是系统故障，是纪律。<br>
  观察要点：{support_ref or ''} 什么时候可以再看？——等它同时满足：站上并守住 MA20、MACD 多头、跑赢沪深300，且没有过热信号。
  </div>
</div>"""

    # 任性买入参考（系统不建议但用户坚持要买时，给纪律数字）
    stubborn_html = ""
    if st != "强势可入" and v1:
        stubborn_html = _stubborn_block(a, v1)

    # 技术细节（默认折叠）
    wf_txt = f"{a['wf']['n']} 笔 | 胜率 {_ci_text(a['wf'])} | CAGR {a['wf']['cagr']}% | MDD {a['wf']['mdd']}%"
    p_txt = (f"p = {a['bt']['p']:.3f}（{'显著' if a['bt']['p'] < 0.05 else '不显著'}）"
             if a["bt"]["p"] is not None else "样本过少，未检验")
    grid_rows = "".join(
        f"<tr><td>ATR×{g['atr']}</td><td>MA{g['ma']}</td><td>RS≥{g['rs']}</td>"
        f"<td>{g['n']}</td><td>{g['cagr']}%</td><td>-{g['mdd']}%</td></tr>"
        for g in a["grid"])
    pf_txt = "∞" if a["bt"]["pf"] == float("inf") else (
        "无交易" if a["bt"]["pf"] is None else f"{a['bt']['pf']:.2f}")
    details = f"""<details><summary>技术细节：过滤链路与回测（点开查看）</summary>
<div class="panel">
  <div class="sub" style="line-height:1.9;">
  数据截至 {a['date']} · 回测用后复权8年（hfq），展示用前复权 · 基准：沪深300<br>
  L1 可交易性：{l1} ｜ L2 趋势门槛：{l2}（slope60={a['slope60']}，RS_20={a['rs20']}，RS_rank={a['rs_rank']}，量价={a['vol_ratio']}）｜ L3 择时评分：{a['l3']}/100<br><br>
  <b>回测（样本外 Walk-forward）</b>：{wf_txt} ｜ 随机对照 {p_txt} ｜ 超额收益 vs 沪深300：{a['wf']['excess']}%<br>
  全区间：{a['bt']['n']} 笔 | 胜率 {_ci_text(a['bt'])} | CAGR {a['bt']['cagr']}% | MDD {a['bt']['mdd']}% |
  Sharpe {a['bt']['sharpe']} | Calmar {a['bt']['calmar']} | 盈亏比 {pf_txt}<br><br>
  <b>已知局限（必须声明）</b>：① A股T+1与涨跌停限制执行，回测已建模但仍偏乐观；② 技术面策略对政策冲击、突发公告无免疫力；
  ③ 趋势跟踪在震荡市系统性亏损；④ 公开逻辑随使用者增加而衰减；⑤ 本系统不含基本面；
  ⑥ 免费接口无SLA；截面分位暂以个股自身8年时序分位替代（全市场数据需Tushare Pro）。
  </div>
</div>
</details>
<details><summary>参数敏感性网格（点开查看）</summary>
<div class="panel">
  <table><tr><th>ATR倍数</th><th>MA周期</th><th>RS阈值</th><th>交易次数</th><th>CAGR</th><th>MDD</th></tr>{grid_rows}</table>
  <div class="sub">这是策略的“体检报告”，不是买卖信号：若只有个别参数组合有效、邻近组合明显亏损，说明存在过拟合，参数不可信。</div>
</div>
</details>"""

    return f"""<div class="panel" style="border-left:4px solid {color};">
  <b>v2 结论：<span style="color:{color};">{st}</span></b>
  <div class="sub" style="line-height:1.9;margin-top:6px;">{summary}</div>
</div>
{trade_html}
{stubborn_html}
{details}"""


def _stubborn_block(a, v1):
    """明知弱势仍要买：给买入点/止损点/卖出点/预期点 + 小仓位纪律"""
    q = v1["quote"]
    px = q["price"]
    lv = v1["levels"]
    last = v1["last"]
    ma20 = last.get("ma20") or 0
    ma60 = last.get("ma60") or 0
    s1, s2, s3 = lv["s1"], lv["s2"], lv["s3"]
    atr = last.get("atr") or 0
    r1 = lv.get("r1") or (ma60 or px * 1.05)

    buy1 = px * 0.995                       # 第一批：现价附近
    buy2 = s2 * 1.01                        # 第二批：回踩 S2 中期支撑
    stop1 = (px - 2 * atr) if atr > 0 else s3 * 0.97
    stop1 = max(stop1, 0.01)
    hard = s3 * 0.97                        # 最终防线：跌破 S3
    t1 = ma20 if ma20 > px else max(px * 1.03, s1 * 1.05)
    t2 = max(ma60, r1) if ma60 > px else max(px * 1.06, r1)

    def pct(x):
        return (x / px - 1) * 100 if px else 0

    return f"""<div class="panel" style="border:1px dashed #f59e0b;">
  <b>💡 任性买入参考（系统判定「{a['status']}」不建议买入，但如果你坚持要买）</b>
  <div class="sub" style="line-height:1.9;margin-top:6px;">
  以下数字仅作为“明知风险仍参与”的纪律参考，<b>不是推荐</b>，请务必小仓位：<br>
  · <b>买入点</b>：第一批 现价附近 {buy1:.2f} 元；第二批 回踩 {buy2:.2f} 元（S2 中期支撑）附近再加，别一次买满；<br>
  · <b>止损点</b>：建议止损 {stop1:.2f} 元（约 {pct(stop1):+.1f}%，2×ATR 口径）；最终防线 {hard:.2f} 元（跌破 S3 无条件离场）；<br>
  · <b>卖出点 / 预期点</b>：第一目标 {t1:.2f} 元（约 {pct(t1):+.1f}%，MA20 附近），到点先减半仓；第二目标 {t2:.2f} 元（约 {pct(t2):+.1f}%，MA60/近期高点），到点清仓；<br>
  · <b>仓位纪律</b>：总资金 ≤5%，亏损不补仓，破止损不商量。
  </div>
</div>"""
