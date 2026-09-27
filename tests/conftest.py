# -*- coding: utf-8 -*-
"""pytest 公共夹具：把项目根加入 sys.path，并提供离线合成数据工厂。

所有用例默认不联网（pytest.ini 里 -m "not network"），
需要真实行情的用例请加 @pytest.mark.network。
"""
import datetime
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture
def rows_factory():
    """合成日线序列"""
    def _make(closes, volume=1_000_000.0, limit_pct=0.1, start="2024-01-01"):
        y, m, d = (int(x) for x in start.split("-"))
        base = datetime.date(y, m, d)
        rows = []
        for i, c in enumerate(closes):
            prev = closes[i - 1] if i else c
            rows.append({
                "date": (base + datetime.timedelta(days=i)).isoformat(),
                "open": prev, "high": max(prev, c) * 1.01,
                "low": min(prev, c) * 0.99, "close": c,
                "volume": volume,
                "limit_up": False, "limit_down": False, "limit_pct": limit_pct,
            })
        return rows
    return _make


@pytest.fixture
def index_map_factory():
    """合成基准净值映射"""
    def _make(rows, start=100.0, step=0.05):
        return {r["date"]: start + i * step for i, r in enumerate(rows)}
    return _make


def base_features(rows, ma=9.5, atr=0.5):
    """构造一份中性特征表，供回测用例按需改写成建仓信号"""
    f = []
    for r in rows:
        f.append({
            "close": r["close"], "ma": ma, "ma20": ma,
            "atr14": atr, "atr20": atr, "atr22": atr,
            "slope60": 0.0, "rs20": 0.0, "rs60": 0.0, "rs_rank": 50.0,
            "mom_score": 50.0, "vol_pct": 50.0, "vol_ratio": 1.0,
            "avg_amount": 0.0, "obv_slope": 0.0, "divergence": False,
        })
    return f


def make_entry_worthy(f, idx, ma=9.5, atr=0.5, close=10.0):
    """把 f[idx] 改写成满足 L1+L2 且 L3>=65 的强势可入状态"""
    d = f[idx]
    d.update({"avg_amount": 2e8, "close": close, "ma": ma, "ma20": ma,
              "slope60": 0.5, "rs20": 0.1, "rs60": 0.1, "rs_rank": 90.0,
              "mom_score": 100.0, "vol_pct": 20.0, "vol_ratio": 2.0,
              "atr20": atr, "atr22": atr, "divergence": False})
    return d
