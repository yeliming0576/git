# -*- coding: utf-8 -*-
"""
抓取联德股份(605060.SH, 联德精密)近半年日K线 + 实时行情
数据源: 新浪日K线 + 腾讯实时/日K交叉验证
"""
import sys
import json
import datetime
import re
import os

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "lunde_data.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120",
    "Referer": "https://finance.sina.com.cn",
}


def fetch_sina_kline(symbol="sh605060", datalen=260):
    """新浪日K线(前复权), volume单位=股"""
    url = (
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_/"
        "CN_MarketDataService.getKLineData"
        f"?symbol={symbol}&scale=240&ma=no&datalen={datalen}"
    )
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    m = re.search(r"\((\[.*\])\)\s*;?\s*$", r.text, re.S)
    if not m:
        raise RuntimeError("新浪K线解析失败: " + r.text[:200])
    rows = json.loads(m.group(1))
    return [{
        "date": x["day"],
        "open": float(x["open"]),
        "high": float(x["high"]),
        "low": float(x["low"]),
        "close": float(x["close"]),
        "volume": float(x["volume"]),   # 股
    } for x in rows]


def fetch_tencent_kline(symbol="sh605060", beg=None, end=None):
    """腾讯日K线(前复权), volume单位=手；默认取最近300天"""
    if beg is None or end is None:
        end = datetime.date.today().strftime("%Y-%m-%d")
        beg = (datetime.date.today() - datetime.timedelta(days=300)).strftime("%Y-%m-%d")
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,{beg},{end},500,qfq"
    )
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    d = r.json()
    data = d["data"][symbol]
    klines = data.get("qfqday") or data.get("day") or []
    rows = []
    for p in klines:
        rows.append({
            "date": p[0],
            "open": float(p[1]),
            "close": float(p[2]),
            "high": float(p[3]),
            "low": float(p[4]),
            "volume": float(p[5]),   # 手
        })
    return rows


def fetch_tencent_quote(symbol="sh605060"):
    """腾讯实时行情(含换手率/量比/成交额/市值)"""
    r = requests.get(f"https://qt.gtimg.cn/q={symbol}",
                     headers=HEADERS, timeout=20)
    r.encoding = "gbk"
    txt = r.text.strip()
    f = txt.split("~")
    return {
        "name": f[1], "code": f[2],
        "price": float(f[3]), "yesterday_close": float(f[4]),
        "open": float(f[5]), "volume_hand": float(f[6]),      # 手
        "amount_wan": float(f[37]),                            # 万元
        "turnover_pct": float(f[38]),                          # 换手率%
        "pe_ttm": float(f[39]), "high": float(f[33]), "low": float(f[34]),
        "change_pct": float(f[32]),
        "amplitude_pct": float(f[43]),
        "total_mv_yi": float(f[45]),                           # 总市值(亿)
        "circ_mv_yi": float(f[44]),                            # 流通市值(亿)
        "pb": float(f[46]), "vol_ratio": float(f[49]),         # 量比
        "date_time": f[30],
    }


def main():
    quote = fetch_tencent_quote("sh605060")
    sina = fetch_sina_kline("sh605060")
    tencent = fetch_tencent_kline("sh605060")
    print("实时行情:", json.dumps(quote, ensure_ascii=False, indent=1))
    print(f"新浪K线: {len(sina)} 条  {sina[0]['date']} ~ {sina[-1]['date']}")
    print(f"腾讯K线: {len(tencent)} 条  {tencent[0]['date']} ~ {tencent[-1]['date']}")
    # 交叉验证最近一天成交量单位
    s_map = {x["date"]: x for x in sina}
    for row in tencent[-3:]:
        s = s_map.get(row["date"])
        if s:
            print(row["date"], "sina股:", s["volume"], "腾讯手:", row["volume"],
                  "比值:", round(s["volume"] / row["volume"] / 100, 3))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": datetime.datetime.now().isoformat(),
                   "quote": quote, "sina_kline": sina, "tencent_kline": tencent}, f,
                  ensure_ascii=False, indent=1)
    print("已保存:", OUT)


if __name__ == "__main__":
    main()
