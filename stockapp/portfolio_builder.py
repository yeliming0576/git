# -*- coding: utf-8 -*-
"""
模块① 组合构建（按《量化系统落地评审_组合与执行规范》）
仓位由风险预算决定，不由评分决定：评分只排序入选，不用于加权。
"""
from collections import defaultdict
from dataclasses import dataclass

# 规范内部矛盾修正：3ATR目标/2ATR止损=1.5，永远<2；目标位取 4×ATR 使盈亏比≥2 可执行
TARGET_ATR_MULT = 4.0


@dataclass
class Candidate:
    code: str
    name: str = ""
    score: float = 0.0
    entry: float = 0.0
    atr20: float = 0.0
    industry: str = "未知"
    avg_amount_20: float = 0.0


@dataclass
class Position:
    code: str
    shares: int = 0
    entry: float = 0.0
    atr20: float = 0.0
    industry: str = "未知"


def build_portfolio(candidates, equity, current_positions=None,
                    risk_per_trade=0.01, max_weight=0.10, max_positions=20,
                    max_industry=0.30, max_total_risk=0.06, max_exposure=0.70):
    """输入 L6 候选，输出 {code: target_shares} 及组合统计"""
    current = {p.code: p for p in (current_positions or [])}
    targets = {}
    industry_w = defaultdict(float)
    total_risk = 0.0
    exposure = 0.0
    skipped = []
    for c in sorted(candidates, key=lambda x: -x.score):
        if len(targets) >= max_positions:
            break
        stop = c.entry - 2.0 * c.atr20
        if c.entry <= stop or c.atr20 <= 0:
            skipped.append((c.code, "止损无效"))
            continue
        target_price = c.entry + TARGET_ATR_MULT * c.atr20
        if (target_price - c.entry) / (c.entry - stop) < 2.0:
            skipped.append((c.code, "盈亏比<2"))
            continue
        shares = equity * risk_per_trade / (c.entry - stop)
        shares = min(shares, equity * max_weight / c.entry)
        shares = int(shares // 100) * 100
        if shares == 0:
            skipped.append((c.code, "股数不足一手"))
            continue
        cap = max(c.avg_amount_20, 0) * 0.01 / c.entry
        shares = min(shares, int(cap // 100) * 100)
        if shares == 0:
            skipped.append((c.code, "容量不足(>1%成交额)"))
            continue
        w = shares * c.entry / equity
        risk = shares * (c.entry - stop) / equity
        ind = c.industry or "未知"
        if industry_w[ind] + w > max_industry:
            skipped.append((c.code, "行业超限"))
            continue
        if total_risk + risk > max_total_risk:
            skipped.append((c.code, "组合风险超限"))
            continue
        if exposure + w > max_exposure:
            skipped.append((c.code, "总仓位超限"))
            continue
        targets[c.code] = shares
        industry_w[ind] += w
        total_risk += risk
        exposure += w
    return targets, {
        "industry_w": dict(industry_w),
        "total_risk": round(total_risk * 100, 2),
        "exposure": round(exposure * 100, 2),
        "n": len(targets),
        "skipped": skipped,
    }
