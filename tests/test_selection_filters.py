# -*- coding: utf-8 -*-
"""selection.py 过滤与低值复苏打分（离线）"""
import pytest

from stockapp import selection


def _row(code, name="测试股", price=10.0, pe=20.0, turnover=5.0, amount=1e8, **kw):
    r = {"code": code, "name": name, "price": price, "pe": pe,
         "turnover": turnover, "amount_yuan": amount}
    r.update(kw)
    return r


def test_z_scores_degrades_gracefully():
    assert selection._z_scores({"a": 1.0, "b": 2.0}) == {"a": 0.0, "b": 0.0}
    assert selection._z_scores({"a": 1.0, "b": 1.0, "c": 1.0}) == \
        {"a": 0.0, "b": 0.0, "c": 0.0}
    z = selection._z_scores({"a": 1.0, "b": 2.0, "c": 3.0})
    assert z["c"] > z["a"]


def test_compute_activity_scores_uses_all_three_lists():
    lists = {
        "volume": [_row("600000", volume=1e7, circ_shares=1e8, nmc_yuan=1e9),
                   _row("600001", volume=5e6, circ_shares=1e8, nmc_yuan=1e9),
                   _row("600002", volume=2e6, circ_shares=1e8, nmc_yuan=1e9)],
        "amount": [_row("600000", nmc_yuan=1e9), _row("600001", nmc_yuan=1e9)],
        "turnover": [_row("600000", turnover=10.0), _row("600001", turnover=5.0)],
    }
    scores, info = selection.compute_activity_scores(lists)
    assert set(info) >= {"600000", "600001"}
    assert scores["600000"] > scores["600001"]


def test_basic_filters_rules():
    scores = {"600000": 3.0, "600001": 2.0, "600002": 1.0, "600003": 0.0}
    info = {
        "600000": _row("600000", price=10.0, turnover=3.0),
        "600001": _row("600001", price=1.0, turnover=3.0),
        "600002": _row("600002", name="ST测试", price=10.0),
        "600003": _row("600003", price=10.0, pe=-5.0),
    }
    out = selection._basic_filters(list(scores), scores, info, price_cap=50.0)
    assert out == ["600000"]


def test_lowval_coarse_filter_turnover_band():
    rows = [
        _row("600000", turnover=5.0, amount_yi=10.0),
        _row("600001", turnover=0.5, amount_yi=10.0),
        _row("600002", turnover=20.0, amount_yi=10.0),
        _row("600003", turnover=5.0, amount_yi=0.0),
        _row("600004", turnover=5.0, amount_yi=10.0, name="*ST测试"),
    ]
    out = selection.lowval_coarse_filter(rows, 3.0, 8.0)
    assert [r["code"] for r in out] == ["600000"]


def test_lowval_recovery_assess_strict_and_relaxed():
    good = [{"rev_growth": 20.0, "profit_growth": 30.0, "report_name": "2026中报"},
            {"rev_growth": 10.0, "profit_growth": 15.0, "report_name": "2026一季报"}]
    mixed = [{"rev_growth": -5.0, "profit_growth": 30.0, "report_name": "2026中报"},
             {"rev_growth": -10.0, "profit_growth": 15.0, "report_name": "2026一季报"}]
    assert selection.lowval_recovery_assess(good)["ok"] is True
    assert selection.lowval_recovery_assess(mixed)["ok"] is False
    assert selection.lowval_recovery_assess(mixed, strict=False)["ok"] is True
    assert selection.lowval_recovery_assess([])["ok"] is False


def test_lowval_score_is_monotonic_in_growth():
    weak = selection.lowval_recovery_assess(
        [{"rev_growth": 2.0, "profit_growth": 3.0, "report_name": "r"},
         {"rev_growth": 1.0, "profit_growth": 1.0, "report_name": "p"}])
    strong = selection.lowval_recovery_assess(
        [{"rev_growth": 40.0, "profit_growth": 50.0, "report_name": "r"},
         {"rev_growth": 1.0, "profit_growth": 1.0, "report_name": "p"}])
    s_weak, _ = selection.lowval_score(weak, 10.0, 30.0, 5.0, 3.0, 8.0)
    s_strong, _ = selection.lowval_score(strong, 10.0, 30.0, 5.0, 3.0, 8.0)
    assert s_strong > s_weak
    s_mid, _ = selection.lowval_score(strong, 10.0, 30.0, 5.5, 3.0, 8.0)
    s_edge, _ = selection.lowval_score(strong, 10.0, 30.0, 3.0, 3.0, 8.0)
    assert s_mid > s_edge


def test_lowval_interleave_by_sector_round_robin():
    rows = [{"code": "A1"}, {"code": "A2"}, {"code": "B1"}]
    sector_of = {"A1": "板块A", "A2": "板块A", "B1": "板块B"}
    out = [r["code"] for r in selection.lowval_interleave_by_sector(rows, sector_of)]
    assert out == ["A1", "B1", "A2"]


def test_lv_pe_median_prefers_sector_then_market():
    n = selection.LOWVAL_PE_MIN_SAMPLE
    sector = [_row(f"60000{i}", pe=10.0 + i) for i in range(n)]
    median, note = selection._lv_pe_median(sector, [])
    assert note.startswith("所属板块")
    assert median == pytest.approx(10.0 + (n - 1) / 2)
    market = [_row(f"00000{i}", pe=20.0 + i) for i in range(n)]
    _, note2 = selection._lv_pe_median(sector[:2], market)
    assert "全市场" in note2
