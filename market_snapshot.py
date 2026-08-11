# -*- coding: utf-8 -*-
"""
活跃截面快照（P1：修复截面分位 / 扩大股票池 / 市场宽度）
========================================================
用东方财富 push2 clist 抓取沪深 A 股“活跃截面”（成交额榜前 600 只，约 6 页），
当天缓存到 SQLite：
  - 截面百分位：动量（60日涨跌幅）、当日涨幅、换手率、成交额、PE（活跃池内计算）
  - 市场宽度：涨跌家数比、涨幅中位数、总成交额（活跃池近似）
  - 股票池：按成交额/换手率取 top N（供 L0 股票池扩展）

口径说明：免费接口限流严重，无法稳定抓全市场 5000+ 只；本模块以“活跃截面
top600”代替全市场，百分位含义为“在该活跃池内的相对位置”。精确全市场截面
需要 Tushare Pro（README 已声明）。离线/接口失败时自动降级：返回 None / 空。
"""
import datetime
import statistics
import time

import requests

import db

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TIMEOUT = 20
MAX_PAGES = 6      # 每页100条，取成交额榜前 6 页 = 约600只活跃股
DEADLINE_SEC = 55  # 整次抓取硬时限，避免拖慢每日流程


def _fetch_rows():
    """东财全市场行情列表（沪深A）。字段：f2价格 f3涨跌幅 f5量 f6额 f8换手
    f9市盈率 f12代码 f14名称 f20总市值 f23市净率 f24 60日涨跌幅 f25年初至今。
    单页上限 100 条，抓成交额榜前 MAX_PAGES 页；多主机轮换应对限流。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    hosts = ["push2.eastmoney.com", "1.push2.eastmoney.com", "33.push2.eastmoney.com",
             "82.push2.eastmoney.com", "push2delay.eastmoney.com"]
    rows = []
    seen = set()
    start = time.time()
    # 单页上限 100 条，抓成交额榜前 MAX_PAGES 页（活跃截面）
    for pn in range(1, MAX_PAGES + 1):
        if time.time() - start > DEADLINE_SEC:
            break
        params = {
            "pn": str(pn), "pz": "100", "po": "1", "np": "1",
            "fltt": "2", "invt": "2", "fid": "f6",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f2,f3,f5,f6,f8,f9,f12,f14,f20,f23,f24,f25",
        }
        diff = []
        for host in hosts:
            try:
                r = requests.get(url.replace("push2.eastmoney.com", host),
                                 params=params, headers=HEADERS, timeout=8)
                diff = ((r.json() or {}).get("data") or {}).get("diff") or []
            except Exception:
                diff = []
            if diff:
                break
        if not diff:
            break
        page_new = 0
        for d in diff:
            def _f(k):
                try:
                    v = d.get(k)
                    return float(v) if v not in (None, "-", "") else None
                except (TypeError, ValueError):
                    return None
            code = str(d.get("f12") or "")
            if not code or code in seen:
                continue
            seen.add(code)
            page_new += 1
            rows.append({
                "code": code,
                "name": str(d.get("f14") or ""),
                "price": _f("f2"),
                "change_pct": _f("f3"),
                "volume": _f("f5"),
                "amount": _f("f6"),
                "turnover": _f("f8"),
                "pe": _f("f9"),
                "total_mv": _f("f20"),
                "pb": _f("f23"),
                "mom60": _f("f24"),
                "yoy": _f("f25"),
            })
        if page_new == 0:
            break
        if len(diff) < 100:
            break
        if len(rows) >= MAX_PAGES * 100:
            break
        time.sleep(0.1)
    if len(rows) < 300:
        raise RuntimeError(f"活跃截面数据不足（仅 {len(rows)} 只）")
    return rows


def get_snapshot(force=False):
    """当天快照（缓存优先）；失败返回最近缓存；无缓存返回 None。"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    cached = db.load_market_snapshot(today)
    if cached and not force:
        return cached
    try:
        rows = _fetch_rows()
        if not rows:
            raise RuntimeError("全市场行情为空")
        changes = [r["change_pct"] for r in rows if r.get("change_pct") is not None]
        snapshot = {
            "date": today,
            "n": len(rows),
            "scope": f"活跃截面top{len(rows)}（成交额榜，非全市场）",
            "rows": rows,
            "breadth": {
                "advancers_ratio": round(sum(1 for c in changes if c > 0) / len(changes), 4)
                if changes else None,
                "median_change": round(statistics.median(changes), 2) if changes else None,
                "total_amount_yi": round(sum(r["amount"] for r in rows
                                             if r.get("amount") is not None) / 1e8, 2),
            },
            "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        db.save_market_snapshot(today, snapshot)
        return snapshot
    except Exception:
        return db.load_market_snapshot()


def _pct(rows, key, code, higher_better=True):
    """某股票在全市场的百分位（0~100）；数据缺失返回 None。"""
    vals = [(r[key], r["code"]) for r in rows
            if r.get(key) is not None and r["code"] == code]
    if not vals:
        return None
    v = vals[0][0]
    allv = [r[key] for r in rows if r.get(key) is not None]
    if len(allv) < 50:
        return None
    below = sum(1 for x in allv if x < v)
    pct = below / len(allv) * 100
    return pct if higher_better else 100 - pct


def cross_sectional(code):
    """返回该股票的截面百分位 dict；快照不可用返回 None。
    mom60_pct：60日涨跌幅百分位（越高越强）；chg_pct：当日涨幅百分位（越高越拥挤）；
    turnover_pct / amount_pct：换手与成交额百分位；pe_pct：市盈率百分位。"""
    snap = get_snapshot()
    if not snap or not snap.get("rows"):
        return None
    rows = snap["rows"]
    return {
        "mom60_pct": _pct(rows, "mom60", code),
        "chg_pct": _pct(rows, "change_pct", code),
        "turnover_pct": _pct(rows, "turnover", code),
        "amount_pct": _pct(rows, "amount", code),
        "pe_pct": _pct(rows, "pe", code, higher_better=False),
        "date": snap.get("date"),
    }


def top_universe(n=120, extra_codes=None):
    """全市场按成交额与换手率取 top N 并集，再并入自选/持仓等。失败返回 None。"""
    snap = get_snapshot()
    if not snap or not snap.get("rows"):
        return None
    rows = snap["rows"]
    by_amount = sorted([r for r in rows if r.get("amount")], key=lambda r: -r["amount"])
    by_turn = sorted([r for r in rows if r.get("turnover")], key=lambda r: -r["turnover"])
    codes, seen = [], set()
    # 自选/持仓/固定关注优先保底，避免被截断
    for c in extra_codes or []:
        if c and c not in seen:
            seen.add(c)
            codes.append(c)
    for r in by_amount[:n] + by_turn[:n]:
        if r["code"] not in seen:
            seen.add(r["code"])
            codes.append(r["code"])
    return codes[:n]


def breadth():
    """市场宽度：涨跌家数比 / 涨幅中位数 / 总成交额(亿)；失败返回 None。"""
    snap = get_snapshot()
    return (snap or {}).get("breadth")
