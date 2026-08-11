# -*- coding: utf-8 -*-
"""
模块② 调仓执行：目标持仓 vs 当前持仓 → 订单列表；可交易性检查（回测与实盘同一份代码）。
"""
from dataclasses import dataclass


@dataclass
class Order:
    code: str
    side: str            # BUY / SELL
    shares: int
    reason: str = ""
    exec_price: float = 0.0


def generate_orders(targets, current, quotes=None):
    """targets: {code: shares}；current: {code: Position}；quotes 可选，提供时做可交易性预检"""
    orders = []
    for code, pos in current.items():
        if code not in targets:
            orders.append(Order(code, "SELL", pos.shares, reason="exit_signal"))
        elif targets[code] < pos.shares:
            orders.append(Order(code, "SELL", pos.shares - targets[code], reason="reduce"))
    for code, shares in targets.items():
        held = current.get(code)
        held_shares = held.shares if held else 0
        if shares > held_shares:
            orders.append(Order(code, "BUY", shares - held_shares, reason="entry"))
    if quotes:
        orders = [o for o in orders if is_tradable(o, quotes)]
    return orders


def is_tradable(order, quote):
    """quote: {suspended, open, limit_up, limit_down} —— 回测与实盘共用"""
    if quote.get("suspended"):
        return False
    if order.side == "BUY" and quote.get("open", 0) >= (quote.get("limit_up") or 0) * 0.995:
        return False
    if order.side == "SELL" and quote.get("open", 0) <= (quote.get("limit_down") or 0) * 1.005:
        return False
    return True


def execution_style(amount):
    """按单笔金额分档委托方式"""
    if amount < 500000:
        return "限价单 + 超价 0.3%"
    if amount <= 2000000:
        return "分 3 笔限价，间隔 5 分钟"
    return "VWAP 算法单（需券商算法交易权限）"
