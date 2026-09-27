# -*- coding: utf-8 -*-
"""v2 指标与特征：离线数值校验"""
import pytest

import v2


def test_sma_basic():
    assert v2.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    assert v2.sma([1, 2], 5) == [None, None]


def test_atr_wilder_first_value_is_defined(rows_factory):
    rows = rows_factory([10, 11, 12, 13, 14, 15])
    atr = v2.atr_wilder(rows, 3)
    assert atr[:3] == [None, None, None]
    assert atr[3] is not None and atr[3] > 0


def test_lin_slope_identity():
    assert v2.lin_slope([0, 1, 2, 3, 4]) == pytest.approx(1.0)
    assert v2.lin_slope([5, 5, 5, 5]) == pytest.approx(0.0)


def test_ts_percentile_rank_bounds():
    up = list(range(1, 21))
    down = list(range(20, 0, -1))
    assert v2.ts_percentile_rank(up, 19) == 100.0
    assert v2.ts_percentile_rank(down, 19) == 0.0
    # 窗口不足 10 个值时给中性值 50
    assert v2.ts_percentile_rank([1, 2, 3], 2) == 50.0


def test_obv_accumulates_on_up_days(rows_factory):
    rows = rows_factory([10, 11, 12], volume=100.0)
    assert v2.obv_series(rows) == [0.0, 100.0, 200.0]
    rows_down = rows_factory([10, 9, 8], volume=100.0)
    assert v2.obv_series(rows_down) == [0.0, -100.0, -200.0]


def test_build_features_shapes(rows_factory, index_map_factory):
    closes = [10 + i * 0.1 for i in range(80)]
    rows = rows_factory(closes)
    f = v2.build_features(rows, index_map_factory(rows), ma_period=20)
    assert len(f) == len(rows)
    last = f[-1]
    for key in ("ma", "ma20", "atr14", "atr20", "close", "vol_ratio"):
        assert key in last
    assert last["close"] == pytest.approx(closes[-1])


def test_status_of_requires_tradability(rows_factory):
    closes = [10 + i * 0.1 for i in range(80)]
    rows = rows_factory(closes)
    f = v2.build_features(rows, {r["date"]: 100.0 for r in rows}, ma_period=20)
    st = v2.status_of(f, len(rows) - 1, "测试股", abs_i=10)
    assert st["l1"] is False
