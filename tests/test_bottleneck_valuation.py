# -*- coding: utf-8 -*-
"""紫苏叶估值红黄绿灯与信号强度（离线，等价于 selftest 的 pytest 版）"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOTTLENECK_DIR = os.path.join(ROOT, "紫苏叶选股")
if BOTTLENECK_DIR not in sys.path:
    sys.path.insert(0, BOTTLENECK_DIR)

import bottleneck_picker as B  # noqa: E402


def pack(rev=100e8, np_=20e8, growth=20.0, cap=3000e8, pe=30.0):
    return {"market_cap_yi": cap / 1e8, "pe": pe,
            "financials": [{"report_date": "2025-12-31", "revenue": rev,
                            "net_profit": np_, "rev_growth": growth, "roe": 15.0,
                            "gross_margin": 40.0, "net_margin": 10.0,
                            "debt_ratio": 30.0}]}


def test_valuate_lights():
    assert B.valuate(pack(np_=-5e8, rev=100e8, cap=2000e8))["light"] == "黄灯"
    assert B.valuate(pack(rev=50e8, np_=5e8, growth=50.0, cap=3000e8))["light"] == "红灯"
    assert B.valuate(pack(rev=400e8, np_=50e8, growth=15.0, cap=1500e8))["light"] == "绿灯"


def test_market_cap_over_tam_is_red():
    p = pack(rev=200e8, np_=30e8, growth=30.0, cap=2000e8)
    r = B.evaluate_candidate("测试环节", "S", 500, {"code": "600000", "name": "T",
                                                   "verified": {}}, pack=p, cs=None)
    assert r["val"]["light"] == "红灯"


def test_red_light_caps_strength():
    ver = {"customer": True, "revenue": True, "capacity": True, "price": True}
    red = B.valuate(pack(rev=50e8, np_=5e8, growth=50.0, cap=3000e8))
    assert red["light"] == "红灯"
    assert B.strength_of("S", ver, red) <= 2


def test_annual_10y_return_is_computable():
    v = B.valuate(pack(rev=100e8, np_=20e8, growth=10.0, cap=1000e8))
    assert v["annual_10y"] is not None
    assert abs(v["annual_10y"]) < 1
