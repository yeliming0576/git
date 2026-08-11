# -*- coding: utf-8 -*-
"""
选股系统 v2（热度 + 趋势框架优化版）
====================================
1. 热度评分：三榜活跃度指标对数化 + Z-score 标准化求和（替代单日排名线性分）
2. 市值中性化：成交额/流通市值、成交量/流通股本（消除大市值偏差）
3. 热度持续性：多日排名稳定性 + 上升趋势得分（数据缓存在 排名历史.json）
4. 过滤自适应：动态股价上限（市场中位数×倍数）、20日分位数门槛、绝对底线
5. 市场环境过滤：中证全指 收盘>MA20 且 MA20>MA60，否则降仓（只保留1只）
6. 行业分散：最终入选至少分属两个行业（新浪行业），并给出拥挤度预警

常用入口：
    import selection
    result = selection.pick_hot_stocks(3)
    result["picks"]  -> [{"code","name","price","change_pct","amount",
                          "turnover","pe","total_mv","score"}, ...]
    result["meta"]   -> {"market_ok","warnings","notes","from_cache","fetched_at"}
"""
import datetime
import json
import math
import os
import re
import statistics
import time

import requests

import eastmoney
import quant_engine as Q
import db

BASE = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE, "排名历史.json")
CACHE_FILE = os.path.join(BASE, "热门股缓存.json")

# ============ 可调参数（想改规则只改这里） ============
PRICE_MEDIAN_MULTIPLE = 2.5   # 股价上限 = 全市场股价中位数 × 倍数
PRICE_CAP_FLOOR = 12.0        # 股价上限绝对底限（元），与动态值取大
PRICE_FLOOR = 2.0             # 股价下限（元），过滤低价垃圾股
TURNOVER_FLOOR = 1.0          # 换手率绝对底线（%），低于直接淘汰
SHORTLIST_SIZE = 20           # 进入 20 日分位数检查的候选数量
PERCENTILE_LEVEL = 0.2        # 当日活跃度需高于过去 20 日的第 20 分位
PERCENTILE_RELAX = 0.1        # 候选不足时的放宽分位
HISTORY_DAYS = 5              # 多日持续性统计用最近 N 个交易日
MARKET_INDEX = "sh000985"     # 中证全指
RANK_TYPES = ("volume", "amount", "turnover")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Referer": "https://finance.sina.com.cn",
}


# ---------------- 数据抓取 ----------------
def _sina_get(url, params=None, timeout=15):
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


def _parse_sina_json(text):
    """新浪返回未加引号的键，统一补引号后解析"""
    fixed = re.sub(r"([{,])(\w+):", r'\1"\2":', text.strip())
    return json.loads(fixed)


def _sina_rank(rank_type, num=100):
    """新浪行情排行，返回标准化行"""
    sort = {"volume": "volume", "amount": "amount", "turnover": "turnoverratio"}.get(rank_type, "volume")
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/"
           "api/json_v2.php/Market_Center.getHQNodeData")
    rows = _parse_sina_json(_sina_get(url, {
        "page": 1, "num": num, "sort": sort, "asc": 0, "node": "hs_a"}))
    out = []
    for d in rows or []:
        try:
            price = float(d.get("trade") or 0)
            volume = float(d.get("volume") or 0)
            amount = float(d.get("amount") or 0)
            if price <= 0 or volume <= 0 or amount <= 0:
                continue
            nmc_yuan = float(d.get("nmc") or 0) * 1e4          # 新浪市值单位=万元
            mktcap_yuan = float(d.get("mktcap") or 0) * 1e4
            circ_shares = nmc_yuan / price if nmc_yuan > 0 else 0.0
            pe = float(d.get("per") or 0)
            out.append({
                "code": str(d.get("code") or ""),
                "name": str(d.get("name") or ""),
                "price": price,
                "change_pct": float(d.get("changepercent") or 0),
                "volume": volume,                                # 股
                "amount_yuan": amount,                           # 元
                "turnover": float(d.get("turnoverratio") or 0),  # %
                "pe": pe if pe > 0 else None,
                "nmc_yuan": nmc_yuan,                            # 流通市值(元)
                "mktcap_yuan": mktcap_yuan,                      # 总市值(元)
                "circ_shares": circ_shares,                      # 流通股本(股)
            })
        except Exception:
            continue
    return out


def fetch_rank_lists(num=100):
    """抓取三榜；新浪失败时自动用内置 eastmoney 兜底"""
    lists = {}
    for rt in RANK_TYPES:
        rows = []
        try:
            rows = _sina_rank(rt, num)
        except Exception:
            try:
                rows = eastmoney.get_hot_stocks(rt, num)
                rows = [{
                    "code": r["code"], "name": r["name"],
                    "price": r["price"], "change_pct": r["change_pct"],
                    "volume": 0.0, "amount_yuan": r["amount"] * 1e8,
                    "turnover": r["turnover"], "pe": r["pe"],
                    "nmc_yuan": r.get("total_mv", 0) * 1e8,
                    "mktcap_yuan": r.get("total_mv", 0) * 1e8,
                    "circ_shares": 0.0,
                } for r in rows if r.get("price", 0) > 0]
            except Exception:
                rows = []
        lists[rt] = rows
    return lists


def fetch_market_median_price(pages=5):
    """全市场股价中位数采样：按代码排序取前 N 页，共 pages×100 只"""
    prices = []
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/"
           "api/json_v2.php/Market_Center.getHQNodeData")
    for page in range(1, pages + 1):
        try:
            rows = _parse_sina_json(_sina_get(url, {
                "page": page, "num": 100, "sort": "symbol", "asc": 1, "node": "hs_a"}))
            prices += [float(r.get("trade") or 0) for r in rows if float(r.get("trade") or 0) > 0]
        except Exception:
            break
        time.sleep(0.3)
    return statistics.median(prices) if prices else None


def fetch_industry_map():
    """新浪行业映射：股票代码 -> 行业名（解析行业->成分股列表后反转）"""
    try:
        text = _sina_get("http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php")
        m = re.search(r"=\s*(\{.*\})", text, re.S)
        if not m:
            return None
        data = _parse_sina_json(m.group(1))
        result = {}
        for key, value in data.items():
            parts = str(value).split(",")
            if len(parts) < 2:
                continue
            industry = parts[1]
            for token in parts:
                if re.fullmatch(r"(sh|sz|bj)\d{6}", token):
                    result[token[2:]] = industry
        return result or None
    except Exception:
        return None


def fetch_stock_industry(code, fallback_map=None):
    """个股所属行业：东财 f127（申万二级）优先，新浪代表股映射兜底"""
    if fallback_map and code in fallback_map:
        return fallback_map[code]
    secid = ("1." if code.startswith(("6", "9")) else "0.") + code
    try:
        r = requests.get("https://push2.eastmoney.com/api/qt/stock/get",
                         params={"secid": secid, "fields": "f127"},
                         headers=HEADERS, timeout=8)
        r.raise_for_status()
        d = r.json()
        v = (d.get("data") or {}).get("f127")
        if v:
            return str(v).strip()
    except Exception:
        pass
    return None


def fetch_index_kline(symbol=MARKET_INDEX, datalen=120):
    """腾讯指数日K（新浪指数接口会返回 2016 年旧数据，改用腾讯）"""
    end = datetime.date.today().strftime("%Y-%m-%d")
    beg = (datetime.date.today() - datetime.timedelta(days=int(datalen * 1.4) + 30)).strftime("%Y-%m-%d")
    r = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                     params={"param": f"{symbol},day,{beg},{end},{datalen},qfq"},
                     headers=HEADERS, timeout=15)
    r.raise_for_status()
    d = r.json()
    data = (d.get("data") or {}).get(symbol) or {}
    kl = data.get("qfqday") or data.get("day") or []
    if not kl:
        raise RuntimeError("指数K线为空")
    return [{"date": p[0], "close": float(p[2])} for p in kl]


# ---------------- 热度评分 ----------------
def _log1p(v):
    return math.log1p(v) if v > 0 else 0.0


def _z_scores(values):
    vals = [v for v in values.values() if v and v > 0]
    if len(vals) < 3:
        return {k: 0.0 for k in values}
    mean = statistics.mean(vals)
    sd = statistics.pstdev(vals)
    if sd <= 0:
        return {k: 0.0 for k in values}
    return {k: (v - mean) / sd if v > 0 else 0.0 for k, v in values.items()}


def compute_activity_scores(lists):
    """三榜活跃度：对数化 + 榜内 Z-score 求和；同时做市值中性化"""
    metrics = {}
    info = {}
    for rt in RANK_TYPES:
        rows = lists.get(rt) or []
        m = {}
        for r in rows:
            code = r["code"]
            if not code:
                continue
            if rt == "volume":
                shares = r.get("circ_shares") or (r.get("nmc_yuan", 0) / max(r["price"], 0.01))
                val = _log1p(r["volume"] / shares) if shares > 0 else 0.0
            elif rt == "amount":
                val = _log1p(r["amount_yuan"] / r["nmc_yuan"]) if r.get("nmc_yuan", 0) > 0 else 0.0
            else:
                val = _log1p(r["turnover"])
            m[code] = val
            info.setdefault(code, r)
        z = _z_scores(m)
        for code, zz in z.items():
            metrics[code] = metrics.get(code, 0.0) + zz
    return metrics, info


# ---------------- 多日持续性评分 ----------------
def _load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_history(history):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def multi_day_scores(z_today):
    """多日得分 = 0.5×今日 + 0.3×稳定性 + 0.2×上升趋势（组件均做Z-score）"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    try:
        db.init_db()
        db.save_rank_history(z_today)
        history = db.load_rank_history(HISTORY_DAYS * 2)
        if today not in history:
            history[today] = z_today
    except Exception:
        history = db.load_rank_history_json(HISTORY_DAYS * 2)
        history[today] = z_today
        db.save_rank_history_json(history)
    dates = sorted(history)[-HISTORY_DAYS:]
    codes = set()
    for d in dates:
        codes.update(history.get(d, {}))
    if len(dates) <= 1:
        return dict(z_today)
    stability, trend = {}, {}
    for c in codes:
        series = [history[d].get(c, 0.0) for d in dates]
        stability[c] = 1.0 / (1.0 + statistics.pstdev(series))
        n = len(series)
        x = list(range(n))
        xm = sum(x) / n
        ym = sum(series) / n
        trend[c] = sum((x[i] - xm) * (series[i] - ym) for i in range(n)) / \
            (sum((x[i] - xm) ** 2 for i in range(n)) or 1.0)
    sz = _z_scores(stability)
    tz = _z_scores(trend)
    final = {}
    for c in codes:
        final[c] = 0.5 * z_today.get(c, 0.0) + 0.3 * sz.get(c, 0.0) + 0.2 * tz.get(c, 0.0)
    return final


# ---------------- 过滤与筛选 ----------------
def _basic_filters(codes, scores, info, price_cap):
    out = []
    for c in codes:
        r = info.get(c)
        if not r:
            continue
        name = r.get("name", "")
        price = r.get("price", 0)
        if price < PRICE_FLOOR or price > price_cap:
            continue
        if name.startswith(("N", "C", "ST", "*ST", "退")):
            continue
        if r.get("pe") is not None and r.get("pe") <= 0:
            continue
        if r.get("turnover", 0) < TURNOVER_FLOOR:
            continue
        out.append(c)
    return sorted(out, key=lambda c: scores.get(c, -999), reverse=True)


def _percentile_filter(codes, info, level=PERCENTILE_LEVEL):
    """过去20日换手率分位数门槛（成交额/流通市值与换手率同源，一次计算覆盖两项）"""
    kept = []
    for c in codes:
        r = info.get(c)
        if not r:
            continue
        shares = r.get("circ_shares") or (r.get("nmc_yuan", 0) / max(r["price"], 0.01))
        if shares <= 0:
            kept.append(c)          # 无股本数据则保留，避免误杀
            continue
        try:
            rows = Q.fetch_kline(c, datalen=60)
        except Exception:
            kept.append(c)          # K线抓取失败则保留，避免误杀
            continue
        vols = [row["volume"] for row in rows[-21:]]
        if len(vols) < 21:
            kept.append(c)
            continue
        turns = [v / shares * 100 for v in vols]
        today_t, past = turns[-1], sorted(turns[:-1])
        threshold = past[min(int(len(past) * level), len(past) - 1)]
        if today_t > threshold:
            kept.append(c)
    return kept


def _market_regime():
    """中证全指 MA20/MA60 + 全市场宽度；指数偏弱才降仓，
    指数正常但宽度偏弱时降为“观察”（不强制只剩1只），避免长期空转。"""
    try:
        rows = fetch_index_kline(datalen=120)
        closes = [r["close"] for r in rows]
        ma20 = Q.sma(closes, 20)
        ma60 = Q.sma(closes, 60)
        last = closes[-1]
        index_ok = last > ma20[-1] and ma20[-1] > ma60[-1]
        note = (f"中证全指 {rows[-1]['date']} 收{last:.0f}，"
                f"MA20={ma20[-1]:.0f}，MA60={ma60[-1]:.0f}")
    except Exception as e:
        index_ok, note = None, f"指数数据暂不可用（{e}）"
    breadth_ok = None
    try:
        import market_snapshot
        b = market_snapshot.breadth()
        if b and b.get("advancers_ratio") is not None:
            breadth_ok = b["advancers_ratio"] >= 0.5
            note += (f"；市场宽度：涨跌家数比 {b['advancers_ratio']:.0%}，"
                     f"涨幅中位数 {b['median_change']:+.2f}%")
    except Exception:
        pass
    if index_ok is False:
        return False, note + " → 环境偏弱（指数未站上MA20/MA60），降仓：仅保留 1 只观察"
    if index_ok is True and breadth_ok is False:
        return None, note + " → 指数正常但宽度偏弱，维持观察（不强制降仓至1只）"
    return index_ok, note + (" → 环境正常" if index_ok is True else " → 环境数据不足，按正常处理")


def _select_diverse(codes, limit, industry_map, shortlist, meta):
    """行业分散：尽量保证入选股票至少分属两个行业；同行业拥挤度>40%预警"""
    if not industry_map:
        meta["warnings"].append("行业数据暂不可用，未启用行业分散约束")
        return codes[:limit]
    counts = {}
    known = 0
    for c in shortlist:
        ind = industry_map.get(c)
        if ind:
            known += 1
            counts[ind] = counts.get(ind, 0) + 1
    if known < max(2, len(shortlist) // 2):
        meta["warnings"].append("行业数据覆盖不足，分散约束为尽力执行")
    selected, backup = [], []
    for c in codes:
        if len(selected) >= limit:
            break
        ind = industry_map.get(c) or "未分类"
        used = {industry_map.get(s) or "未分类" for s in selected}
        if ind and ind in used:
            backup.append(c)
            continue
        selected.append(c)
    for c in backup:
        if len(selected) >= limit:
            break
        selected.append(c)
    # 拥挤度预警
    for c in selected:
        ind = industry_map.get(c)
        share = counts.get(ind, 0) / max(len(shortlist), 1)
        if share > 0.4:
            meta["warnings"].append(f"{ind}板块在候选中占比{share:.0%}，拥挤度偏高，注意风险")
    return selected


# ---------------- 主入口 ----------------
def pick_hot_stocks(limit=3):
    """完整选股流程，返回 {"picks": [...], "meta": {...}}"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    meta = {"market_ok": None, "warnings": [], "notes": [],
            "from_cache": False, "fetched_at": now}
    try:
        db.init_db()
    except Exception:
        pass
    try:
        lists = fetch_rank_lists()
        if not any(lists.get(rt) for rt in RANK_TYPES):
            raise RuntimeError("三榜数据均为空")
    except Exception as e:
        meta["notes"].append(f"行情接口暂不可用（{e}）")
        try:
            cached = db.load_latest_picks()
        except Exception:
            cached = db.load_latest_picks_json()
        if cached:
            meta["from_cache"] = True
            meta["warnings"].append("热门股为最近一次成功数据（开盘前接口常无数据，盘中自动恢复）")
            return {"picks": cached, "meta": meta}
        return {"picks": [], "meta": meta}

    z_scores, info = compute_activity_scores(lists)
    scores = multi_day_scores(z_scores)

    median = fetch_market_median_price()
    if median:
        price_cap = max(median * PRICE_MEDIAN_MULTIPLE, PRICE_CAP_FLOOR)
        meta["notes"].append(f"全市场股价中位数 {median:.2f} 元，股价上限 {price_cap:.2f} 元")
    else:
        price_cap = 30.0
        meta["notes"].append("市场股价采样失败，股价上限使用默认 30 元")

    shortlist = _basic_filters(list(scores.keys()), scores, info, price_cap)[:SHORTLIST_SIZE]
    if not shortlist:
        meta["warnings"].append("过滤后无候选，使用全部股票按热度排序")
        shortlist = sorted(scores, key=scores.get, reverse=True)[:SHORTLIST_SIZE]

    kept = _percentile_filter(shortlist, info)
    if len(kept) < limit:
        kept2 = _percentile_filter(shortlist, info, level=PERCENTILE_RELAX)
        if len(kept2) >= len(kept):
            kept = kept2
        if len(kept) < limit:
            kept = shortlist          # 全部放宽
            meta["notes"].append("20日分位数过滤放宽（当前处于开盘前或数据不足）")
    kept = sorted(kept, key=lambda c: scores.get(c, -999), reverse=True)

    market_ok, market_note = _market_regime()
    meta["market_ok"] = market_ok
    meta["notes"].append(market_note)
    effective_limit = 1 if market_ok is False else limit
    if market_ok is False:
        meta["warnings"].append("市场环境偏弱（中证全指未站上MA20/MA60），降仓：仅保留 1 只观察")

    fallback_map = fetch_industry_map()
    ind_of = {}
    for c in kept:
        ind_of[c] = fetch_stock_industry(c, fallback_map)
        time.sleep(0.1)
    selected = _select_diverse(kept, effective_limit, ind_of, kept, meta)

    picks = []
    for c in selected:
        r = info.get(c) or {}
        picks.append({
            "code": c,
            "name": r.get("name", ""),
            "price": r.get("price", 0),
            "change_pct": r.get("change_pct", 0),
            "amount": round(r.get("amount_yuan", 0) / 1e8, 2),
            "turnover": r.get("turnover", 0),
            "pe": r.get("pe"),
            "total_mv": round(r.get("mktcap_yuan", 0) / 1e8, 2),
            "score": round(scores.get(c, 0) * 10 + 50),
        })
    if picks:
        try:
            db.save_picks(picks)
        except Exception:
            pass
        eastmoney.save_picks_cache(CACHE_FILE, picks)   # 保留JSON备份
    try:
        db.save_selection_run(datetime.date.today().strftime("%Y-%m-%d"),
                              len(picks), meta["market_ok"])
        empty_n = db.consecutive_empty_runs(10)
        if len(picks) == 0 and empty_n >= 5:
            meta["warnings"].append(
                f"已连续 {empty_n} 个运行日零选股，建议检查市场过滤是否过严或模型失效")
    except Exception:
        pass
    return {"picks": picks, "meta": meta}


def build_universe(max_n=60, extra_codes=None):
    """L0 股票池：优先全市场截面（成交额+换手 top N，P1 扩大池），
    失败时回退三榜前 max_n + 自选/持仓/固定关注。"""
    try:
        import market_snapshot
        codes = market_snapshot.top_universe(max_n, extra_codes)
        if codes:
            return codes
    except Exception:
        pass
    try:
        lists = fetch_rank_lists(120)
    except Exception:
        lists = {}
    codes, seen = [], set()
    for rt in RANK_TYPES:
        for r in lists.get(rt) or []:
            c = r.get("code") or ""
            if c and c not in seen:
                seen.add(c)
                codes.append(c)
    for c in extra_codes or []:
        if c and c not in seen:
            seen.add(c)
            codes.append(c)
    return codes[:max_n]


def heat_exclude(codes, rows_map):
    """L4 热度负向剔除（热度=风险出口）：
    - 5日累计涨幅百分位 > 95 → 剔除（注意力买入的接盘位置）
    - 换手/量能百分位 > 95 → 剔除（rows 含 turnover 用换手，否则用量比代理）
    - 近3日涨停 ≥ 2 次 → 剔除
    返回应剔除的 code 集合。"""
    ret5, turn, lim3 = {}, {}, {}
    for c in codes:
        rows = rows_map.get(c) or []
        if len(rows) < 6:
            continue
        closes = [r["close"] for r in rows]
        prev5 = closes[-6]
        ret5[c] = closes[-1] / prev5 - 1 if prev5 else 0.0
        if rows[-1].get("turnover"):
            turn[c] = rows[-1]["turnover"]
        else:
            vols = [r["volume"] for r in rows[-60:]]
            base = sum(vols) / len(vols) if vols else 1
            turn[c] = rows[-1]["volume"] / base if base else 1.0
        cnt = 0
        for i in range(max(1, len(rows) - 3), len(rows)):
            prev = rows[i - 1]["close"]
            pct = rows[i]["close"] / prev - 1 if prev else 0
            if pct >= 0.095:
                cnt += 1
        lim3[c] = cnt

    out = set()
    for key, thr_name in ((ret5, "涨幅"), (turn, "换手")):
        vals = sorted(key.values())
        if len(vals) >= 10:
            th95 = vals[min(int(len(vals) * 0.95), len(vals) - 1)]
            out |= {c for c, v in key.items() if v > th95}
    out |= {c for c, n in lim3.items() if n >= 2}
    return out


if __name__ == "__main__":
    result = pick_hot_stocks(3)
    print("meta:", result["meta"])
    for p in result["picks"]:
        print(p)
