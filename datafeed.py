# -*- coding: utf-8 -*-
"""统一数据抓取层（主源不变，akshare/efinance 仅作兜底）
=========================================================
解决体检报告里的两个问题：
  1. 7 个模块各自 requests.get，无统一重试/退避/限流
  2. 单一数据源失败就没有退路

设计原则：
  - **主源顺序不变**：东财/新浪/腾讯仍是第一优先，历史报告口径可回溯
  - akshare / efinance 只在主源失败或返回空时接管，未安装自动跳过
  - 语义级兜底（quote/kline/financials/rank_list/board_list），不是简单包一层 HTTP
  - 所有返回都带来源标注：dict 结果带 `_source` 字段，列表结果由调用方拿 `source`

用法：
    rows, src = datafeed.kline("600519", days=260, native=lambda: _sina_kline(...))
    quote["_source"]     # "腾讯" / "akshare" / "efinance"
"""
import datetime
import threading
import time
from urllib.parse import urlsplit

import requests

import config as C
import log_utils

log = log_utils.get_logger("datafeed")

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126 Safari/537.36")
DEFAULT_TIMEOUT = C.HTTP_TIMEOUT      # 单次请求超时（秒）
DEFAULT_RETRIES = C.HTTP_RETRIES      # 单 URL 重试次数
DEFAULT_BACKOFF = C.HTTP_BACKOFF      # 退避底数：等待 = BACKOFF ** 尝试次数
MIN_INTERVAL = C.HTTP_MIN_INTERVAL    # 同一 host 两次请求的最小间隔（秒）


class DataFeedError(RuntimeError):
    """所有数据源都失败时抛出"""


_throttle_lock = threading.Lock()
_last_call = {}


def _throttle(url, min_interval=MIN_INTERVAL):
    """按 host 限流，避免把免费接口打挂"""
    host = urlsplit(url).netloc or "unknown"
    with _throttle_lock:
        now = time.time()
        wait = _last_call.get(host, 0.0) + min_interval - now
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.time()


def http_get(url, params=None, headers=None, timeout=DEFAULT_TIMEOUT,
             retries=DEFAULT_RETRIES, backoff=DEFAULT_BACKOFF,
             min_interval=MIN_INTERVAL, encoding=None):
    """统一 GET：UA / 超时 / 重试 / 指数退避 / 按 host 限流。
    成功返回 Response（未调用 raise_for_status，交由调用方解析）；
    全部失败抛 DataFeedError。"""
    hdrs = {"User-Agent": DEFAULT_UA}
    if headers:
        hdrs.update(headers)
    last_err = None
    for attempt in range(max(1, retries)):
        _throttle(url, min_interval)
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            if encoding:
                r.encoding = encoding
            if r.status_code >= 400:
                raise DataFeedError(f"HTTP {r.status_code}")
            return r
        except Exception as e:          # 网络抖动/超时/限流都重试
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff ** attempt)
    log.warning("http_get 失败 url=%s err=%s", url, last_err)
    raise DataFeedError(f"请求失败: {url} ({last_err})")


def get_json(url, **kw):
    return http_get(url, **kw).json()


def get_text(url, **kw):
    return http_get(url, **kw).text


# ---------------- 兜底调度 ----------------
def fallback(name, calls):
    """依次尝试 [(来源名, 可调用对象), ...]；返回 (value, source)。
    空结果视为失败并继续下一个来源；全部失败抛 DataFeedError。"""
    errors = []
    for source, fn in calls:
        if fn is None:
            continue
        try:
            value = fn()
        except Exception as e:
            errors.append(f"{source}: {e}")
            log.warning("%s 取数失败，尝试下一个数据源：%s", name, source, exc_info=True)
            continue
        if value:
            if source != calls[0][0]:
                log.warning("%s 已降级到备用数据源：%s", name, source)
            if isinstance(value, dict):
                value.setdefault("_source", source)
            return value, source
        errors.append(f"{source}: 空结果")
    raise DataFeedError(f"{name} 全部数据源失败（{'；'.join(errors)}）")


# ---------------- akshare / efinance 延迟加载 ----------------
def _import_ak():
    try:
        import akshare as ak
        return ak
    except Exception:
        return None


def _import_ef():
    try:
        import efinance as ef
        return ef
    except Exception:
        return None


def optional_available():
    """返回可选兜底依赖的可用状态（报告/日志用）"""
    return {"akshare": _import_ak() is not None, "efinance": _import_ef() is not None}


def _num(v):
    try:
        if v is None or v == "-" or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _market(code):
    code = str(code).strip()
    if code.startswith(("6", "9", "5")):
        return "SH"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SZ"


_ak_spot_cache = {"ts": 0.0, "df": None}


def ak_spot():
    """akshare 全市场快照（进程内缓存 60 秒，供 quote/rank 兜底复用）"""
    ak = _import_ak()
    if ak is None:
        raise DataFeedError("akshare 未安装")
    now = time.time()
    if _ak_spot_cache["df"] is not None and now - _ak_spot_cache["ts"] < 60:
        return _ak_spot_cache["df"]
    df = ak.stock_zh_a_spot_em()
    _ak_spot_cache.update({"ts": now, "df": df})
    return df


def _ak_row(code):
    df = ak_spot()
    hit = df[df["代码"] == str(code)]
    if hit.empty:
        raise DataFeedError(f"akshare 快照无 {code}")
    return hit.iloc[0].to_dict()


def ak_quote(code):
    """akshare 行情 → 统一结构"""
    r = _ak_row(code)
    return {
        "code": str(r.get("代码")), "name": r.get("名称"),
        "price": _num(r.get("最新价")), "change_pct": _num(r.get("涨跌幅")),
        "amount_yi": (_num(r.get("成交额")) or 0) / 1e8,
        "turnover_rate": _num(r.get("换手率")),
        "pe": _num(r.get("市盈率-动态")), "pb": _num(r.get("市净率")),
        "total_mv_yi": (_num(r.get("总市值")) or 0) / 1e8,
        "circ_mv_yi": (_num(r.get("流通市值")) or 0) / 1e8,
        "vol_ratio": _num(r.get("量比")),
        "_source": "akshare",
    }


def ef_quote(code):
    """efinance 行情 → 统一结构"""
    ef = _import_ef()
    if ef is None:
        raise DataFeedError("efinance 未安装")
    df = ef.stock.get_latest_quote([str(code)])
    if df is None or len(df) == 0:
        raise DataFeedError("efinance 无行情")
    r = df.iloc[0].to_dict()
    return {
        "code": str(r.get("股票代码") or code), "name": r.get("股票名称"),
        "price": _num(r.get("最新价")), "change_pct": _num(r.get("涨跌幅")),
        "amount_yi": (_num(r.get("成交额")) or 0) / 1e8,
        "turnover_rate": _num(r.get("换手率")),
        "pe": _num(r.get("市盈率(动态)")), "pb": _num(r.get("市净率")),
        "total_mv_yi": None, "circ_mv_yi": None,
        "vol_ratio": _num(r.get("量比")),
        "_source": "efinance",
    }


def _ak_kline(code, days, adjust):
    ak = _import_ak()
    if ak is None:
        raise DataFeedError("akshare 未安装")
    start = (datetime.date.today() - datetime.timedelta(days=int(days * 1.7) + 30))
    df = ak.stock_zh_a_hist(symbol=str(code), period="daily",
                            start_date=start.strftime("%Y%m%d"),
                            end_date=datetime.date.today().strftime("%Y%m%d"),
                            adjust={"hfq": "hfq", "qfq": "qfq"}.get(adjust, ""))
    if df is None or len(df) == 0:
        raise DataFeedError("akshare K线为空")
    out = []
    for _, r in df.iterrows():
        out.append({
            "date": str(r["日期"])[:10],
            "open": float(r["开盘"]), "high": float(r["最高"]),
            "low": float(r["最低"]), "close": float(r["收盘"]),
            "volume": float(r["成交量"]) * 100,      # akshare 单位=手 → 股
        })
    return out[-days:]


def _ef_kline(code, days, adjust):
    ef = _import_ef()
    if ef is None:
        raise DataFeedError("efinance 未安装")
    df = ef.stock.get_quote_history(str(code), fqt={"hfq": 2, "qfq": 1}.get(adjust, 0))
    if df is None or len(df) == 0:
        raise DataFeedError("efinance K线为空")
    out = []
    for _, r in df.iterrows():
        out.append({
            "date": str(r["日期"])[:10],
            "open": float(r["开盘"]), "high": float(r["最高"]),
            "low": float(r["最低"]), "close": float(r["收盘"]),
            "volume": float(r["成交量"]) * 100,
        })
    return out[-days:]


def quote(code, native=None):
    """行情：主源 → akshare → efinance"""
    return fallback(f"行情({code})", [
        ("主源", native),
        ("akshare", lambda: ak_quote(code)),
        ("efinance", lambda: ef_quote(code)),
    ])


def kline(code, days=260, adjust="qfq", native=None):
    """K线：主源 → akshare → efinance（返回 (rows, source)）"""
    return fallback(f"K线({code})", [
        ("主源", native),
        ("akshare", lambda: _ak_kline(code, days, adjust)),
        ("efinance", lambda: _ef_kline(code, days, adjust)),
    ])


def _ak_financials(code):
    """akshare 财务摘要 → 统一结构（营收/净利同比 + ROE/毛利率/净利率/负债率）"""
    ak = _import_ak()
    if ak is None:
        raise DataFeedError("akshare 未安装")
    df = ak.stock_financial_abstract(symbol=str(code))
    if df is None or len(df) == 0:
        raise DataFeedError("akshare 财务为空")
    cols = [c for c in df.columns if str(c)[:4].isdigit()]
    cols = sorted(cols, reverse=True)[:5]
    rows = []
    for col in cols:
        def _get(*names):
            for n in names:
                hit = df[df.iloc[:, 1].astype(str).str.contains(n, regex=False)]
                if not hit.empty:
                    return _num(hit.iloc[0][col])
            return None
        rows.append({
            "report_date": str(col),
            "report_name": str(col),
            "rev_growth": _get("营业总收入同比增长", "营业收入同比增长"),
            "profit_growth": _get("归母净利润同比增长", "净利润同比增长"),
            "roe": _get("净资产收益率"),
            "gross_margin": _get("销售毛利率", "毛利率"),
            "net_margin": _get("销售净利率", "净利率"),
            "debt_ratio": _get("资产负债率"),
            "revenue": _get("营业总收入", "营业收入"),
            "net_profit": _get("归母净利润", "净利润"),
        })
    if not rows:
        raise DataFeedError("akshare 财务字段解析为空")
    return rows


def financials(code, native=None):
    """财务：主源 → akshare（返回 (rows, source)）"""
    return fallback(f"财务({code})", [
        ("主源", native),
        ("akshare", lambda: _ak_financials(code)),
    ])


def ak_rank(kind, n=100):
    """akshare 全市场快照按指标排序 → 与新浪排行同构的行"""
    df = ak_spot()
    key = {"volume": "成交量", "amount": "成交额", "turnover": "换手率"}.get(kind)
    if key not in df.columns:
        raise DataFeedError(f"akshare 快照缺少字段 {key}")
    top = df.sort_values(key, ascending=False).head(n)
    out = []
    for _, r in top.iterrows():
        out.append({
            "code": str(r.get("代码")), "name": r.get("名称"),
            "price": _num(r.get("最新价")),
            "change_pct": _num(r.get("涨跌幅")),
            "amount_yuan": _num(r.get("成交额")) or 0.0,
            "volume": (_num(r.get("成交量")) or 0.0) * 100,
            "turnover": _num(r.get("换手率")) or 0.0,
            "pe": _num(r.get("市盈率-动态")),
            "nmc_yuan": (_num(r.get("流通市值")) or 0.0),
            "mktcap_yuan": (_num(r.get("总市值")) or 0.0),
            "circ_shares": ((_num(r.get("流通市值")) or 0.0) /
                            (_num(r.get("最新价")) or 1)),
        })
    return out


def rank_list(kind, native=None, n=100):
    """热度榜：主源 → akshare 快照按对应指标排序"""
    return fallback(f"热度榜({kind})",
                    [("主源", native), ("akshare", lambda: ak_rank(kind, n))])


def board_list(native=None):
    """行业板块列表：主源 → akshare（返回 (rows, source)）"""
    def _ak_boards():
        ak = _import_ak()
        if ak is None:
            raise DataFeedError("akshare 未安装")
        df = ak.stock_board_industry_name_em()
        return [{"name": str(r.get("板块名称")), "code": str(r.get("板块代码"))}
                for _, r in df.iterrows()]
    return fallback("行业板块列表", [("主源", native), ("akshare", _ak_boards)])
