# -*- coding: utf-8 -*-
"""公共回测引擎（全站单一口径）
================================
把原先散在 v2.py（特征驱动）与 quant_engine.py（信号数组驱动）里的撮合、
交易成本与绩效统计统一到这里，保证单股页、组合页、紫苏叶报告口径一致。

统一口径：
  - 信号在 T 日收盘生成，T+1 开盘执行
  - 双边成本 0.15%/边（佣金+印花税+过户费+滑点）
  - 一字涨停无法买入、跌停无法卖出（顺延到下一个可成交日）
  - 止损用 2×ATR20 建仓、吊灯 3×ATR22 上移，目标 4×ATR（盈亏比 2:1）

对外接口：
    run(rows, features, status_fn, **params)   -> {"trades","equity","stats","meta"}
    run_signals(rows, signals, **params)       -> quant_engine 式信号数组撮合
    walk_forward(...) / grid(...) / permutation_p(...) / metrics(...)

兼容保留：v2.backtest()、quant_engine.backtest() 仍按原签名返回原结构，
内部改为调用本模块。
"""
import math
import random
import statistics

import log_utils

COST_PER_SIDE = 0.0015       # 往返 0.30%（佣金+印花税+过户费+滑点）
RF_ANNUAL = 0.02             # 无风险利率 2%
TARGET_ATR_MULT = 4.0        # 目标位 4×ATR，保证盈亏比 >= 2:1
PERM_N = 200                 # 随机对照抽样次数
MIN_SAMPLE = 30              # 样本不足门槛（不足则标注"样本不足"）

log = log_utils.get_logger("backtest")


# ---------------- 成本与可交易性 ----------------
def entry_price(px, cost=COST_PER_SIDE):
    """含成本的买入价"""
    return px * (1 + cost)


def exit_price(px, cost=COST_PER_SIDE):
    """含成本的卖出价"""
    return px * (1 - cost)


def limit_pct_of(row, default=0.1):
    v = row.get("limit_pct")
    return default if v in (None, 0) else v


def is_limit_up(rows, i):
    return bool(0 <= i < len(rows) and rows[i].get("limit_up"))


def is_limit_down(rows, i):
    return bool(0 <= i < len(rows) and rows[i].get("limit_down"))


def is_one_word_limit_up(rows, i):
    """一字涨停：当日涨停且开盘即封板（无法买入）"""
    if not is_limit_up(rows, i):
        return False
    prev = rows[i - 1]["close"] if i > 0 else rows[i]["open"]
    if not prev or prev <= 0:
        return False
    gap = rows[i]["open"] / prev - 1
    return gap >= limit_pct_of(rows[i]) - 0.005


# ---------------- 特征驱动的撮合（原 v2.backtest） ----------------
def run(rows, features, status_fn, *, atr_stop_mult=2.0, start_i=250,
        cost=COST_PER_SIDE, target_atr_mult=TARGET_ATR_MULT):
    """features 为特征表，status_fn(i) 返回 status_of(...) 的判定结果。
    返回 {"trades": [...], "equity": [...], "stats": {...}, "meta": {...}}"""
    trades = []
    cash, shares, entry_px, stop = 1.0, 0.0, 0.0, 0.0
    entry_i = None
    exit_pending = False
    equity = []
    for i in range(len(rows)):
        r = rows[i]
        if shares > 0:
            # 吊灯止损（只上移）
            if i >= 21 and features[i].get("atr22"):
                hi22 = max(rows[j]["high"] for j in range(i - 21, i + 1))
                chand = hi22 - 3.0 * features[i]["atr22"]
                stop = max(stop, chand)
            # 出场触发（收盘判定）
            status_fn(i)
            d = features[i]
            ma_break = (d.get("ma") is not None and d["close"] < d["ma"] and
                        i >= 1 and features[i - 1].get("ma") is not None and
                        rows[i - 1]["close"] < features[i - 1]["ma"])
            rs_weak = d.get("rs20") is not None and d["rs20"] < -0.05
            if exit_pending or r["close"] <= stop or ma_break or rs_weak:
                exit_pending = True
        if exit_pending and shares > 0:
            k = i + 1
            while k < len(rows) and is_limit_down(rows, k):
                k += 1
            if k < len(rows):
                px = exit_price(rows[k]["open"], cost)
                trades.append({
                    "entry_date": rows[entry_i]["date"], "exit_date": rows[k]["date"],
                    "entry": round(entry_px, 2), "exit": round(px, 2),
                    "ret": round((px - entry_px) / entry_px * 100, 2),
                    "days": k - entry_i,
                })
                cash = shares * px
                shares, stop, entry_px, entry_i, exit_pending = 0.0, 0.0, 0.0, None, False
        if shares == 0 and not exit_pending and i >= start_i and i + 1 < len(rows):
            st = status_fn(i)
            if st["status"] == "强势可入":
                j = i + 1
                if not is_one_word_limit_up(rows, j):
                    atr = features[i].get("atr20") or 0
                    if atr > 0:
                        entry_proxy = entry_price(rows[j]["open"], cost)
                        stop0 = entry_proxy - atr_stop_mult * atr
                        target = entry_proxy + target_atr_mult * atr
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
    stats = metrics(trades, equity, equity[0] if equity else 1.0,
                    equity[-1] if equity else 1.0)
    meta = {"cost_per_side": cost, "target_atr_mult": target_atr_mult,
            "atr_stop_mult": atr_stop_mult, "start_i": start_i,
            "bars": len(rows)}
    return {"trades": trades, "equity": equity, "stats": stats, "meta": meta}


# ---------------- 信号数组驱动的撮合（原 quant_engine.backtest） ----------------
def run_signals(rows, signals, *, cost=COST_PER_SIDE, block_untradable=True):
    """signals[i] 为 "B"/"S"/None；T 日信号 → T+1 开盘执行。
    返回 {"trades", "equity", "stats", "meta"}；rows 无涨跌停字段时行为与旧版一致。"""
    trades = []
    pos = 0.0
    entry_i = None
    cash, shares = 1.0, 0.0
    eq = []
    pending_exit = False
    for i in range(1, len(rows)):
        sig = signals[i - 1] if i - 1 < len(signals) else None
        if pos > 0 and (pending_exit or sig == "S"):
            k = i
            if block_untradable:
                while k < len(rows) and is_limit_down(rows, k):
                    k += 1
            if k < len(rows):
                px = exit_price(rows[k]["open"], cost)
                trades.append({
                    "entry_date": rows[entry_i]["date"],
                    "entry_price": round(pos, 2),
                    "exit_date": rows[k]["date"],
                    "exit_price": round(px, 2),
                    "ret": round((px - pos) / pos * 100, 2),
                })
                cash = shares * px
                shares = 0.0
                pos = 0.0
                entry_i = None
                pending_exit = False
            else:
                pending_exit = True
        elif sig == "B" and pos == 0:
            if not (block_untradable and is_one_word_limit_up(rows, i)):
                entry_i = i
                pos = entry_price(rows[i]["open"], cost)
                shares = cash / pos
                cash = 0.0
        eq.append(cash + shares * rows[i]["close"])
    if pos > 0:
        mark_px = exit_price(rows[-1]["close"], cost)
        trades.append({
            "entry_date": rows[entry_i]["date"],
            "entry_price": round(pos, 2),
            "exit_date": "持仓中",
            "exit_price": round(mark_px, 2),
            "ret": round((mark_px - pos) / pos * 100, 2),
            "open": True,
        })
    stats = simple_stats(trades, eq)
    return {"trades": trades, "equity": eq, "stats": stats,
            "meta": {"cost_per_side": cost, "bars": len(rows)}}


def simple_stats(trades, equity):
    """quant_engine 口径的简版统计（保持原字段与算法）"""
    wins = [t for t in trades if t["ret"] > 0]
    losses = [t for t in trades if t["ret"] <= 0]
    gross_win = sum(t["ret"] for t in wins)
    gross_loss = abs(sum(t["ret"] for t in losses))
    total_ret = math.prod(1 + t["ret"] / 100 for t in trades) - 1
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    max_e, mdd = 0.0, 0.0
    for v in equity:
        max_e = max(max_e, v)
        mdd = max(mdd, (max_e - v) / max_e) if max_e > 0 else mdd
    return {
        "trades": trades, "n": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0,
        "profit_factor": round(pf, 2) if pf != float("inf") else None,
        "total_ret": round(total_ret * 100, 2),
        "max_drawdown": round(mdd * 100, 2),
        "eq": equity,
        "insufficient_sample": len(trades) < MIN_SAMPLE,
    }


# ---------------- 绩效统计（原 v2.metrics） ----------------
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
    expectancy = (win_rate / 100 * avg_win - (1 - win_rate / 100) * avg_loss) \
        if win_rate is not None else None
    days = len(equity)
    if days > 1 and equity[-1] > 0 and equity[0] > 0:
        cagr = (equity[-1] / equity[0]) ** (252 / days) - 1
    else:
        cagr = 0.0
    peak, mdd = 0.0, 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak) if peak > 0 else mdd
    daily = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]
    sd = statistics.pstdev(daily) if len(daily) > 2 else 0.0
    sharpe = ((statistics.mean(daily) - RF_ANNUAL / 252) / sd * math.sqrt(252)) if sd > 0 else 0.0
    calmar = cagr / mdd if mdd > 0 else 0.0
    bench_cagr = (index_close_last / index_close_first) ** (252 / days) - 1 \
        if index_close_first and index_close_first > 0 and days > 1 else 0.0
    ci = 1.96 * math.sqrt((win_rate / 100) * (1 - win_rate / 100) / n) * 100 \
        if n >= MIN_SAMPLE and win_rate is not None else None
    return {
        "n": n, "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "ci": round(ci, 1) if ci is not None else None,
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "pf": pf, "expectancy": round(expectancy, 2) if expectancy is not None else None,
        "cagr": round(cagr * 100, 2), "mdd": round(mdd * 100, 2),
        "sharpe": round(sharpe, 2), "calmar": round(calmar, 2),
        "bench_cagr": round(bench_cagr * 100, 2),
        "excess": round((cagr - bench_cagr) * 100, 2),
        "insufficient_sample": n < MIN_SAMPLE,
        "trades": trades,
    }


def permutation_p(trades, equity, n_iter=PERM_N):
    """随机对照：保持交易次数与持仓天数分布，随机重排买入日期，返回 p 值"""
    real = (equity[-1] / equity[0] - 1) if equity else 0
    closed = [t for t in trades if not t.get("open")]
    if len(closed) < 5:
        return None
    days = [t["days"] for t in closed]
    n_better = 0
    for _ in range(n_iter):
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
    return n_better / n_iter


def walk_forward(rows, features, status_fn_factory, *, years=8,
                 cost=COST_PER_SIDE, **params):
    """训练3年/测试1年前滚：拼接样本外区间的交易。
    status_fn_factory(i0, sub_rows, sub_features) 需返回该切片的判定函数。"""
    dates = [r["date"] for r in rows]
    start_year = int(dates[0][:4]) + 3
    end_year = int(dates[-1][:4])
    trades_all, eq_all = [], [1.0]
    for test_year in range(start_year, end_year + 1):
        lo, hi = f"{test_year}-01-01", f"{test_year}-12-31"
        i0 = next((i for i, d in enumerate(dates) if d >= lo), None)
        i1 = next((i for i, d in enumerate(dates) if d > hi), len(rows))
        if i0 is None or i0 >= i1 or i0 < 250:
            continue
        sub_rows, sub_f = rows[i0:i1], features[i0:i1]
        status_fn = status_fn_factory(i0, sub_rows, sub_f)
        res = run(sub_rows, sub_f, status_fn, start_i=0, cost=cost, **params)
        trades_all += res["trades"]
        eq = res["equity"]
        if eq:
            eq_all.extend([eq_all[-1] * (v / eq[0]) for v in eq[1:]])
    return trades_all, eq_all


def grid(rows, features_by_ma, status_fn_factory, *,
         atr_mults=(1.5, 2.0, 2.5), rs_thresholds=(60, 70, 80),
         cost=COST_PER_SIDE):
    """参数敏感性网格：ATR 倍数 × MA 周期 × RS 阈值"""
    out = []
    for atr_m in atr_mults:
        for ma_p, feat in features_by_ma.items():
            for rs_t in rs_thresholds:
                status_fn = status_fn_factory(ma_p, atr_m, rs_t)
                res = run(rows, feat, status_fn, atr_stop_mult=atr_m, cost=cost)
                m = res["stats"]
                out.append({"atr": atr_m, "ma": ma_p, "rs": rs_t,
                            "n": m["n"], "cagr": m["cagr"], "mdd": m["mdd"]})
    return out
