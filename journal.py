# -*- coding: utf-8 -*-
"""
模块④ 日志与绩效（journal.db）：trades / daily_nav / signal_log / pending_orders
含月度归因（Beta/Alpha/t值）与实盘-回测一致性指标。
"""
import datetime
import math
import os
import sqlite3
import statistics

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "journal.db")


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_date TEXT NOT NULL,
            exec_date TEXT NOT NULL,
            code TEXT NOT NULL,
            side TEXT NOT NULL,
            signal_price REAL,
            exec_price REAL,
            slippage_bps REAL,
            shares INTEGER,
            commission REAL,
            stamp_tax REAL,
            reason TEXT,
            reject_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS daily_nav (
            date TEXT PRIMARY KEY,
            equity REAL, cash REAL, position_mv REAL,
            daily_return REAL, benchmark_ret REAL, excess_ret REAL,
            drawdown REAL, n_positions INTEGER, total_risk REAL
        );
        CREATE TABLE IF NOT EXISTS signal_log (
            date TEXT, code TEXT, l1_pass INTEGER, l2_pass INTEGER,
            l3_score REAL, l4_excluded INTEGER, final_action TEXT,
            PRIMARY KEY (date, code)
        );
        CREATE TABLE IF NOT EXISTS pending_orders (
            signal_date TEXT, code TEXT, side TEXT, shares INTEGER,
            reason TEXT, status TEXT DEFAULT 'pending',
            PRIMARY KEY (signal_date, code, side)
        );
        """)
        conn.commit()
    finally:
        conn.close()


def log_signal(date, code, l1, l2, l3, l4_excluded, action):
    conn = _connect()
    try:
        conn.execute("INSERT OR REPLACE INTO signal_log VALUES(?,?,?,?,?,?,?)",
                     (date, code, int(l1), int(l2), l3, int(l4_excluded), action))
        conn.commit()
    finally:
        conn.close()


def log_trade(signal_date, exec_date, code, side, signal_price, exec_price,
              shares, reason="", reject_reason=None):
    slip = (exec_price - signal_price) / signal_price * 10000 if signal_price else None
    commission = min(5.0, exec_price * shares * 0.00025) if exec_price else 0
    stamp = exec_price * shares * 0.001 if side == "SELL" else 0.0
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO trades(signal_date,exec_date,code,side,signal_price,exec_price,"
            "slippage_bps,shares,commission,stamp_tax,reason,reject_reason) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_date, exec_date, code, side, signal_price, exec_price,
             slip, shares, commission, stamp, reason, reject_reason))
        conn.commit()
    finally:
        conn.close()


def update_nav(date, equity, cash, position_mv, benchmark_ret=None, total_risk=0.0):
    conn = _connect()
    try:
        row = conn.execute("SELECT equity FROM daily_nav ORDER BY date DESC LIMIT 1").fetchone()
        prev = row["equity"] if row else equity
        daily = equity / prev - 1 if prev else 0.0
        excess = daily - benchmark_ret if benchmark_ret is not None else None
        all_rows = conn.execute("SELECT equity FROM daily_nav ORDER BY date").fetchall()
        peak = max([r["equity"] for r in all_rows] + [equity])
        dd = (peak - equity) / peak if peak else 0.0
        conn.execute(
            "INSERT OR REPLACE INTO daily_nav(date,equity,cash,position_mv,daily_return,"
            "benchmark_ret,excess_ret,drawdown,n_positions,total_risk) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (date, equity, cash, position_mv, daily, benchmark_ret, excess, dd, 0, total_risk))
        conn.commit()
    finally:
        conn.close()


def current_positions():
    """从成交记录聚合当前持仓 {code: {shares, avg_entry}}"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT code, side, shares, exec_price FROM trades "
            "WHERE reject_reason IS NULL").fetchall()
    finally:
        conn.close()
    agg = {}
    for r in rows:
        d = agg.setdefault(r["code"], {"shares": 0, "cost": 0.0})
        if r["side"] == "BUY":
            d["shares"] += r["shares"]
            d["cost"] += r["shares"] * r["exec_price"]
        else:
            d["shares"] -= r["shares"]
    return {c: {"shares": d["shares"],
                "avg_entry": d["cost"] / d["shares"] if d["shares"] else 0.0}
            for c, d in agg.items() if d["shares"] > 0}


def pending_orders():
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT signal_date,code,side,shares,reason FROM pending_orders "
            "WHERE status='pending'").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def save_pending(signal_date, orders):
    conn = _connect()
    try:
        conn.execute("DELETE FROM pending_orders WHERE signal_date=?", (signal_date,))
        for o in orders:
            conn.execute("INSERT OR REPLACE INTO pending_orders VALUES(?,?,?,?,?,'pending')",
                         (signal_date, o.code, o.side, o.shares, o.reason))
        conn.commit()
    finally:
        conn.close()


def mark_pending_done(signal_date, code, side):
    conn = _connect()
    try:
        conn.execute("UPDATE pending_orders SET status='done' WHERE signal_date=? AND code=? AND side=?",
                     (signal_date, code, side))
        conn.commit()
    finally:
        conn.close()


def attribution(dates, strategy_rets, bench_rets):
    """月度/区间归因：Beta、Alpha、t统计量"""
    if len(strategy_rets) < 10 or len(bench_rets) < 10:
        return None
    bm = statistics.mean(bench_rets)
    sm = statistics.mean(strategy_rets)
    var_b = statistics.pstdev(bench_rets) ** 2 or 1e-12
    beta = sum((a - sm) * (b - bm) for a, b in zip(strategy_rets, bench_rets)) / \
        (len(strategy_rets) * var_b)
    alpha = sm - beta * bm
    resid = [a - (alpha + beta * b) for a, b in zip(strategy_rets, bench_rets)]
    sd_r = statistics.pstdev(resid) or 1e-12
    t = alpha / (sd_r / math.sqrt(len(resid)))
    return {"beta": round(beta, 3), "alpha_daily": round(alpha * 100, 3),
            "alpha_annual": round(alpha * 252 * 100, 2), "t": round(t, 2),
            "significant": abs(t) >= 2}


def consistency(signals_expected, signals_actual, fills, avg_slippage):
    return {
        "signal_rate": round(signals_actual / signals_expected * 100, 1) if signals_expected else None,
        "fill_rate": round(fills / signals_actual * 100, 1) if signals_actual else None,
        "avg_slippage_bps": round(avg_slippage, 1) if avg_slippage is not None else None,
    }


if __name__ == "__main__":
    init_db()
    print("journal.db 就绪")
