# -*- coding: utf-8 -*-
"""
模块③ 实时风控（三级）：
L1 个股级（3分钟扫描） / L2 组合级（每日收盘） / L3 系统级（熔断）
当前以“模拟盘+报告”方式落地，实盘需接入券商回报与告警通道。
"""


def check_stock(code, pos, quote, rows=None, atr22=0.0):
    """L1 个股级，返回动作列表"""
    actions = []
    price = quote.get("price", 0)
    entry = pos.entry
    if price <= 0:
        return actions
    if price < entry - 2.0 * pos.atr20:
        actions.append(("L1_初始止损", code, "现价 < entry-2ATR20，全平", price))
    if atr22 > 0:
        hi22 = max(r["high"] for r in rows[-22:]) if rows else 0
        chandelier = hi22 - 3.0 * atr22
        if price < chandelier:
            actions.append(("L1_吊灯止损", code, "现价 < 吊灯线，全平", price))
    # 跳空：开盘已低于止损位 → 以开盘价立即市价平
    if quote.get("open") and quote["open"] < entry - 2.0 * pos.atr20:
        actions.append(("L1_跳空处理", code, "开盘已破止损，市价立即平仓", quote["open"]))
    return actions


def check_portfolio(positions, quotes, equity, day_return=0.0,
                    industry_weight=None, total_risk=0.0):
    """L2 组合级，返回动作列表"""
    actions = []
    if day_return < -0.02:
        actions.append(("L2_暂停开新仓", "", "单日亏损>2%", day_return))
    if total_risk > 0.06:
        actions.append(("L2_组合风险超限", "", f"组合风险 {total_risk:.1%}>6%，按风险贡献降序减仓", total_risk))
    for ind, w in (industry_weight or {}).items():
        if w > 0.35:
            actions.append(("L2_行业超限", "", f"{ind} 权重 {w:.1%}>35%，强制减至30%", w))
    return actions


def check_system(nav_series, recent_trade_rets, signal_deviation=None):
    """L3 系统级熔断"""
    actions = []
    peak = 0.0
    dd = 0.0
    for v in nav_series:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak)
    if dd > 0.15:
        actions.append(("L3_熔断", "", f"净值回撤 {dd:.1%}>15%，全部清仓，人工复核", dd))
    if len(recent_trade_rets) >= 8 and sum(1 for r in recent_trade_rets[-8:] if r < 0) >= 8:
        actions.append(("L3_连亏", "", "连续8笔亏损，降至半仓，参数复核", None))
    if signal_deviation is not None and signal_deviation > 0.20:
        actions.append(("L3_信号偏离", "", f"信号与回测偏离 {signal_deviation:.1%}>20%，停机排查", signal_deviation))
    return actions
