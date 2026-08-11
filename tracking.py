# -*- coding: utf-8 -*-
"""
近7日选股跟踪：从 SQLite 读取历史选股记录，
联网计算每只股票“选出日收盘价 → 最新收盘价”的区间涨跌幅。
"""
import datetime

import db
import quant_engine as Q


def _load_rows(code):
    try:
        return Q.fetch_kline(code, datalen=60)
    except Exception:
        return None


def _day_return(rows, pick_date):
    """rows: 日K列表；返回 (选出日收盘, 最新价, 区间涨跌幅%)"""
    if not rows:
        return None
    ref = None
    for r in rows:
        if r["date"] >= pick_date:
            ref = r["close"]
            break
    if ref is None:
        ref = rows[-1]["close"]
    latest = rows[-1]["close"]
    ret = round((latest / ref - 1) * 100, 2)
    return round(ref, 2), round(latest, 2), ret


def build_tracking(days=7):
    """返回 [{date, rows:[...], avg_ret, count}]；当日不计算区间涨跌"""
    db.init_db()
    history = db.load_pick_history(days)
    today = datetime.date.today().strftime("%Y-%m-%d")
    rows_cache = {}
    result = []
    for day in history:
        date = day["date"]
        out_rows = []
        rets = []
        for p in day["picks"]:
            row = {"code": p["code"], "name": p.get("name", ""),
                   "price": p.get("price"), "change_pct": p.get("change_pct"),
                   "score": p.get("score"), "ref": None, "latest": None,
                   "ret": None, "today": date == today}
            if date == today:
                row["ref"] = p.get("price")
                row["latest"] = p.get("price")
            else:
                if p["code"] not in rows_cache:
                    rows_cache[p["code"]] = _load_rows(p["code"])
                info = _day_return(rows_cache[p["code"]], date)
                if info:
                    row["ref"], row["latest"], row["ret"] = info
                    if row["ret"] is not None:
                        rets.append(row["ret"])
            out_rows.append(row)
        result.append({
            "date": date,
            "rows": out_rows,
            "avg_ret": round(sum(rets) / len(rets), 2) if rets else None,
            "count": len(rets),
        })
    return result


if __name__ == "__main__":
    for day in build_tracking(7):
        print(day["date"], "avg:", day["avg_ret"])
        for r in day["rows"]:
            print("  ", r["code"], r["name"], "ref:", r["ref"], "latest:", r["latest"], "ret:", r["ret"])
