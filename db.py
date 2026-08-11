# -*- coding: utf-8 -*-
"""
SQLite 数据层（选股系统 v2 配套）
====================================
数据全部存入项目内的 选股数据.db（单文件数据库，零安装、零配置）：
  - rank_history  每日三榜 Z-score 排名历史（多日持续性评分用）
  - hot_picks     每日选股结果（接口无数据时的缓存兜底）
数据库不可用时自动降级到原来的 JSON 文件，不会影响运行。
"""
import datetime
import json
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "选股数据.db")
HISTORY_JSON = os.path.join(BASE, "排名历史.json")
PICKS_JSON = os.path.join(BASE, "热门股缓存.json")


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """建表；并把旧 JSON 数据一次性迁入数据库（只迁一次）"""
    conn = _connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS rank_history (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            score REAL NOT NULL,
            PRIMARY KEY (date, code))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS hot_picks (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            price REAL,
            change_pct REAL,
            amount REAL,
            turnover REAL,
            pe REAL,
            total_mv REAL,
            score REAL,
            PRIMARY KEY (date, code))""")
        conn.commit()
    finally:
        conn.close()
    _migrate_json()


def _migrate_json():
    try:
        conn = _connect()
        try:
            n = conn.execute("SELECT COUNT(*) FROM rank_history").fetchone()[0]
            if n == 0 and os.path.exists(HISTORY_JSON):
                with open(HISTORY_JSON, encoding="utf-8") as f:
                    hist = json.load(f)
                rows = [(d, c, float(v))
                        for d, codes in hist.items() for c, v in codes.items()]
                conn.executemany(
                    "INSERT OR REPLACE INTO rank_history(date, code, score) VALUES(?,?,?)", rows)
            n = conn.execute("SELECT COUNT(*) FROM hot_picks").fetchone()[0]
            if n == 0 and os.path.exists(PICKS_JSON):
                with open(PICKS_JSON, encoding="utf-8") as f:
                    data = json.load(f)
                today = (data.get("fetched_at") or "")[:10] or \
                    datetime.date.today().strftime("%Y-%m-%d")
                rows = [(today, p["code"], p.get("name"), p.get("price"),
                         p.get("change_pct"), p.get("amount"), p.get("turnover"),
                         p.get("pe"), p.get("total_mv"), p.get("score"))
                        for p in data.get("picks", [])]
                conn.executemany(
                    "INSERT OR REPLACE INTO hot_picks VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


# ---------------- 排名历史 ----------------
def save_rank_history(z_today):
    today = datetime.date.today().strftime("%Y-%m-%d")
    conn = _connect()
    try:
        rows = [(today, c, float(v)) for c, v in z_today.items()]
        conn.executemany(
            "INSERT OR REPLACE INTO rank_history(date, code, score) VALUES(?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()


def load_rank_history(days=10):
    """返回最近 N 个日期的 {date: {code: score}}，按日期升序"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT date, code, score FROM rank_history "
            "WHERE date IN (SELECT date FROM rank_history ORDER BY date DESC LIMIT ?)",
            (days,)).fetchall()
    finally:
        conn.close()
    hist = {}
    for date, code, score in rows:
        hist.setdefault(date, {})[code] = score
    return hist


# ---------------- 每日选股结果缓存 ----------------
def save_picks(picks):
    today = datetime.date.today().strftime("%Y-%m-%d")
    conn = _connect()
    try:
        conn.execute("DELETE FROM hot_picks WHERE date=?", (today,))
        rows = [(today, p["code"], p.get("name"), p.get("price"), p.get("change_pct"),
                 p.get("amount"), p.get("turnover"), p.get("pe"),
                 p.get("total_mv"), p.get("score")) for p in picks]
        conn.executemany("INSERT INTO hot_picks VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()


def load_latest_picks():
    conn = _connect()
    try:
        row = conn.execute("SELECT date FROM hot_picks ORDER BY date DESC LIMIT 1").fetchone()
        if not row:
            return None
        rows = conn.execute(
            "SELECT code,name,price,change_pct,amount,turnover,pe,total_mv,score "
            "FROM hot_picks WHERE date=? ORDER BY score DESC", (row[0],)).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    return [{"code": r[0], "name": r[1], "price": r[2], "change_pct": r[3],
             "amount": r[4], "turnover": r[5], "pe": r[6], "total_mv": r[7],
             "score": r[8]} for r in rows]


def load_pick_history(days=7):
    """返回最近 N 天的选股历史 [{date, picks:[...]}]，日期倒序"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT date, code, name, price, change_pct, amount, turnover, pe, total_mv, score "
            "FROM hot_picks WHERE date IN "
            "(SELECT DISTINCT date FROM hot_picks ORDER BY date DESC LIMIT ?) "
            "ORDER BY date DESC, score DESC", (days,)).fetchall()
    finally:
        conn.close()
    history = []
    for row in rows:
        date = row[0]
        if not history or history[-1]["date"] != date:
            history.append({"date": date, "picks": []})
        history[-1]["picks"].append({
            "code": row[1], "name": row[2], "price": row[3], "change_pct": row[4],
            "amount": row[5], "turnover": row[6], "pe": row[7], "total_mv": row[8],
            "score": row[9]})
    return history


def purge_code(code):
    """删除某只股票的本地缓存数据（8年K线缓存、v2分析缓存）。
    保留：选股历史榜单、排名历史、模拟盘交易账本（历史记录不应被删）。"""
    conn = _connect()
    try:
        for table in ("kline_hfq", "kline_qfq", "v2_cache"):
            try:
                conn.execute(f"DELETE FROM {table} WHERE code=?", (code,))
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()


# ---------------- JSON 降级（数据库不可用时） ----------------
def save_rank_history_json(history):
    try:
        with open(HISTORY_JSON, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def load_rank_history_json(days=10):
    try:
        with open(HISTORY_JSON, encoding="utf-8") as f:
            hist = json.load(f)
        dates = sorted(hist)[-days:]
        return {d: hist[d] for d in dates}
    except Exception:
        return {}


def save_picks_json(picks):
    try:
        with open(PICKS_JSON, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                       "picks": picks}, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def load_latest_picks_json():
    try:
        with open(PICKS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("picks") or None
    except Exception:
        return None
