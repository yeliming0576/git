# -*- coding: utf-8 -*-
"""
v2 数据层：8 年历史日线（腾讯接口分页取后复权 hfq，展示用前复权 qfq）
- 回测用 hfq（历史不变、可复现）；展示/图表用 qfq（与现价可比）
- 数据缓存进 SQLite（kline_hfq / kline_qfq / index_kline），每天增量更新
- 自动清洗：剔除停牌日（volume==0）、标记涨跌停日
"""
import datetime
import json
import os
import sqlite3

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Referer": "https://gu.qq.com/",
}

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "数据")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "选股数据.db")
YEARS = 8


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = _connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS kline_hfq (
            code TEXT NOT NULL, date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (code, date))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS kline_qfq (
            code TEXT NOT NULL, date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (code, date))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS index_kline (
            symbol TEXT NOT NULL, date TEXT NOT NULL, close REAL,
            PRIMARY KEY (symbol, date))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS v2_cache (
            code TEXT NOT NULL, date TEXT NOT NULL, payload TEXT,
            PRIMARY KEY (code, date))""")
        conn.commit()
    finally:
        conn.close()


def _tencent_kline(symbol, beg, end, count, adj):
    """腾讯日K，返回原始行列表；count<=640 时稳定返回区间内最新 count 条"""
    r = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                     params={"param": f"{symbol},day,{beg},{end},{count},{adj}"},
                     headers=HEADERS, timeout=20)
    r.raise_for_status()
    d = r.json()
    data = (d.get("data") or {}).get(symbol) or {}
    key = {"hfq": "hfqday", "qfq": "qfqday"}.get(adj, "day")
    kl = data.get(key) or data.get("day") or []
    return kl


def _chunks(start_date, end_date):
    """把区间切成每段<=640 根的小段（腾讯 hfq 单次上限）"""
    out = []
    cur = start_date
    while cur < end_date:
        nxt = min(cur + datetime.timedelta(days=730), end_date)
        out.append((cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
        cur = nxt + datetime.timedelta(days=1)
    return out


def fetch_history(code, adj="hfq", years=YEARS):
    """拉取并缓存个股日K（含清洗），返回 [{date,open,high,low,close,volume}] 升序"""
    init_db()
    end = datetime.date.today()
    start = end - datetime.timedelta(days=int(years * 365.25) + 30)
    symbol = ("sh" if code.startswith(("6", "9")) else "sz") + code
    all_rows = []
    for beg, fin in _chunks(start, end):
        try:
            kl = _tencent_kline(symbol, beg, fin, 2000, adj)
        except Exception:
            kl = []
        for p in kl:
            try:
                all_rows.append({
                    "date": p[0],
                    "open": float(p[1]),
                    "close": float(p[2]),
                    "high": float(p[3]),
                    "low": float(p[4]),
                    "volume": float(p[5]),
                })
            except Exception:
                continue
    # 去重 + 排序 + 清洗（剔除停牌日 volume==0）
    seen = set()
    rows = []
    for r in sorted(all_rows, key=lambda x: x["date"]):
        if r["date"] in seen:
            continue
        seen.add(r["date"])
        if r["volume"] <= 0:
            continue
        rows.append(r)
    if rows:
        _save_rows(code, rows, adj)
    return rows


def _save_rows(code, rows, adj):
    table = "kline_hfq" if adj == "hfq" else "kline_qfq"
    conn = _connect()
    try:
        conn.execute(f"DELETE FROM {table} WHERE code=?", (code,))
        conn.executemany(
            f"INSERT INTO {table}(code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
            [(code, r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"])
             for r in rows])
        conn.commit()
    finally:
        conn.close()


def load_history(code, adj="hfq"):
    init_db()
    table = "kline_hfq" if adj == "hfq" else "kline_qfq"
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT date,open,high,low,close,volume FROM {table} WHERE code=? ORDER BY date",
            (code,)).fetchall()
    finally:
        conn.close()
    return [{"date": r[0], "open": r[1], "high": r[2], "low": r[3],
             "close": r[4], "volume": r[5]} for r in rows]


def get_history(code, adj="hfq", years=YEARS):
    """缓存优先，本地不足 years 年时自动补拉"""
    rows = load_history(code, adj)
    if not rows:
        return fetch_history(code, adj, years)
    first = datetime.datetime.strptime(rows[0]["date"], "%Y-%m-%d").date()
    need = datetime.date.today() - datetime.timedelta(days=int(years * 365.25))
    if first > need or len(rows) < 400:
        return fetch_history(code, adj, years)
    return rows


def fetch_index(symbol="sh000300", years=YEARS):
    """指数日K（指数无复权概念，用原始收盘），缓存"""
    init_db()
    end = datetime.date.today()
    start = end - datetime.timedelta(days=int(years * 365.25) + 30)
    all_rows = []
    for beg, fin in _chunks(start, end):
        try:
            kl = _tencent_kline(symbol, beg, fin, 2000, "qfq")
        except Exception:
            kl = []
        for p in kl:
            try:
                all_rows.append({"date": p[0], "close": float(p[2])})
            except Exception:
                continue
    rows = {r["date"]: r["close"] for r in all_rows}
    conn = _connect()
    try:
        conn.execute("DELETE FROM index_kline WHERE symbol=?", (symbol,))
        conn.executemany(
            "INSERT INTO index_kline(symbol,date,close) VALUES(?,?,?)",
            [(symbol, d, c) for d, c in rows.items()])
        conn.commit()
    finally:
        conn.close()
    return sorted([{"date": d, "close": c} for d, c in rows.items()], key=lambda x: x["date"])


def get_index(symbol="sh000300", years=YEARS):
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT date,close FROM index_kline WHERE symbol=? ORDER BY date", (symbol,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return fetch_index(symbol, years)
    first = datetime.datetime.strptime(rows[0][0], "%Y-%m-%d").date()
    if first > datetime.date.today() - datetime.timedelta(days=int(years * 365.25)):
        return fetch_index(symbol, years)
    return [{"date": r[0], "close": r[1]} for r in rows]


def mark_limits(rows, name=""):
    """标记涨跌停：主板10% / 创业板科创板20% / ST 5%，返回新列表（含 limit_pct/limit_up/limit_down）"""
    out = []
    for i, r in enumerate(rows):
        prev = rows[i - 1]["close"] if i else r["open"]
        pct = (r["close"] / prev - 1) if prev else 0
        is_st = name.startswith(("ST", "*ST", "退"))
        if name.startswith(("300", "301", "688", "689")):
            lim = 0.20
        elif is_st:
            lim = 0.05
        else:
            lim = 0.10
        r = dict(r)
        r["pct"] = pct
        r["limit_pct"] = lim
        r["limit_up"] = pct >= lim - 0.005
        r["limit_down"] = pct <= -(lim - 0.005)
        out.append(r)
    return out
