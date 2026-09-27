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

低值复苏选股（网页 /lowval 专用，见文件底部 LOWVAL_ 段落）：
    selection.pick_lowval("科技,医药")   # 换手3~8% + PE低于板块中位数 + 业绩同比双正且改善
"""
import datetime
import json
import math
import os
import re
import statistics
import time

import eastmoney
import quant_engine as Q
import db
import datafeed
import config as C

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "数据")
HISTORY_FILE = os.path.join(DATA_DIR, "排名历史.json")
CACHE_FILE = os.path.join(DATA_DIR, "热门股缓存.json")

# ============ 可调参数（默认值集中在 config.py，可用 config.json 覆盖） ============
PRICE_MEDIAN_MULTIPLE = C.PRICE_MEDIAN_MULTIPLE
PRICE_CAP_FLOOR = C.PRICE_CAP_FLOOR
PRICE_FLOOR = C.PRICE_FLOOR
TURNOVER_FLOOR = C.TURNOVER_FLOOR
SHORTLIST_SIZE = C.SHORTLIST_SIZE
PERCENTILE_LEVEL = C.PERCENTILE_LEVEL
PERCENTILE_RELAX = C.PERCENTILE_RELAX
HISTORY_DAYS = C.HISTORY_DAYS
MARKET_INDEX = C.MARKET_INDEX
RANK_TYPES = ("volume", "amount", "turnover")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Referer": "https://finance.sina.com.cn",
}


# ---------------- 数据抓取 ----------------
def _sina_get(url, params=None, timeout=15):
    """新浪取数（统一重试/退避/限流）"""
    return datafeed.get_text(url, params=params, headers=HEADERS, timeout=timeout)


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
    """抓取三榜；新浪失败用内置 eastmoney，再失败用 akshare 快照兜底"""
    lists = {}
    for rt in RANK_TYPES:
        def _eastmoney_rows(_rt=rt):
            rows = eastmoney.get_hot_stocks(_rt, num)
            return [{
                "code": r["code"], "name": r["name"],
                "price": r["price"], "change_pct": r["change_pct"],
                "volume": 0.0, "amount_yuan": r["amount"] * 1e8,
                "turnover": r["turnover"], "pe": r["pe"],
                "nmc_yuan": r.get("total_mv", 0) * 1e8,
                "mktcap_yuan": r.get("total_mv", 0) * 1e8,
                "circ_shares": 0.0,
            } for r in rows if r.get("price", 0) > 0]

        try:
            rows, _src = datafeed.fallback(f"{rt}榜", [
                ("新浪", lambda _rt=rt: _sina_rank(_rt, num)),
                ("东财", _eastmoney_rows),
                ("akshare", lambda _rt=rt: datafeed.ak_rank(_rt, num)),
            ])
        except Exception as e:
            rows = []
            print(f"[选股] {rt} 榜抓取失败: {e}")
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
        d = datafeed.get_json("https://push2.eastmoney.com/api/qt/stock/get",
                              params={"secid": secid, "fields": "f127"},
                              headers=HEADERS, timeout=8, retries=2)
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
    d = datafeed.get_json("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                          params={"param": f"{symbol},day,{beg},{end},{datalen},qfq"},
                          headers=HEADERS, timeout=15)
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


# ==================================================================
# 低值复苏选股（网页单页 /lowval 专用，与上面的热度选股相互独立）
#   输入板块（科技 / 农业 / 医药 …）→ 输出 5 只同时满足：
#     换手 3%~8% ｜ PE 低于所属板块内中位数 ｜ 最新报告期营收与净利同比双正且较上期改善
#   附带：大盘环境提示、热度剔除（5日暴涨/连续涨停）、分级放宽留痕、当日缓存
# ==================================================================
LOWVAL_TURNOVER_MIN = C.LOWVAL_TURNOVER_MIN
LOWVAL_TURNOVER_MAX = C.LOWVAL_TURNOVER_MAX
LOWVAL_TURNOVER_RELAX = C.LOWVAL_TURNOVER_RELAX
LOWVAL_SHORTLIST_SIZE = C.LOWVAL_SHORTLIST_SIZE
LOWVAL_SHORTLIST_MAX = C.LOWVAL_SHORTLIST_MAX
LOWVAL_LIMIT = C.LOWVAL_LIMIT
LOWVAL_W_RECOVERY = C.LOWVAL_W_RECOVERY
LOWVAL_W_VALUE = C.LOWVAL_W_VALUE
LOWVAL_W_TURNOVER = C.LOWVAL_W_TURNOVER
LOWVAL_PE_MIN_SAMPLE = C.LOWVAL_PE_MIN_SAMPLE
LOWVAL_BOARD_PAGES = C.LOWVAL_BOARD_PAGES
LOWVAL_HEAT_RET5_CAP = C.LOWVAL_HEAT_RET5_CAP
LOWVAL_HEAT_LIMIT_DAYS = C.LOWVAL_HEAT_LIMIT_DAYS
LOWVAL_BOARD_CACHE_DAYS = C.LOWVAL_BOARD_CACHE_DAYS
LOWVAL_TIMEOUT = C.LOWVAL_TIMEOUT
LOWVAL_MARKET_FS = C.LOWVAL_MARKET_FS
LOWVAL_MARKET_PAGES = C.LOWVAL_MARKET_PAGES
LOWVAL_FIELDS = C.LOWVAL_FIELDS
LOWVAL_HOSTS = list(C.LOWVAL_HOSTS)
LOWVAL_BOARD_MAP_FILE = os.path.join(DATA_DIR, "板块映射.json")
LOWVAL_FIN_CACHE_FILE = os.path.join(DATA_DIR, "低值复苏_财务缓存.json")
LOWVAL_POOL_CACHE_FILE = os.path.join(DATA_DIR, "低值复苏_池缓存.json")

LOWVAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "Chrome/126 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
_LV_HOST_OK = {"host": None}            # 记住可用的东财镜像节点

# 东财行业板块代码表（实测自东财行业板块 m:90+t:2）
LOWVAL_BOARD_CODES = {
    # 电子 / 计算机 / 通信
    "电子": "BK1201", "半导体": "BK1036", "消费电子": "BK1037", "光学光电子": "BK1038",
    "元件": "BK0459", "电子化学品Ⅱ": "BK1039", "其他电子Ⅱ": "BK1223",
    "半导体材料": "BK1325", "半导体设备": "BK1326", "数字芯片设计": "BK1331",
    "集成电路制造": "BK1329", "集成电路封测": "BK1328", "被动元件": "BK1339",
    "印制电路板": "BK1340", "面板": "BK1335", "LED": "BK1333",
    "计算机": "BK1207", "计算机设备": "BK0735", "软件开发": "BK0737", "IT服务Ⅱ": "BK1238",
    "通信": "BK1215", "通信设备": "BK0448", "通信服务": "BK0736",
    "通信网络设备及器件": "BK1591",
    "传媒": "BK0486", "游戏Ⅱ": "BK1046", "广告营销": "BK1220",
    # 农林牧渔
    "农林牧渔": "BK0433", "养殖业": "BK1259", "种植业": "BK1261", "饲料": "BK1258",
    "农产品加工": "BK1256", "渔业": "BK1260", "林业Ⅱ": "BK1255", "农业综合Ⅱ": "BK1257",
    "动物保健Ⅱ": "BK1254", "农化制品": "BK0731", "农用机械": "BK1404",
    "生猪养殖": "BK1512", "肉鸡养殖": "BK1511", "种子": "BK1518", "复合肥": "BK1433",
    # 医药
    "医药生物": "BK1216", "化学制药": "BK0465", "中药Ⅱ": "BK1040", "生物制品": "BK1044",
    "医疗器械": "BK1041", "医疗服务": "BK0727", "医药商业": "BK1042",
    "原料药": "BK1595", "血液制品": "BK1597", "疫苗": "BK1598", "体外诊断": "BK1603",
    # 消费
    "食品饮料": "BK0438", "家用电器": "BK0456", "美容护理": "BK1035", "纺织服饰": "BK0436",
    "商贸零售": "BK1213", "社会服务": "BK1214", "白酒Ⅱ": "BK1277", "调味发酵品Ⅱ": "BK1278",
    "零食": "BK1583", "肉制品": "BK1580", "酒店餐饮": "BK1271",
    # 新能源 / 电力
    "电力设备": "BK1200", "光伏设备": "BK1031", "风电设备": "BK1032", "电池": "BK1033",
    "锂电池": "BK1303", "逆变器": "BK1320", "电网设备": "BK0457", "电力": "BK0428",
    # 金融 / 地产 / 周期
    "银行Ⅱ": "BK0475", "非银金融": "BK1203", "证券Ⅱ": "BK0473", "保险Ⅱ": "BK0474",
    "房地产": "BK1202", "钢铁": "BK0479", "煤炭": "BK0437", "有色金属": "BK0478",
    "基础化工": "BK1206", "化学制品": "BK0538", "石油石化": "BK0464",
    "机械设备": "BK1205", "通用设备": "BK0545", "专用设备": "BK0910", "机器人": "BK1408",
    "国防军工": "BK1204", "汽车": "BK1211", "乘用车": "BK1262", "汽车零部件": "BK0481",
    "建筑装饰": "BK1209", "建筑材料": "BK1208", "环保": "BK0728", "公用事业": "BK0427",
}

# 关键词 → 东财行业板块（输入"科技"即展开；多个关键词取并集）
LOWVAL_SECTOR_ALIAS = {
    "科技": ["半导体", "消费电子", "光学光电子", "元件", "电子化学品Ⅱ",
             "计算机设备", "软件开发", "IT服务Ⅱ", "通信设备", "通信服务"],
    "农业": ["养殖业", "种植业", "饲料", "农产品加工", "渔业", "林业Ⅱ",
             "农业综合Ⅱ", "动物保健Ⅱ", "农化制品", "农用机械"],
    "医药": ["化学制药", "中药Ⅱ", "生物制品", "医疗器械", "医疗服务", "医药商业"],
    "消费": ["食品饮料", "家用电器", "美容护理", "纺织服饰", "商贸零售", "社会服务"],
    "新能源": ["光伏设备", "风电设备", "电池", "锂电池", "逆变器"],
    "电力设备": ["电力设备", "电网设备", "电力"],
    "金融": ["银行Ⅱ", "非银金融", "证券Ⅱ", "保险Ⅱ"],
    "军工": ["国防军工"],
    "汽车": ["汽车", "乘用车", "汽车零部件"],
    "化工": ["基础化工", "化学制品", "农化制品"],
    "有色": ["有色金属"],
    "机械": ["机械设备", "通用设备", "专用设备", "机器人"],
    "传媒": ["传媒", "游戏Ⅱ", "广告营销"],
    "地产": ["房地产"],
    "半导体": ["半导体", "半导体材料", "半导体设备", "数字芯片设计",
               "集成电路制造", "集成电路封测"],
    "AI算力": ["半导体", "半导体设备", "数字芯片设计", "通信网络设备及器件",
               "IT服务Ⅱ", "软件开发"],
    "白酒": ["白酒Ⅱ"],
    "中药": ["中药Ⅱ"],
}
LOWVAL_PRESET_SECTORS = ["科技", "农业", "医药", "消费", "新能源", "金融", "军工",
                         "汽车", "化工", "有色", "机械", "半导体"]


# ---------------- 低值复苏：基础工具 ----------------
def _lv_num(v):
    try:
        if v in (None, "-", ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _lv_norm_board(name):
    """去掉板块名的 Ⅱ/Ⅲ 后缀，便于输入"中药"命中"中药Ⅱ" """
    return re.sub(r"[ⅡⅢ]+$", "", str(name or "").strip()).strip()


def _lv_load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _lv_save_json(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _lv_today():
    return datetime.date.today().strftime("%Y-%m-%d")


def _lv_em_get(path, params, timeout=LOWVAL_TIMEOUT):
    """东财请求：轮换镜像节点并记住可用节点；全部失败抛异常"""
    hosts = ([_LV_HOST_OK["host"]] if _LV_HOST_OK["host"] else []) + \
            [h for h in LOWVAL_HOSTS if h != _LV_HOST_OK["host"]]
    last_err = None
    for host in hosts:
        for _ in range(2):
            try:
                data = datafeed.get_json(f"https://{host}{path}", params=params,
                                         headers=LOWVAL_HEADERS, timeout=timeout,
                                         retries=1, min_interval=0.1)
                if data:
                    _LV_HOST_OK["host"] = host
                    return data
            except Exception as e:
                last_err = e
            time.sleep(0.3)
    raise RuntimeError(f"东财接口不可用: {last_err}")


def _lv_clist(fs, pages=1, fields=LOWVAL_FIELDS, fid="f6"):
    """拉取东财榜单（板块成分股 / 全市场截面），按 fid 降序取前 pages 页"""
    rows = []
    for pn in range(1, max(1, int(pages)) + 1):
        data = _lv_em_get("/api/qt/clist/get", {
            "pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": fid, "fs": fs, "fields": fields,
        })
        diff = ((data or {}).get("data") or {}).get("diff") or []
        if not diff:
            break
        rows.extend(diff)
        if len(diff) < 100:
            break
        time.sleep(0.15)
    return rows


def lowval_load_board_map(force=False):
    """东财行业板块表 {板块名: BK代码}，缓存若干天；失败返回上次缓存"""
    cached = _lv_load_json(LOWVAL_BOARD_MAP_FILE) or {}
    fetched = cached.get("fetched_at") or ""
    if not force and cached.get("boards") and fetched:
        try:
            age = (datetime.date.today() -
                   datetime.datetime.strptime(fetched, "%Y-%m-%d").date()).days
            if age <= LOWVAL_BOARD_CACHE_DAYS:
                return cached["boards"]
        except Exception:
            pass
    boards = {}
    try:
        for pn in range(1, 7):
            data = _lv_em_get("/api/qt/clist/get", {
                "pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f3", "fs": "m:90+t:2", "fields": "f12,f14",
            })
            d = (data or {}).get("data") or {}
            diff = d.get("diff") or []
            if not diff:
                break
            for r in diff:
                if r.get("f14") and r.get("f12"):
                    boards[str(r["f14"]).strip()] = str(r["f12"]).strip()
            if len(boards) >= (d.get("total") or 0):
                break
            time.sleep(0.2)
    except Exception:
        return cached.get("boards") or {}
    if boards:
        _lv_save_json(LOWVAL_BOARD_MAP_FILE, {"fetched_at": _lv_today(),
                                              "boards": boards})
    return boards


def lowval_available_sectors():
    """网页上可展示的板块关键词"""
    return list(LOWVAL_SECTOR_ALIAS.keys())


def lowval_resolve_sectors(text):
    """解析输入 → {"boards":[(板块名,代码)], "unknown":[词], "kws":[...]}"""
    kws = [k.strip() for k in re.split(r"[,，、;；\s/|]+", text or "") if k.strip()]
    boards, seen, unknown = [], set(), []
    board_map = None
    for kw in kws:
        if kw in LOWVAL_SECTOR_ALIAS:
            hit_names = list(LOWVAL_SECTOR_ALIAS[kw])
        elif kw in LOWVAL_BOARD_CODES:
            hit_names = [kw]
        else:
            if board_map is None:
                board_map = lowval_load_board_map()
            pool = dict(LOWVAL_BOARD_CODES)
            pool.update(board_map)
            norm_kw = _lv_norm_board(kw)
            exact = [n for n in pool if _lv_norm_board(n) == norm_kw]
            fuzzy = [n for n in pool if norm_kw and norm_kw in _lv_norm_board(n)]
            hit_names = exact or fuzzy[:6]
        if not hit_names:
            unknown.append(kw)
            continue
        if board_map is None:
            board_map = {}
        for name in hit_names:
            code = LOWVAL_BOARD_CODES.get(name) or board_map.get(name)
            if not code or code in seen:
                continue
            seen.add(code)
            boards.append((name, code))
    return {"boards": boards, "unknown": unknown, "kws": kws}


# ---------------- 低值复苏：股票池 ----------------
def _lv_parse_em_rows(raw, source="东财板块"):
    """东财榜单行 → 统一结构（含所属行业 f100）"""
    out = []
    for r in raw:
        code = str(r.get("f12") or "").strip()
        price = _lv_num(r.get("f2"))
        if not code or not price or price <= 0:
            continue
        out.append({
            "code": code,
            "name": str(r.get("f14") or "").strip(),
            "price": price,
            "change_pct": _lv_num(r.get("f3")) or 0.0,
            "amount_yi": round((_lv_num(r.get("f6")) or 0.0) / 1e8, 2),
            "turnover": _lv_num(r.get("f8")),
            "pe": _lv_num(r.get("f9")),
            "pb": _lv_num(r.get("f23")),
            "total_mv": round((_lv_num(r.get("f20")) or 0.0) / 1e8, 2),
            "industry": str(r.get("f100") or "").strip(),
            "source": source,
        })
    return out


def _lv_board_rows(board_code, pages=LOWVAL_BOARD_PAGES, force=False):
    """板块成分股（含换手/PE/PB/市值），按成交额降序；当日缓存"""
    cache = _lv_load_json(LOWVAL_POOL_CACHE_FILE) or {}
    entry = (cache.get("boards") or {}).get(board_code) or {}
    if not force and entry.get("fetched_at") == _lv_today() and entry.get("rows"):
        return entry["rows"]
    try:
        raw = _lv_clist(f"b:{board_code}", pages=pages)
    except Exception:
        return entry.get("rows") or []
    out = _lv_parse_em_rows(raw, source="东财板块")
    if out:
        cache.setdefault("boards", {})[board_code] = {
            "fetched_at": _lv_today(), "rows": out}
        _lv_save_json(LOWVAL_POOL_CACHE_FILE, cache)
    return out


def _lv_market_rows(force=False):
    """全市场活跃截面（成交额 top600）：东财直取（带所属行业），失败退回 market_snapshot"""
    cache = _lv_load_json(LOWVAL_POOL_CACHE_FILE) or {}
    entry = cache.get("market") or {}
    if not force and entry.get("fetched_at") == _lv_today() and entry.get("rows"):
        return entry["rows"]
    out = []
    try:
        out = _lv_parse_em_rows(_lv_clist(LOWVAL_MARKET_FS, pages=LOWVAL_MARKET_PAGES,
                                          fid="f6"), source="全市场活跃池")
    except Exception:
        out = []
    if not out:
        try:
            import market_snapshot
            rows = ((market_snapshot.get_snapshot() or {}).get("rows")) or []
        except Exception:
            rows = []
        for r in rows:
            price = _lv_num(r.get("price"))
            if not price or price <= 0:
                continue
            out.append({
                "code": r.get("code"),
                "name": str(r.get("name") or "").strip(),
                "price": price,
                "change_pct": _lv_num(r.get("change_pct")) or 0.0,
                "amount_yi": round((_lv_num(r.get("amount")) or 0.0) / 1e8, 2),
                "turnover": _lv_num(r.get("turnover")),
                "pe": _lv_num(r.get("pe")),
                "pb": _lv_num(r.get("pb")),
                "total_mv": round((_lv_num(r.get("total_mv")) or 0.0) / 1e8, 2),
                "industry": "",
                "source": "全市场活跃池(market_snapshot)",
            })
    if out:
        cache["market"] = {"fetched_at": _lv_today(), "rows": out}
        _lv_save_json(LOWVAL_POOL_CACHE_FILE, cache)
    return out


def _lv_keyword_rows(keywords, rows):
    """板块名未命中时的关键词兜底：先匹配东财所属行业，再查新浪行业映射"""
    if not keywords:
        return []
    out = [dict(r) for r in rows
           if r.get("industry") and any(kw in r["industry"] for kw in keywords)]
    if out:
        return out
    try:
        ind_map = fetch_industry_map() or {}
    except Exception:
        return []
    by_code = {r["code"]: r for r in rows}
    for code, industry in ind_map.items():
        if industry and any(kw in industry for kw in keywords) and code in by_code:
            row = dict(by_code[code])
            row["industry"] = row.get("industry") or industry
            out.append(row)
    return out


def lowval_build_pool(sectors_text, meta, force=False):
    """候选池：东财板块成分股 / 关键词兜底 / 全市场活跃池"""
    resolved = lowval_resolve_sectors(sectors_text)
    meta["sectors_input"] = resolved["kws"]
    meta["boards"] = [{"name": n, "code": c} for n, c in resolved["boards"]]
    meta["unknown_sectors"] = resolved["unknown"]
    pool, sector_of = [], {}

    for name, code in resolved["boards"]:
        rows = _lv_board_rows(code, force=force)
        if not rows:
            meta["warnings"].append(f"板块「{name}」成分股获取失败，已跳过")
            continue
        for r in rows:
            sector_of.setdefault(r["code"], name)
        pool.extend(rows)
        time.sleep(0.2)

    if not pool:
        market_rows = _lv_market_rows(force)
        if sectors_text.strip():
            kws = resolved["unknown"] or resolved["kws"]
            fallback = _lv_keyword_rows(kws, market_rows)
            meta["degraded"] = True
            if fallback:
                pool = fallback
                meta["warnings"].append("东财板块未命中，已改用所属行业关键词匹配（降级）")
                meta["unknown_sectors"] = []
            else:
                pool = market_rows
                meta["warnings"].append(
                    f"板块「{'、'.join(kws)}」未命中，结果来自全市场活跃池（非该板块）")
        else:
            pool = market_rows
            meta["notes"].append("未输入板块，使用全市场活跃池（成交额 top600）")
    elif resolved["unknown"]:
        meta["warnings"].append("未识别板块：" + "、".join(resolved["unknown"]) + "（已忽略）")

    if not sector_of:
        for r in pool:
            sector_of[r["code"]] = r.get("industry") or "未分类"

    uniq, seen = [], set()
    for r in sorted(pool, key=lambda x: -(x.get("amount_yi") or 0)):
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        uniq.append(r)
    meta["pool_size"] = len(uniq)
    return uniq, sector_of


def lowval_coarse_filter(rows, tmin, tmax):
    """硬门槛：换手区间 / PE>0 / 价格下限 / 剔 ST 退市 次新"""
    out = []
    for r in rows:
        name = r.get("name") or ""
        if not r.get("code") or not name:
            continue
        if name.startswith(("N", "C", "ST", "退", "S")) or "ST" in name:
            continue
        price = r.get("price") or 0
        if price < PRICE_FLOOR:
            continue
        pe = r.get("pe")
        if pe is None or pe <= 0:
            continue
        t = r.get("turnover")
        if t is None or t < tmin or t > tmax:
            continue
        if (r.get("amount_yi") or 0) <= 0:
            continue
        out.append(r)
    return out


def lowval_interleave_by_sector(rows, sector_of):
    """各板块轮流取候选（组内仍按成交额降序），避免结果被单一细分行业占满"""
    groups = {}
    for r in rows:
        groups.setdefault(sector_of.get(r["code"], "未分类"), []).append(r)
    if len(groups) <= 1:
        return list(rows)
    out, i = [], 0
    while True:
        added = False
        for g in groups.values():
            if i < len(g):
                out.append(g[i])
                added = True
        if not added:
            break
        i += 1
    return out


# ---------------- 低值复苏：财务与复苏判定 ----------------
def _lowval_fetch_eastmoney(code):
    """东财 F10 主要财务指标（季报/半年报/年报，最新在前）"""
    market = "SH" if code.startswith(("6", "9", "5")) else (
        "BJ" if code.startswith(("4", "8")) else "SZ")
    params = {
        "type": "RPT_F10_FINANCE_MAINFINADATA", "sty": "ALL",
        "filter": f'(SECUCODE="{code}.{market}")',
        "p": "1", "ps": "8", "sr": "-1", "st": "REPORT_DATE",
        "source": "HSF10", "client": "PC",
    }
    data = datafeed.get_json("https://datacenter.eastmoney.com/securities/api/data/get",
                             params=params, headers=LOWVAL_HEADERS,
                             timeout=LOWVAL_TIMEOUT, retries=2)
    rows = []
    for x in ((data.get("result") or {}).get("data") or []):
        rows.append({
            "report_date": (x.get("REPORT_DATE") or "")[:10],
            "report_name": x.get("REPORT_DATE_NAME") or "",
            "rev_growth": _lv_num(x.get("TOTALOPERATEREVETZ")),
            "profit_growth": _lv_num(x.get("PARENTNETPROFITTZ")),
            "roe": _lv_num(x.get("ROEJQ")),
            "gross_margin": _lv_num(x.get("XSMLL")),
            "net_margin": _lv_num(x.get("XSJLL")),
            "debt_ratio": _lv_num(x.get("ZCFZL")),
        })
    return rows


def lowval_fetch_quarterly(code, force=False):
    """东财 F10 主要财务指标（季报/半年报/年报，最新在前），当日缓存；
    东财不可用时降级 akshare"""
    cache = _lv_load_json(LOWVAL_FIN_CACHE_FILE) or {}
    hit = cache.get(code) or {}
    if not force and hit.get("fetched_at") == _lv_today() and hit.get("rows"):
        return hit["rows"]
    try:
        rows, _src = datafeed.financials(
            code, native=lambda: _lowval_fetch_eastmoney(code))
    except Exception:
        rows = []
    if rows:
        cache[code] = {"fetched_at": _lv_today(), "rows": rows}
        _lv_save_json(LOWVAL_FIN_CACHE_FILE, cache)
        return rows
    return hit.get("rows") or []          # 接口失败时退回旧缓存


def lowval_recovery_assess(fins, strict=True):
    """业绩复苏判定。
    strict：最新报告期 营收同比>0 且 净利同比>0，且至少一项优于上一期
    放宽：任一同比>0 且至少一项优于上一期
    """
    if not fins:
        return {"ok": False, "note": "财务数据不足（接口无数据）",
                "rev_growth": None, "profit_growth": None, "improved": None}
    latest = fins[0]
    prev = fins[1] if len(fins) > 1 else None
    rev, npf = latest.get("rev_growth"), latest.get("profit_growth")
    if rev is None or npf is None:
        return {"ok": False, "note": "财务数据不足（同比缺失）",
                "rev_growth": rev, "profit_growth": npf, "improved": None}
    improved = None
    if prev and prev.get("rev_growth") is not None and prev.get("profit_growth") is not None:
        improved = bool(rev > prev["rev_growth"] or npf > prev["profit_growth"])
    if strict:
        ok = rev > 0 and npf > 0 and (improved is not False)
        cond = "营收同比>0 且 净利同比>0"
    else:
        ok = (rev > 0 or npf > 0) and (improved is not False)
        cond = "营收或净利任一同比>0"
    note = f"{latest['report_name']}：营收同比{rev:+.1f}%、净利同比{npf:+.1f}%"
    if ok:
        note += "，同比增幅继续改善" if improved else "（上期数据缺失，未做改善校验）"
    else:
        note += f"，未满足{cond}"
        if improved is False:
            note += "（且同比增幅未改善）"
    return {"ok": bool(ok), "note": note, "rev_growth": rev, "profit_growth": npf,
            "improved": improved, "report_name": latest.get("report_name"),
            "roe": latest.get("roe")}


# ---------------- 低值复苏：热度剔除与打分 ----------------
def _lv_limit_pct(code):
    """涨停幅度阈值：创业板/科创板 20%，北交所 30%，其余 10%"""
    if code.startswith(("300", "301", "688", "689")):
        return 0.195
    if code.startswith(("4", "8")):
        return 0.29
    return 0.095


def lowval_heat_check(codes):
    """5 日暴涨或近 3 日连续涨停的剔除；返回 (剔除集合, K线缓存)
    K线源连续失败时提前放弃，避免一只一个重试把整页拖慢（结果里会标注未剔除）。"""
    rows_map, excluded, miss = {}, set(), 0
    for c in codes:
        try:
            rows = Q.fetch_kline(c, datalen=90)
        except Exception:
            miss += 1
            if miss >= 3 and not rows_map:
                break
            continue
        miss = 0
        if len(rows) < 6:
            continue
        rows_map[c] = rows
        closes = [r["close"] for r in rows]
        prev5 = closes[-6]
        ret5 = closes[-1] / prev5 - 1 if prev5 else 0.0
        thr = _lv_limit_pct(c)
        cnt = 0
        for i in range(max(1, len(rows) - 3), len(rows)):
            prev = rows[i - 1]["close"]
            if prev and (rows[i]["close"] / prev - 1) >= thr:
                cnt += 1
        if ret5 > LOWVAL_HEAT_RET5_CAP or cnt >= LOWVAL_HEAT_LIMIT_DAYS:
            excluded.add(c)
    if len(rows_map) >= 10:
        try:
            excluded |= heat_exclude(list(rows_map), rows_map)
        except Exception:
            pass
    return excluded, rows_map


def _lv_trend_note(rows):
    """60 日涨幅与均线状态（仅展示，不做门槛）"""
    if not rows or len(rows) < 61:
        return None
    closes = [r["close"] for r in rows]
    ma20 = Q.sma(closes, 20)[-1]
    ma60 = Q.sma(closes, 60)[-1]
    last = closes[-1]
    return {
        "ret60": round((last / closes[-61] - 1) * 100, 2) if closes[-61] else None,
        "above_ma20": None if ma20 is None else bool(last > ma20),
        "ma20_above_ma60": None if (ma20 is None or ma60 is None) else bool(ma20 > ma60),
    }


def _lv_clip01(x):
    return max(0.0, min(1.0, x))


def lowval_score(assess, pe, pe_median, turnover, tmin, tmax):
    """0~100 分：复苏强度 45% + 估值便宜度 35% + 换手适中度 20%"""
    rev = assess.get("rev_growth") or 0.0
    npf = assess.get("profit_growth") or 0.0
    imp = assess.get("improved")
    rec = (0.45 * _lv_clip01(rev / 30.0) + 0.35 * _lv_clip01(npf / 40.0)
           + 0.20 * (1.0 if imp else 0.5 if imp is None else 0.0))
    value = _lv_clip01((1 - (pe / pe_median)) / 0.5) if pe_median else 0.5
    mid, span = (tmin + tmax) / 2.0, max((tmax - tmin) / 2.0, 0.1)
    turn = _lv_clip01(1 - abs((turnover if turnover is not None else mid) - mid) / span)
    total = (LOWVAL_W_RECOVERY * rec + LOWVAL_W_VALUE * value
             + LOWVAL_W_TURNOVER * turn) * 100
    return round(total, 1), {"recovery": round(rec * 100, 1),
                             "value": round(value * 100, 1),
                             "turnover": round(turn * 100, 1)}


def _lv_pe_median(rows, fallback_rows):
    """板块内 PE 中位数（取整个板块样本，剔除 ST/次新/低价）；样本不足用全市场兜底"""
    def _ok(r):
        name = r.get("name") or ""
        if name.startswith(("N", "C", "ST", "退")) or "ST" in name:
            return False
        if (r.get("price") or 0) < PRICE_FLOOR:
            return False
        return bool(r.get("pe") and r["pe"] > 0)

    pes = [r["pe"] for r in rows if _ok(r)]
    if len(pes) >= LOWVAL_PE_MIN_SAMPLE:
        return statistics.median(pes), "所属板块内中位数"
    all_pe = [r["pe"] for r in fallback_rows if _ok(r)]
    if all_pe:
        return statistics.median(all_pe), "全市场中位数（板块样本不足）"
    return None, "无参考样本"


# ---------------- 低值复苏：主入口 ----------------
def lowval_fallback_pack(pick, force=False):
    """行情源（腾讯）不可用时的降级数据包：
    现价/PE/PB 取自东财板块接口已抓到的字段，财务取东财 F10，52 周取东财 push2。
    结构与 research_data 的数据包一致，可直接喂给速评与四大师评分。"""
    code = pick["code"]
    fins = lowval_fetch_quarterly(code, force=force)
    high52 = low52 = None
    try:
        import research_data
        high52, low52 = research_data.fetch_52w(code)
    except Exception:
        pass
    return {
        "code": code, "name": pick.get("name") or code,
        "price": pick.get("price"), "change_pct": pick.get("change_pct"),
        "pe": pick.get("pe"), "pb": pick.get("pb"),
        "market_cap_yi": pick.get("total_mv"), "float_cap_yi": None,
        "amount_yi": pick.get("amount"), "turnover_rate": pick.get("turnover"),
        "high_52w": high52, "low_52w": low52,
        "financials": fins, "report_date": _lv_today(), "from_cache": False,
        "notes": ["行情源(腾讯)不可用：现价/PE/PB 取自东财板块行情，"
                  "财务取自东财 F10（降级生成）"],
    }


def lowval_fundamental(picks, force=False):
    """对入选股票生成「基本面速评（自动初筛）」与「四大师财务代理初筛」HTML 片段。
    复用 research_data 的年报财务快照与 master_score 的规则化评分；
    两块共用同一份数据包，每只 3 个请求（当日快照自动缓存，二次运行很快）；
    行情源不可用时自动降级为纯东财数据，不会整块空白。"""
    out = {"quick_html": "", "master_html": "", "failed": [], "degraded": []}
    if not picks:
        return out
    try:
        import research_data
    except Exception:
        return out
    import concurrent.futures as _cf

    def _pack(p):
        """先取标准数据包（腾讯行情+东财）；行情源不可用则退回纯东财降级包"""
        try:
            pack = research_data.get_pack(p["code"], force=force)
            if pack:
                return pack, "ok"
        except Exception:
            pass
        try:
            return lowval_fallback_pack(p, force=force), "degraded"
        except Exception:
            return None, "failed"

    packs = [None] * len(picks)

    def _collect(results):
        for i, (pack, tag) in enumerate(results):
            packs[i] = pack
            if tag == "degraded":
                out["degraded"].append(picks[i]["code"])
            elif tag == "failed":
                out["failed"].append(picks[i]["code"])

    workers = max(1, min(4, len(picks)))
    try:                       # 逐只取数彼此独立，并行做，避免一只超时拖慢全部
        with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
            _collect(list(ex.map(_pack, picks)))
    except Exception:
        _collect([_pack(p) for p in picks])
    try:
        out["quick_html"] = research_data.build_quick_review_html(packs)
    except Exception:
        pass
    try:
        import master_score
        out["master_html"] = master_score.build_master_review_html(packs)
    except Exception:
        pass
    return out


def pick_lowval(sectors=None, limit=LOWVAL_LIMIT, tmin=LOWVAL_TURNOVER_MIN,
                tmax=LOWVAL_TURNOVER_MAX, relax=True, force=False,
                with_fundamental=True):
    """低值复苏选股（网页 /lowval 专用），返回 {"picks": [...], "meta": {...}}

    picks 字段：code/name/sector/price/change_pct/turnover/pe/pb/total_mv/amount/
                score/score_parts/rev_growth/profit_growth/roe/report_name/
                recovery_note/relax_level/trend
    fundamental 字段：{"quick_html","master_html","failed"}（基本面速评 + 四大师初筛）
    """
    meta = {"warnings": [], "notes": [], "sectors_input": [], "boards": [],
            "unknown_sectors": [], "relax_level": 0, "relax_notes": [],
            "market_ok": None, "degraded": False,
            "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "turnover_range": [tmin, tmax]}
    try:
        db.init_db()
    except Exception:
        pass

    pool, sector_of = lowval_build_pool(sectors or "", meta, force=force)
    if not pool:
        meta["warnings"].append("候选池为空：板块接口与全市场截面均不可用")
        return {"picks": [], "meta": meta}

    fallback_rows = _lv_market_rows(force) or pool
    stages = [(tmin, tmax, True, 0, None)]
    if relax:
        stages.append((LOWVAL_TURNOVER_RELAX[0], LOWVAL_TURNOVER_RELAX[1], True, 1,
                       "换手区间放宽至 %.0f%%~%.0f%%" % LOWVAL_TURNOVER_RELAX))
        stages.append((LOWVAL_TURNOVER_RELAX[0], LOWVAL_TURNOVER_RELAX[1], False, 2,
                       "复苏口径放宽为『营收或净利任一同比>0 且较上期改善』"))

    used, verified, kline_map = set(), {}, {}
    picks = []
    for (lo, hi, strict, lvl, relax_note) in stages:
        cands = lowval_coarse_filter(pool, lo, hi)
        meta["relax_level"] = lvl
        if lvl:
            meta["notes"].append(f"分级放宽（第{lvl}级）：{relax_note}")
            meta["relax_notes"].append(relax_note)
            meta["turnover_range"] = [lo, hi]
        pe_median, pe_scope = _lv_pe_median(pool, fallback_rows)
        meta["pe_median"] = round(pe_median, 2) if pe_median else None
        meta["pe_median_scope"] = pe_scope
        if not pe_median:
            meta["warnings"].append("PE 中位数样本不足，估值门槛本次未生效")
        cap = LOWVAL_SHORTLIST_SIZE if lvl == 0 else LOWVAL_SHORTLIST_MAX
        ordered = lowval_interleave_by_sector(cands, sector_of)
        if pe_median:
            shortlist = [r for r in ordered if r.get("pe") and r["pe"] < pe_median][:cap]
        else:
            shortlist = ordered[:cap]
        meta["shortlist_size"] = len(shortlist)
        if not shortlist:
            continue

        for r in shortlist:
            if r["code"] in used:
                continue
            used.add(r["code"])
            rows = lowval_fetch_quarterly(r["code"], force=force)
            verified[r["code"]] = {
                "row": r, "sector": sector_of.get(r["code"], "未分类"),
                "assess": lowval_recovery_assess(rows, strict=strict),
            }
            time.sleep(0.12)
        short_codes = {r["code"] for r in shortlist}
        ok_codes = [c for c, v in verified.items()
                    if v["assess"]["ok"] and c in short_codes]
        if len(ok_codes) < limit and lvl < stages[-1][3]:
            continue                      # 进入下一级放宽
        if len(ok_codes) < limit:
            meta["warnings"].append(
                f"放宽后仍只有 {len(ok_codes)} 只达标（目标 {limit} 只），如实输出")
        excluded, kline_map = lowval_heat_check(ok_codes)
        if ok_codes and not kline_map:
            meta["notes"].append("K线源暂不可用，本次未做热度剔除"
                                 "（5日暴涨/连续涨停未过滤，结果仅供参考）")
        if excluded:
            names = [verified[c]["row"]["name"] for c in excluded if c in verified]
            meta["notes"].append("热度剔除（5日暴涨或连续涨停）：" + "、".join(names))
        scored = []
        for c in ok_codes:
            if c in excluded:
                continue
            v = verified[c]
            sc, parts = lowval_score(v["assess"], v["row"]["pe"], pe_median,
                                     v["row"]["turnover"], lo, hi)
            scored.append((sc, parts, c))
        scored.sort(key=lambda x: -x[0])
        picks = []
        for sc, parts, c in scored[:limit]:
            v, r = verified[c], verified[c]["row"]
            picks.append({
                "code": c, "name": r["name"], "sector": v["sector"],
                "price": r["price"], "change_pct": r["change_pct"],
                "turnover": r["turnover"], "pe": r["pe"], "pb": r["pb"],
                "total_mv": r["total_mv"], "amount": r["amount_yi"],
                "score": sc, "score_parts": parts,
                "rev_growth": v["assess"].get("rev_growth"),
                "profit_growth": v["assess"].get("profit_growth"),
                "roe": v["assess"].get("roe"),
                "report_name": v["assess"].get("report_name"),
                "recovery_note": v["assess"]["note"],
                "relax_level": lvl, "trend": _lv_trend_note(kline_map.get(c)),
                "source": r.get("source", ""),
            })
        if len(picks) >= limit or lvl >= stages[-1][3]:
            break
        picks = []

    try:
        reg_ok, reg_note = _market_regime()
        meta["market_ok"] = reg_ok
        meta["market_note"] = reg_note
    except Exception as e:
        meta["market_note"] = f"大盘环境判定失败（{e}）"

    meta["checked"] = len(verified)
    meta["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = {"picks": picks, "meta": meta}
    if with_fundamental and picks:
        try:
            result["fundamental"] = lowval_fundamental(picks, force=force)
            failed = result["fundamental"].get("failed") or []
            if failed:
                meta["notes"].append("基本面数据获取失败：" + "、".join(failed)
                                     + "（该股在速评表中已跳过）")
            degraded = result["fundamental"].get("degraded") or []
            if degraded:
                meta["notes"].append("行情源(腾讯)不可用，以下股票的基本面速评已降级为"
                                     "纯东财数据（现价/PE/PB 取东财板块行情）："
                                     + "、".join(degraded))
        except Exception as e:
            meta["warnings"].append(f"基本面速评生成失败：{e}")
    return result


if __name__ == "__main__":
    result = pick_hot_stocks(3)
    print("meta:", result["meta"])
    for p in result["picks"]:
        print(p)
