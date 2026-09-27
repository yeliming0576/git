# -*- coding: utf-8 -*-
"""回测撮合口径：T+1 开盘执行、双边成本、一字涨跌停不可成交（离线）"""
import v2
from conftest import base_features, make_entry_worthy


def _flat_closes(n=60, price=10.0):
    return [price] * n


def test_entry_cost_applied_at_next_open(rows_factory):
    rows = rows_factory(_flat_closes())
    f = base_features(rows)
    make_entry_worthy(f, 30)
    f[31]["rs20"] = -0.2                     # 次日触发离场判定
    trades, eq = v2.backtest(rows, f, "测试股", start_i=0, offset=250)
    assert len(trades) == 1
    t = trades[0]
    assert t["entry_date"] == rows[31]["date"], "应在 T+1 开盘建仓"
    assert t["entry"] == round(rows[31]["open"] * (1 + v2.COST_PER_SIDE), 2)
    assert t["exit_date"] == rows[32]["date"], "应在 T+1 开盘离场"
    assert t["exit"] == round(rows[32]["open"] * (1 - v2.COST_PER_SIDE), 2)
    assert len(eq) == len(rows)


def test_limit_up_open_blocks_entry(rows_factory):
    rows = rows_factory(_flat_closes())
    f = base_features(rows)
    make_entry_worthy(f, 30)
    rows[31]["limit_up"] = True
    rows[31]["open"] = rows[30]["close"] * 1.1     # 一字涨停
    trades, _ = v2.backtest(rows, f, "测试股", start_i=0, offset=250)
    assert trades == [], "一字涨停不应成交"


def test_limit_down_delays_exit(rows_factory):
    rows = rows_factory(_flat_closes())
    f = base_features(rows)
    make_entry_worthy(f, 30)
    f[31]["rs20"] = -0.2                     # i=31 判定离场
    rows[32]["limit_down"] = True            # 跌停无法卖出
    trades, _ = v2.backtest(rows, f, "测试股", start_i=0, offset=250)
    closed = [t for t in trades if not t.get("open")]
    assert len(closed) == 1
    assert closed[0]["exit_date"] == rows[33]["date"], "跌停应顺延到下一可成交日"
