# -*- coding: utf-8 -*-
"""
任性操作：手动买入/卖出（模拟盘，T+1 开盘执行）
================================================
用途：紫苏叶/量化看板之外的"自己拍板"操作。系统不提示买入，
只在你确认后按你的决定生成模拟订单，并标记为"任性单"（与系统信号单分开统计）。

风控提示（仅供参考，不构成建议）：
  - 入场价、2ATR 止损位、单笔最大亏损会先展示，确认后才下单；
  - 仍遵守 T+1、涨跌停不可成交（次日开盘执行时自动顺延）；
  - 只写模拟盘 journal，不连接任何券商。

用法：
  python manual_trade.py 600519              # 按风险预算股数确认买入
  python manual_trade.py 600519 200          # 指定买入 200 股
  python manual_trade.py 600519 --sell 100   # 手动卖出 100 股
  python manual_trade.py 600519 --yes        # 跳过确认直接下单
"""
import datetime
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import history  # noqa: E402
import journal  # noqa: E402
import quant_engine as Q  # noqa: E402
import rebalance  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EQUITY = 1000000.0
RISK_PER_TRADE = 0.01
MAX_POSITION_PCT = 0.10


def plan(code, shares=None, price=None, side="BUY"):
    """计算入场参考/止损/风险与建议股数，返回 dict（不写任何账本）"""
    quote = Q.fetch_quote(code)
    name = quote.get("name") or code
    rows = history.get_history(code, "hfq")
    atr20 = 0.0
    try:
        atr20 = Q.atr(rows, 20)[-1] or 0.0
    except Exception:
        pass
    entry = float(price or quote.get("price") or 0)
    stop = entry - 2.0 * atr20 if atr20 > 0 else None
    risk_share = (entry - stop) if stop else None
    suggested = 0
    if entry > 0 and risk_share and risk_share > 0:
        by_risk = int(EQUITY * RISK_PER_TRADE / risk_share // 100 * 100)
        by_weight = int(EQUITY * MAX_POSITION_PCT / entry // 100 * 100)
        suggested = max(0, min(by_risk, by_weight))
    if shares is None:
        shares = suggested
    else:
        shares = int(shares)
    chg = quote.get("change_pct") or 0
    lim = 20.0 if str(code).startswith(("300", "301", "688", "689")) else 10.0
    limit_warn = chg >= lim - 1.0
    max_loss = shares * risk_share if risk_share else None
    return {
        "code": code, "name": name, "side": side, "price": quote.get("price"),
        "change_pct": chg, "entry": entry, "atr20": atr20, "stop": stop,
        "risk_share": risk_share, "suggested": suggested, "shares": shares,
        "max_loss": max_loss, "limit_warn": limit_warn,
    }


def execute(info, reason="任性操作"):
    """把任性单写入模拟盘账本（T+1 执行），返回订单"""
    journal.init_db()
    today = datetime.date.today().strftime("%Y-%m-%d")
    side = "BUY" if info["side"] == "BUY" else "SELL"
    if side == "SELL":
        pos = journal.current_positions().get(info["code"], {}).get("shares", 0)
        if info["shares"] <= 0:
            raise RuntimeError("卖出股数必须大于 0")
        if info["shares"] > pos:
            raise RuntimeError(f"持仓不足：当前仅 {pos} 股")
    if info["shares"] <= 0:
        raise RuntimeError("股数必须大于 0（可用系统建议股数，或手动指定）")
    order = rebalance.Order(info["code"], side, info["shares"], reason=reason)
    journal.log_signal(today, info["code"], 0, 0, 0, 0, action=reason)
    journal.save_pending(today, [order])
    return order


def main(argv):
    if len(argv) < 2 or not (argv[1].isdigit() and len(argv[1]) == 6):
        print(__doc__)
        return 1
    code = argv[1]
    shares = None
    price = None
    side = "BUY"
    yes = "--yes" in argv
    i = 2
    while i < len(argv):
        a = argv[i]
        if a == "--sell":
            side = "SELL"
        elif a == "--price" and i + 1 < len(argv):
            try:
                price = float(argv[i + 1])
            except ValueError:
                pass
            i += 1
        elif a == "--yes":
            pass
        elif a.isdigit():
            shares = int(a)
        i += 1
    try:
        info = plan(code, shares=shares, price=price, side=side)
    except Exception as e:
        print(f"❌ 无法获取 {code} 数据：{e}")
        return 1
    act = "买入" if side == "BUY" else "卖出"
    print("=" * 56)
    print(f" 任性{act}（模拟盘，T+1 开盘执行）：{info['name']}({code})")
    print("=" * 56)
    print(f" 现价: {info['price']} ｜ 当日: {info['change_pct']:+.2f}%")
    print(f" 参考入场: {info['entry']:.2f} ｜ 2ATR止损: "
          f"{info['stop']:.2f}" if info["stop"] else "（ATR数据不足，无法给止损）")
    if info["risk_share"]:
        print(f" 单股风险: {info['risk_share']:.2f} ｜ 建议股数(风险预算): {info['suggested']}")
    print(f" 本次股数: {info['shares']}"
          + (f" ｜ 预计最大亏损: {info['max_loss']:.0f} 元" if info["max_loss"] else ""))
    if info["limit_warn"]:
        print(" ⚠ 今日已接近涨停，次日开盘可能无法成交（涨跌停自动顺延）")
    if side == "SELL":
        pos = journal.current_positions().get(code, {}).get("shares", 0)
        print(f" 当前持仓: {pos} 股")
    if not yes:
        try:
            r = input(f"确认{act} {info['shares']} 股？(y/N): ").strip().lower()
        except EOFError:
            r = ""
        if r not in ("y", "yes"):
            print("已取消")
            return 0
    try:
        order = execute(info, reason="任性" + act)
    except Exception as e:
        print(f"❌ 下单失败：{e}")
        return 1
    print(f"✅ 已生成 T+1 {'买单' if side == 'BUY' else '卖单'}：{code} {order.shares} 股"
          f"（{order.reason}，明日开盘执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
