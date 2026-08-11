# -*- coding: utf-8 -*-
"""
行情排行内置模块（备用数据源）
优先走新浪（稳定），失败自动切换东方财富多镜像；任何情况下都保证项目能出数据。
与技能版 get_hot_stocks 返回结构保持一致：
code, name, price, change_pct, amount(亿), turnover(%), pe, total_mv(亿), vol_ratio
"""
import json
import os
import re
import time

import requests

SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Referer": "https://finance.sina.com.cn",
}

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

# 东方财富多镜像（部分节点时好时坏，全部轮流尝试）
API_BASES = [
    "https://push2.eastmoney.com/api/qt",
    "https://1.push2.eastmoney.com/api/qt",
    "https://33.push2.eastmoney.com/api/qt",
    "https://48.push2.eastmoney.com/api/qt",
    "https://82.push2.eastmoney.com/api/qt",
    "https://push2delay.eastmoney.com/api/qt",
    "https://push2his.eastmoney.com/api/qt",
]

FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
FIELDS = "f2,f3,f5,f6,f8,f9,f10,f12,f14,f20,f21"
RANK_FIELD = {"volume": "f5", "amount": "f6", "turnover": "f8"}


def _num(v, allow_none=False):
    """接口未成交/停牌时返回 '-' 或空字符串，统一转成数字或 None"""
    if v is None or v == "-" or v == "":
        return None if allow_none else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return None if allow_none else 0.0


def get_hot_stocks(rank_type="volume", limit=100):
    """获取热门榜（新浪优先，东方财富兜底），rank_type: volume/amount/turnover"""
    last_err = None
    for fn in (_sina_hot_stocks, _eastmoney_hot_stocks):
        try:
            return fn(rank_type, limit)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"热门榜单抓取失败: {last_err}")


def save_picks_cache(path, picks):
    """把最近一次成功的热门股结果存到本地，供开盘前接口无数据时兜底"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": time.strftime("%Y-%m-%d %H:%M"), "picks": picks},
                      f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def load_picks_cache(path):
    """读取缓存；无缓存或格式错误返回 None"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        picks = d.get("picks") or []
        return picks if picks else None
    except Exception:
        return None


def _sina_hot_stocks(rank_type="volume", limit=100):
    """新浪行情排行（稳定，数据为实时行情）"""
    sort = {"volume": "volume", "amount": "amount", "turnover": "turnoverratio"}.get(
        rank_type, "volume")
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/"
           "api/json_v2.php/Market_Center.getHQNodeData")
    params = {"page": 1, "num": min(int(limit), 100),
              "sort": sort, "asc": 0, "node": "hs_a"}
    r = requests.get(url, params=params, headers=SINA_HEADERS, timeout=12)
    r.raise_for_status()
    # 新浪返回的是未加引号的键，先补引号再解析
    fixed = re.sub(r"([{,])(\w+):", r'\1"\2":', r.text.strip())
    items = json.loads(fixed) or []
    rows = []
    for d in items:
        try:
            price = _num(d.get("trade"))
            amount_yi = _num(d.get("amount")) / 1e8
            if price <= 0 or amount_yi <= 0:
                continue
            rows.append({
                "code": str(d.get("code") or ""),
                "name": str(d.get("name") or ""),
                "price": price,
                "change_pct": _num(d.get("changepercent")),
                "amount": round(amount_yi, 2),
                "turnover": _num(d.get("turnoverratio")),
                "pe": _num(d.get("per")),
                "total_mv": round(_num(d.get("mktcap")) / 1e4, 2),   # 新浪市值单位=万元 -> 亿
                "vol_ratio": None,
            })
        except Exception:
            continue
    if rows:
        return rows[:limit]
    raise RuntimeError("新浪榜单数据为空或无有效行情")


def _eastmoney_hot_stocks(rank_type="volume", limit=100):
    """东方财富热门榜（多镜像轮询，返回全 0 的数据视为无效并跳过）"""
    fid = RANK_FIELD.get(rank_type, "f5")
    params = {
        "pn": 1, "pz": min(int(limit), 100), "po": 1, "np": 1,
        "fltt": 2, "invt": 2, "fid": fid, "fs": FS, "fields": FIELDS,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    }
    last_err = None
    for base in API_BASES:
        try:
            r = requests.get(base + "/clist/get", params=params,
                             headers=EM_HEADERS, timeout=8)
            r.raise_for_status()
            data = (r.json() or {}).get("data") or {}
            diff = data.get("diff") or []
            rows = []
            for d in diff:
                try:
                    price = _num(d.get("f2"))
                    amount_yi = _num(d.get("f6")) / 1e8
                    # 过滤停牌/无行情数据（接口偶尔返回全 0 的延迟镜像数据）
                    if price <= 0 or amount_yi <= 0:
                        continue
                    rows.append({
                        "code": str(d.get("f12") or ""),
                        "name": str(d.get("f14") or ""),
                        "price": price,
                        "change_pct": _num(d.get("f3")),
                        "amount": round(amount_yi, 2),
                        "turnover": _num(d.get("f8")),
                        "pe": _num(d.get("f9")),
                        "total_mv": round(_num(d.get("f20")) / 1e8, 2),
                        "vol_ratio": _num(d.get("f10"), allow_none=True),
                    })
                except Exception:
                    continue
            if rows:
                return rows[:limit]
            last_err = RuntimeError(f"榜单数据为空或无有效行情: {base}")
        except Exception as e:
            last_err = e
        time.sleep(0.6)
    raise RuntimeError(f"东方财富榜单抓取失败: {last_err}")


if __name__ == "__main__":
    for rt in ("volume", "amount", "turnover"):
        try:
            rows = get_hot_stocks(rt, 5)
            print(f"[{rt}] 前5:", [(r["code"], r["name"], r["price"]) for r in rows])
        except Exception as e:
            print(f"[{rt}] 失败: {e}")
