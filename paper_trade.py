# -*- coding: utf-8 -*-
"""
组合与执行模拟盘（按《量化系统落地评审_组合与执行规范》）
流程：T日 L0-L6 → 组合构建 → 订单 → T+1 开盘执行（模拟）→ 风控 → 日志 → 组合报告
说明：当前为模拟盘（过渡期），不连接券商；实盘接入时复用同一套 is_tradable/风控逻辑。
"""
import datetime
import os

import history
import journal
import portfolio_builder
import quant_engine as Q
import rebalance
import risk_monitor
import selection
import v2

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE, "报告归档", "组合")
os.makedirs(REPORT_DIR, exist_ok=True)
EQUITY = 1000000.0           # 模拟总权益（元）
WATCH_FILE = os.path.join(BASE, "自选股.txt")
FIXED = ["605060"]


def _prog(pct, msg):
    try:
        import progress
        progress.report("组合模拟盘", pct, msg)
    except Exception:
        pass


def read_watchlist():
    if not os.path.exists(WATCH_FILE):
        return []
    codes = []
    with open(WATCH_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.isdigit() and len(line) == 6:
                codes.append(line)
    return codes


def today_str():
    return datetime.date.today().strftime("%Y-%m-%d")


def _ledger(initial_equity=EQUITY):
    """按成交流水还原现金余额与已实现收益（FIFO 配对）。
    买入：现金 -= 成交额+佣金；卖出：现金 += 成交额-佣金-印花税。
    返回 (cash, realized_returns%)。"""
    conn = journal._connect()
    try:
        rows = conn.execute(
            "SELECT code, side, exec_price, shares, commission, stamp_tax "
            "FROM trades WHERE reject_reason IS NULL ORDER BY id").fetchall()
    finally:
        conn.close()
    cash = float(initial_equity)
    fifo = {}
    realized = []
    for r in rows:
        px = float(r["exec_price"] or 0)
        sh = int(r["shares"] or 0)
        comm = float(r["commission"] or 0)
        stamp = float(r["stamp_tax"] or 0)
        if sh <= 0:
            continue
        if r["side"] == "BUY":
            cost = px * sh + comm
            cash -= cost
            fifo.setdefault(r["code"], []).append([sh, cost / sh])
        else:
            proceeds = px * sh - comm - stamp
            cash += proceeds
            q = fifo.setdefault(r["code"], [])
            remaining = sh
            cost_basis = 0.0
            while remaining > 0 and q:
                lot = q[0]
                take = min(lot[0], remaining)
                cost_basis += take * lot[1]
                lot[0] -= take
                remaining -= take
                if lot[0] <= 0:
                    q.pop(0)
            if cost_basis:
                realized.append((proceeds - cost_basis) / cost_basis * 100)
    return round(cash, 2), realized


def _mark_prices(positions):
    """持仓按最新价估值；行情失败回退日K收盘价，再失败用成本价。"""
    prices = {}
    for code in positions:
        try:
            prices[code] = Q.fetch_quote(code)["price"]
        except Exception:
            try:
                rows = history.get_history(code, "hfq")
                prices[code] = rows[-1]["close"]
            except Exception:
                prices[code] = positions[code]["avg_entry"]
    return prices


def _industry_weights(positions, prices, equity):
    """当前持仓的行业权重（用于 L2 行业超限检查）"""
    w = {}
    for code, d in positions.items():
        mv = int(d["shares"]) * float(prices.get(code, d["avg_entry"]))
        try:
            ind = selection.fetch_stock_industry(code) or "未知"
        except Exception:
            ind = "未知"
        w[ind] = w.get(ind, 0.0) + mv
    return {k: v / equity for k, v in w.items()} if equity > 0 else w


def execute_pending(rows_map):
    """T+1 开盘执行昨日挂单（模拟）：用当日开盘价成交，涨跌停/停牌顺延"""
    today = today_str()
    executed = []
    for p in journal.pending_orders():
        code = p["code"]
        rows = rows_map.get(code) or history.get_history(code, "hfq")
        bars = {r["date"]: r for r in rows}
        bar = bars.get(today)
        if not bar or len(rows) < 2:
            continue
        prev = rows[rows.index(bar) - 1]["close"] if rows.index(bar) > 0 else bar["open"]
        lim = 0.10
        if code.startswith(("300", "301", "688", "689")):
            lim = 0.20
        quote = {"suspended": False, "open": bar["open"],
                 "limit_up": prev * (1 + lim), "limit_down": prev * (1 - lim)}
        order = rebalance.Order(code, p["side"], p["shares"], reason=p["reason"])
        if not rebalance.is_tradable(order, quote):
            journal.log_trade(p["signal_date"], today, code, p["side"], None, None,
                              p["shares"], reason=p["reason"],
                              reject_reason="limit_or_suspended")
            journal.mark_pending_done(p["signal_date"], code, p["side"])
            continue
        px = bar["open"] * (1 + (0.0015 if p["side"] == "BUY" else -0.0015))
        journal.log_trade(p["signal_date"], today, code, p["side"],
                          bar["open"], round(px, 2), p["shares"], reason=p["reason"])
        journal.mark_pending_done(p["signal_date"], code, p["side"])
        executed.append((code, p["side"], p["shares"], px))
    return executed


def main():
    journal.init_db()
    today = today_str()
    print("=" * 56)
    print(f"组合与执行模拟盘 | {today} | 总权益 {EQUITY:.0f} 元")
    print("=" * 56)

    # 1) 执行昨日挂单（T+1 开盘）
    _prog(82, "执行昨日挂单")
    rows_map = {}
    executed = execute_pending(rows_map)
    if executed:
        print("已执行昨日订单:", executed)

    # 2) L0 股票池
    watch = read_watchlist()
    positions = journal.current_positions()
    universe = selection.build_universe(35, extra_codes=watch + list(positions) + FIXED)
    print(f"L0 股票池: {len(universe)} 只")
    _prog(84, f"L0 股票池 {len(universe)} 只")

    # 3) L1~L3（v2 单票分析，按日缓存）
    cands = []
    for i, code in enumerate(universe):
        _prog(85 + 10 * (i + 1) / max(len(universe), 1), f"v2 分析 {code}")
        try:
            q = Q.fetch_quote(code)
        except Exception:
            continue
        try:
            a2 = v2.analyze_v2(code, q["name"])
        except Exception:
            continue
        if not a2:
            continue
        bt_n = (a2.get("bt") or {}).get("n") or 0
        if a2["status"] != "强势可入":
            journal.log_signal(today, code, a2["l1"], a2["l2"], a2["l3"],
                               0, a2["status"] if a2 else "数据失败")
            continue
        if bt_n < 30:
            # P3：回测样本不足不输出结论，也不入候选
            journal.log_signal(today, code, a2["l1"], a2["l2"], a2["l3"],
                               0, f"回测样本不足({bt_n}笔)")
            print(f"  {code} 回测样本不足({bt_n} 笔)，跳过")
            continue
        rows = history.get_history(code, "hfq")
        rows_map[code] = rows
        last = a2["last"]
        mode = "截面动量" if a2.get("cs_used") else "自身时序"
        print(f"  {code} {q['name']} 强势可入（相对强度={mode}）")
        cands.append(portfolio_builder.Candidate(
            code=code, name=q["name"], score=a2["l3"],
            entry=last["close"], atr20=last["atr20"] or 0,
            industry=selection.fetch_stock_industry(code) or "未知",
            avg_amount_20=last.get("avg_amount") or 0))

    # 4) L4 热度负向剔除
    l4_rows = {}
    for c in cands:
        rows = rows_map.get(c.code) or history.get_history(c.code, "hfq")
        l4_rows[c.code] = rows
    excluded = selection.heat_exclude([c.code for c in cands], l4_rows)
    cands = [c for c in cands if c.code not in excluded]
    for c in excluded:
        journal.log_signal(today, c, 1, 1, 0, 1, "L4热度过高剔除")
    print(f"L4 热度剔除: {len(excluded)} 只 -> 候选 {len(cands)} 只")

    # 5) L5 行业中性 + L6 前20（build_portfolio 内含行业上限/风险约束）
    current = [portfolio_builder.Position(code=d["code"], shares=d["shares"],
                                          entry=d["avg_entry"]) for d in
               [{"code": k, "shares": v["shares"], "avg_entry": v["avg_entry"]}
                for k, v in positions.items()]]
    targets, stats = portfolio_builder.build_portfolio(cands, EQUITY, current)
    _prog(97, "目标持仓已生成")
    print(f"目标持仓: {stats['n']} 只 | 组合风险 {stats['total_risk']}% | 仓位 {stats['exposure']}%")
    for code, sh in targets.items():
        c = next((x for x in cands if x.code == code), None)
        stop = c.entry - 2 * c.atr20
        print(f"  {code} {c.name} {sh}股 入场≈{c.entry:.2f} 止损≈{stop:.2f} 行业:{c.industry}")

    # 6) 订单（T日生成，T+1 执行）
    cur_pos = {p.code: p for p in current}
    orders = rebalance.generate_orders(targets, cur_pos)
    for o in orders:
        journal.log_signal(today, o.code, 1, 1, 0, 0, f"{o.side}_{o.reason}")
    journal.save_pending(today, orders)
    print(f"已生成订单 {len(orders)} 笔（T+1 开盘执行）")

    # 7) 风控快照 + 净值（按持仓现价 mark-to-market）
    positions = journal.current_positions()
    cash, realized_rets = _ledger(EQUITY)
    prices = _mark_prices(positions)
    position_mv = sum(int(d["shares"]) * float(prices.get(code, d["avg_entry"]))
                      for code, d in positions.items())
    equity_now = round(cash + position_mv, 2)
    prev_row = journal._connect().execute(
        "SELECT equity FROM daily_nav ORDER BY date DESC LIMIT 1").fetchone()
    prev_equity = prev_row["equity"] if prev_row else EQUITY
    day_return = equity_now / prev_equity - 1 if prev_equity else 0.0
    nav_series = [r["equity"] for r in journal._connect().execute(
        "SELECT equity FROM daily_nav ORDER BY date").fetchall()] + [equity_now]
    actions = []
    for code, d in positions.items():
        quote = {"price": float(prices.get(code, 0) or 0), "open": None}
        rows = None
        atr22 = 0.0
        try:
            rows = history.get_history(code, "hfq")
            atr22 = Q.atr(rows, 22)[-1] or 0.0
        except Exception:
            pass
        actions += risk_monitor.check_stock(
            code, portfolio_builder.Position(code=code, shares=d["shares"],
                                              entry=d["avg_entry"]),
            quote, rows=rows, atr22=atr22)
    ind_w = _industry_weights(positions, prices, equity_now)
    actions += risk_monitor.check_portfolio(
        positions, prices, equity_now, day_return=day_return,
        industry_weight=ind_w, total_risk=stats["total_risk"] / 100)
    actions += risk_monitor.check_system(nav_series, realized_rets, signal_deviation=None)
    journal.update_nav(today, equity_now, round(cash, 2), round(position_mv, 2),
                       None, stats["total_risk"] / 100)
    print(f"组合净值: {equity_now:.2f} 元（现金 {cash:.2f} + 持仓市值 {position_mv:.2f}，"
          f"今日 {day_return:+.2%}）")
    for a in actions:
        print("风控:", a)
        try:
            level = a[0].split("_")[0] if "_" in a[0] else a[0]
            journal.log_risk(today, level, a[1] or "", a[2],
                             str(a[3]) if len(a) > 3 else "")
        except Exception:
            pass
    run_days = journal.nav_distinct_days()
    risk_count = journal.risk_event_count()
    if journal.nav_ready():
        print(f"净值数据已达标（{run_days} 天且有真实持仓市值），月度归因可启用（需接入基准收益率）")
    else:
        print(f"净值数据尚不足（{run_days} 天，且需出现真实持仓市值），月度归因暂不启用")
    render_report(today, targets, stats, orders, actions, cands, excluded, equity_now,
                  run_days=run_days, risk_count=risk_count)
    _prog(99, "组合报告已生成")
    print("组合报告已生成: 组合与执行报告.html")


def render_report(today, targets, stats, orders, actions, cands, excluded, equity=None,
                  run_days=None, risk_count=None):
    equity = equity or EQUITY
    run_days = run_days if run_days is not None else journal.nav_distinct_days()
    risk_count = risk_count if risk_count is not None else journal.risk_event_count()
    rows = ""
    for code, sh in targets.items():
        c = next((x for x in cands if x.code == code), None)
        if not c:
            continue
        stop = c.entry - 2 * c.atr20
        rows += (f"<tr><td>{code}</td><td>{c.name}</td><td>{c.industry}</td>"
                 f"<td>{sh}</td><td>{c.entry:.2f}</td><td>{stop:.2f}</td>"
                 f"<td>{c.score:.1f}</td></tr>")
    ord_rows = "".join(
        f"<tr><td>{o.code}</td><td>{o.side}</td><td>{o.shares}</td><td>{o.reason}</td></tr>"
        for o in orders)
    act_rows = "".join(
        f"<tr><td>{a[0]}</td><td>{a[1]}</td><td>{a[2]}</td></tr>" for a in actions)
    ex_rows = "、".join(excluded) if excluded else "无"
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>组合与执行报告 {today}</title>
<style>
body{{background:#f5f6f8;color:#1f2937;font-family:"Microsoft YaHei",sans-serif;padding:28px;}}
.wrap{{max-width:1100px;margin:0 auto;}}
h1{{font-size:24px;}} .sub{{color:#6b7280;font-size:13px;margin:6px 0 14px;}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;}}
.card{{background:#fff;border:1px solid #e6e9ef;border-radius:12px;padding:14px;text-align:center;}}
.card .n{{font-size:20px;font-weight:700;}} .card .l{{font-size:12px;color:#6b7280;}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;margin:10px 0 18px;font-size:13px;}}
th,td{{padding:8px 10px;border-bottom:1px solid #eef1f6;text-align:right;white-space:nowrap;}}
th{{background:#f1f5f9;}} td:first-child,th:first-child{{text-align:left;}}
h2{{font-size:18px;margin:20px 0 4px;color:#2563eb;border-left:3px solid #2563eb;padding-left:8px;}}
.warn{{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:12px 16px;font-size:13px;color:#92400e;}}
</style></head><body><div class="wrap">
<h1>组合与执行报告（模拟盘）</h1>
<div class="sub">生成时间 {today} · 总权益 {equity:,.0f} 元（按持仓现价估值） · 按《组合与执行规范》：热度=L4出口剔除、15~20只、单票≤10%、风险预算1%、成本0.30%往返</div>
<div class="cards">
  <div class="card"><div class="n">{stats['n']}</div><div class="l">目标持仓数</div></div>
  <div class="card"><div class="n">{stats['total_risk']}%</div><div class="l">组合风险敞口</div></div>
  <div class="card"><div class="n">{stats['exposure']}%</div><div class="l">总仓位</div></div>
  <div class="card"><div class="n">{len(orders)}</div><div class="l">待执行订单</div></div>
</div>
<h2>目标持仓（风险预算定仓，非评分加权）</h2>
<table><tr><th>代码</th><th>名称</th><th>行业</th><th>股数</th><th>入场参考</th><th>止损(2ATR)</th><th>L3评分</th></tr>{rows or '<tr><td colspan="7">今日无候选通过 L2/L4（市场偏弱时属正常）</td></tr>'}</table>
<h2>待执行订单（T+1 开盘）</h2>
<table><tr><th>代码</th><th>方向</th><th>股数</th><th>原因</th></tr>{ord_rows or '<tr><td colspan="4">无订单</td></tr>'}</table>
<h2>风控动作</h2>
<table><tr><th>级别</th><th>标的</th><th>说明</th></tr>{act_rows or '<tr><td colspan="3">无触发</td></tr>'}</table>
<h2>L4 热度剔除</h2>
<div class="sub">{ex_rows}</div>
<div class="warn">模拟盘说明：订单 T 日生成、T+1 开盘执行；涨跌停/停牌顺延；三级风控已接通真实数据并记录到 journal.risk_log。<br>
实盘门槛：模拟运行 ≥90 天（当前 {run_days} 天）；风控实际触发 ≥1 次（当前 {risk_count} 次）；信号一致率>95%、成交率>90%、平均滑点<15bps。达标前不接实盘。</div>
</div></body></html>"""
    with open(os.path.join(REPORT_DIR, "组合与执行报告.html"), "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
