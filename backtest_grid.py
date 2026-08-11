# -*- coding: utf-8 -*-
"""
参数敏感性 + 样本外验证（选股系统 v2 配套工具）
用法: python backtest_grid.py [股票代码]   （默认 600519）
输出: 各参数组合回测表 + 最优组合的样本内/样本外对照
"""
import sys

import quant_engine as Q

CODE = sys.argv[1] if len(sys.argv) > 1 else "600519"

# 参数网格（可自行增删）
MA_GRID = [(5, 20), (10, 20), (10, 30), (20, 60)]
VOL_GRID = [0.7, 0.9, 1.1, 1.3]
TRAIL_GRID = [0.08, 0.12, 0.16]


def run_backtest(rows, ma_s, ma_l, vol_ratio, trail):
    closes = [r["close"] for r in rows]
    ma_s_arr = Q.sma(closes, ma_s)
    ma_l_arr = Q.sma(closes, ma_l)
    ma10 = Q.sma(closes, 10)
    dif, dea, _ = Q.macd(closes)
    vol_ma5 = Q.sma([r["volume"] for r in rows], 5)
    sig = Q.trend_signals(rows, ma_s_arr, ma10, ma_l_arr, dif, dea, vol_ma5,
                          trail=trail, vol_ratio=vol_ratio)
    bt = Q.backtest(rows, sig)
    return bt


def main():
    print(f"正在抓取 {CODE} 日K线...")
    rows = Q.fetch_kline(CODE, datalen=300)
    print(f"K线: {rows[0]['date']} ~ {rows[-1]['date']} 共 {len(rows)} 根")
    print(f"交易成本: 单边 {Q.COST_RATE * 100:.2f}%")
    print("=" * 92)
    print(f"{'MA组合':<10}{'量比':<6}{'回撤止损':<8}{'交易次数':<8}{'胜率':<8}{'总收益':<10}{'最大回撤':<10}{'盈亏比'}")
    results = []
    for ma_s, ma_l in MA_GRID:
        for vol_ratio in VOL_GRID:
            for trail in TRAIL_GRID:
                bt = run_backtest(rows, ma_s, ma_l, vol_ratio, trail)
                pf = "∞" if bt["profit_factor"] is None else f"{bt['profit_factor']:.2f}"
                wr = f"{bt['win_rate']}%"
                tr = f"{bt['total_ret']}%"
                md = f"{bt['max_drawdown']}%"
                print(f"MA{ma_s}/{ma_l:<5}{vol_ratio:<6}{trail * 100:<8.0f}%"
                      f"{bt['n']:<8}{wr:<8}{tr:<10}-{md:<9}{pf}")
                results.append((bt["total_ret"], ma_s, ma_l, vol_ratio, trail, bt))
    print("=" * 92)

    best = max(results, key=lambda x: x[0])
    _, ma_s, ma_l, vol_ratio, trail, bt_best = best
    print(f"最优组合: MA{ma_s}/{ma_l} + 量比{vol_ratio} + 回撤{trail * 100:.0f}%"
          f" -> 总收益 {bt_best['total_ret']}%，胜率 {bt_best['win_rate']}%，回撤 -{bt_best['max_drawdown']}%")

    # 样本外验证：前 60% 为样本内，后 40% 为样本外
    split = int(len(rows) * 0.6)
    ins = rows[:split]
    oos = rows[split:]
    bt_in = run_backtest(ins, ma_s, ma_l, vol_ratio, trail)
    bt_oos = run_backtest(oos, ma_s, ma_l, vol_ratio, trail)
    print("-" * 92)
    print(f"样本外验证（最优组合）: 样本内 {ins[0]['date']}~{ins[-1]['date']}（{len(ins)}根）")
    print(f"  样本内: 次数{bt_in['n']} 胜率{bt_in['win_rate']}% 收益{bt_in['total_ret']}% 回撤-{bt_in['max_drawdown']}%")
    print(f"  样本外: {oos[0]['date']}~{oos[-1]['date']}（{len(oos)}根）")
    print(f"  样本外: 次数{bt_oos['n']} 胜率{bt_oos['win_rate']}% 收益{bt_oos['total_ret']}% 回撤-{bt_oos['max_drawdown']}%")
    print("说明: 样本外表现与样本内差距越大，说明参数过拟合风险越高，应优先选两者都稳定的组合。")


if __name__ == "__main__":
    main()
