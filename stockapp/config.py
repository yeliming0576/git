# -*- coding: utf-8 -*-
"""集中配置：config.py 默认值 + 可选 config.json 覆盖
=====================================================
优先级：**config.json > config.py 默认值**（不使用环境变量）

用法：
    from stockapp import config
    config.PRICE_FLOOR          # 默认 2.0

想改参数又不想动代码（EXE 用户）：
    在项目根（EXE 同目录）新建 config.json，只写要覆盖的键即可，例如
        {"PRICE_CAP_FLOOR": 15, "LOWVAL_LIMIT": 8, "PORT": 9000}
    未知键、类型不符的键会被忽略并在日志里告警，其余键回退默认值。

注意：覆盖在 import config 时一次性生效，改完 JSON 需重启程序。
"""
import json
import os

from stockapp.app_paths import BASE
from stockapp import log_utils

log = log_utils.get_logger("config")

CONFIG_JSON = os.path.join(BASE, "config.json")

# ---------------- 选股（热度榜） ----------------
PRICE_MEDIAN_MULTIPLE = 2.5     # 股价上限 = 全市场股价中位数 × 倍数
PRICE_CAP_FLOOR = 12.0          # 股价上限绝对底限（元），与动态值取大
PRICE_FLOOR = 2.0               # 股价下限（元），过滤低价垃圾股
TURNOVER_FLOOR = 1.0            # 换手率绝对底线（%），低于直接淘汰
SHORTLIST_SIZE = 20             # 进入 20 日分位数检查的候选数量
PERCENTILE_LEVEL = 0.2          # 当日活跃度需高于过去 20 日的第 20 分位
PERCENTILE_RELAX = 0.1          # 候选不足时的放宽分位
HISTORY_DAYS = 5                # 多日持续性统计用最近 N 个交易日
MARKET_INDEX = "sh000985"       # 中证全指

# ---------------- 低值复苏选股 ----------------
LOWVAL_TURNOVER_MIN = 3.0
LOWVAL_TURNOVER_MAX = 8.0
LOWVAL_TURNOVER_RELAX = (2.0, 12.0)
LOWVAL_SHORTLIST_SIZE = 20
LOWVAL_SHORTLIST_MAX = 40
LOWVAL_LIMIT = 5
LOWVAL_W_RECOVERY = 0.45
LOWVAL_W_VALUE = 0.35
LOWVAL_W_TURNOVER = 0.20
LOWVAL_PE_MIN_SAMPLE = 8
LOWVAL_BOARD_PAGES = 2
LOWVAL_HEAT_RET5_CAP = 0.25
LOWVAL_HEAT_LIMIT_DAYS = 2
LOWVAL_BOARD_CACHE_DAYS = 7
LOWVAL_TIMEOUT = 12
LOWVAL_MARKET_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
LOWVAL_MARKET_PAGES = 6
LOWVAL_FIELDS = "f2,f3,f5,f6,f8,f9,f12,f14,f20,f23,f100"
LOWVAL_HOSTS = ["push2.eastmoney.com", "1.push2.eastmoney.com",
                "33.push2.eastmoney.com", "48.push2.eastmoney.com",
                "82.push2.eastmoney.com", "push2delay.eastmoney.com"]

# ---------------- 交易成本与回测 ----------------
COST_PER_SIDE = 0.0015          # 单边成本（往返 0.30%）
RF_ANNUAL = 0.02                # 无风险利率
TARGET_ATR_MULT = 4.0           # 目标位 4×ATR（盈亏比 2:1）
PERM_N = 200                    # 随机对照抽样次数
MIN_SAMPLE = 30                 # 样本不足门槛
RISK_PER_TRADE = 0.01           # 单笔风险预算
MAX_POSITION_PCT = 0.20         # 单票上限
EQUITY_DEFAULT = 100000.0       # 报告用总权益（元）

# ---------------- 报告服务 ----------------
HOST = "0.0.0.0"                # 局域网共享常开（保持现状）
PORT = 8765
INTERVAL = 300                  # 自动刷新间隔（秒）

# ---------------- 数据抓取 ----------------
HTTP_TIMEOUT = 15               # 单次请求超时（秒）
HTTP_RETRIES = 3                # 单 URL 重试次数
HTTP_BACKOFF = 1.5              # 退避底数
HTTP_MIN_INTERVAL = 0.3         # 同 host 两次请求最小间隔（秒）
QUOTE_TIMEOUT = 20              # 行情/K线类请求超时


def defaults():
    """返回全部默认键（用于文档、测试与覆盖校验）"""
    return {k: v for k, v in globals().items()
            if k.isupper() and k not in ("CONFIG_JSON",)}


def _compatible(value, default):
    """宽松类型校验：数字之间互通，list/tuple 互通，其余要求同类型"""
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, (list, tuple)):
        return isinstance(value, (list, tuple))
    return isinstance(value, type(default))


def load(path=None):
    """读取 config.json 覆盖默认值；返回 {"applied": [...], "ignored": [...]}"""
    path = path or CONFIG_JSON
    g = globals()
    base = defaults()
    applied, ignored = [], []
    if not os.path.isfile(path):
        return {"applied": applied, "ignored": ignored, "path": path}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.warning("config.json 解析失败，全部使用默认值：%s", e)
        return {"applied": applied, "ignored": ignored, "path": path}
    if not isinstance(data, dict):
        log.warning("config.json 顶层必须是对象，已忽略")
        return {"applied": applied, "ignored": ignored, "path": path}
    for key, value in data.items():
        if key not in base:
            ignored.append((key, "未知配置项"))
            continue
        default = base[key]
        if not _compatible(value, default):
            ignored.append((key, f"类型不符（期望 {type(default).__name__}）"))
            continue
        if isinstance(default, tuple) and isinstance(value, list):
            value = tuple(value)
        g[key] = value
        applied.append(key)
    if ignored:
        log.warning("config.json 忽略了 %d 项：%s", len(ignored), ignored)
    if applied:
        log.info("config.json 已覆盖 %d 项：%s", len(applied), applied)
    return {"applied": applied, "ignored": ignored, "path": path}


load()
