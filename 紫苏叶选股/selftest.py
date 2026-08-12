# -*- coding: utf-8 -*-
"""bottleneck_picker 自检：估值红黄绿灯与信号强度规则（离线，无需网络）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bottleneck_picker as B


def pack(rev=100e8, np_=20e8, growth=20.0, cap=3000e8, pe=30.0):
    return {"market_cap_yi": cap / 1e8, "pe": pe,
            "financials": [{"report_date": "2025-12-31", "revenue": rev,
                            "net_profit": np_, "rev_growth": growth, "roe": 15.0,
                            "gross_margin": 40.0, "net_margin": 10.0,
                            "debt_ratio": 30.0}]}


def main():
    v = B.valuate(pack(np_=-5e8, rev=100e8, cap=2000e8))
    assert v["light"] == "黄灯", v
    print("亏损高PS -> 黄灯 OK")

    v = B.valuate(pack(rev=50e8, np_=5e8, growth=50.0, cap=3000e8))
    assert v["light"] == "红灯", v
    print("PS>30且增速<100 -> 红灯 OK")

    p = pack(rev=200e8, np_=30e8, growth=30.0, cap=2000e8)
    r = B.evaluate_candidate("测试环节", "S", 500, {"code": "600000", "name": "T",
                                                   "verified": {}}, pack=p, cs=None)
    assert r["val"]["light"] == "红灯", r["val"]
    print("市值>TAM20% -> 红灯 OK")

    v = B.valuate(pack(rev=400e8, np_=50e8, growth=15.0, cap=1500e8))
    assert v["light"] == "绿灯", v
    print("PS<10且增长 -> 绿灯 OK")

    v = B.valuate(pack(rev=100e8, np_=20e8, growth=10.0, cap=1000e8))
    assert v["annual_10y"] is not None and abs(v["annual_10y"]) < 1
    print("10年25xPE退出年化 OK:", round(v["annual_10y"] * 100, 2), "%")

    ver = {"customer": True, "revenue": True, "capacity": True, "price": True}
    red_v = B.valuate(pack(rev=50e8, np_=5e8, growth=50.0, cap=3000e8))
    assert red_v["light"] == "红灯"
    assert B.strength_of("S", ver, red_v) <= 2
    print("估值红灯封顶★★ OK")
    print("SELFTEST ALL OK")


if __name__ == "__main__":
    main()
