# -*- coding: utf-8 -*-
"""公共回测引擎：接口结构与统一口径（离线）"""
import pytest

import backtest_engine as E
import quant_engine as Q
import v2
from conftest import base_features


def test_run_signals_entry_exit_and_stats(rows_factory):
    rows = rows_factory([10.0] * 6)
    signals = [None, "B", None, "S", None, None]
    res = E.run_signals(rows, signals)
    assert len(res["trades"]) == 1
    t = res["trades"][0]
    assert t["entry_date"] == rows[2]["date"], "T 日信号 → T+1 开盘买入"
    assert t["exit_date"] == rows[4]["date"]
    assert t["entry_price"] == round(rows[2]["open"] * (1 + E.COST_PER_SIDE), 2)
    assert t["exit_price"] == round(rows[4]["open"] * (1 - E.COST_PER_SIDE), 2)
    assert len(res["equity"]) == len(rows) - 1
    assert res["stats"]["n"] == 1
    assert "meta" in res and res["meta"]["cost_per_side"] == E.COST_PER_SIDE


def test_quant_engine_backtest_keeps_legacy_shape(rows_factory):
    rows = rows_factory([10.0] * 6)
    signals = [None, "B", None, "S", None, None]
    bt = Q.backtest(rows, signals)
    for key in ("trades", "n", "win_rate", "avg_win", "avg_loss", "profit_factor",
                "total_ret", "max_drawdown", "eq"):
        assert key in bt
    # 双边成本合计 0.30%，平进平出应约 -0.30%
    assert bt["total_ret"] == pytest.approx(-0.3, abs=0.02)
    assert bt["max_drawdown"] >= 0


def test_run_returns_unified_structure(rows_factory):
    rows = rows_factory([10.0] * 60)
    f = base_features(rows)
    res = E.run(rows, f, lambda i: v2.status_of(f, i, "测试股", abs_i=i + 250),
                start_i=0)
    assert set(res) == {"trades", "equity", "stats", "meta"}
    assert res["trades"] == []
    assert res["stats"]["insufficient_sample"] is True
    assert res["meta"]["bars"] == len(rows)


def test_walk_forward_and_grid_smoke(rows_factory):
    closes = [10.0] * 1300
    rows = rows_factory(closes, start="2020-01-01")
    f = base_features(rows)
    trades, eq = E.walk_forward(
        rows, f, lambda i0, sub_rows, sub_f: (
            lambda i: v2.status_of(sub_f, i, "测试股", abs_i=i + i0)))
    assert trades == []
    assert len(eq) >= 1

    feat = {ma: base_features(rows) for ma in (40, 60, 80)}
    out = E.grid(rows, feat, lambda ma, atr_m, rs_t: (
        lambda i, _f=feat[ma], _ma=ma, _rs=rs_t: v2.status_of(_f, i, "测试股", _ma, _rs)))
    assert len(out) == 3 * 3 * 3
    assert {"atr", "ma", "rs", "n", "cagr", "mdd"} <= set(out[0])
