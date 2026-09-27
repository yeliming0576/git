# -*- coding: utf-8 -*-
"""统一日志（标准库 logging）
============================
输出目标：
  - 控制台：保持原有用 print 的观感，格式 `[时间] [级别] 模块: 消息`
  - 文件：`日志/运行日志_YYYYMMDD.log`，自动保留最近 30 天

用法：
    import log_utils
    log = log_utils.get_logger("datafeed")
    log.warning("腾讯行情失败，切换 akshare 兜底", exc_info=True)

说明：本模块只负责"把原先被静默吞掉的异常记下来"，不替换项目里既有的 print 输出。
"""
import datetime
import logging
import os
import sys
import time

from app_paths import BASE

LOG_DIR = os.path.join(BASE, "日志")
KEEP_DAYS = 30
_initialized = set()


def _prune_old_logs(keep_days=KEEP_DAYS):
    """删除超过保留期的日志文件（失败不影响主流程）"""
    try:
        deadline = time.time() - keep_days * 86400
        for name in os.listdir(LOG_DIR):
            if not (name.startswith("运行日志_") and name.endswith(".log")):
                continue
            path = os.path.join(LOG_DIR, name)
            try:
                if os.path.getmtime(path) < deadline:
                    os.remove(path)
            except OSError:
                continue
    except OSError:
        pass


def get_logger(name="选股系统", level=logging.INFO):
    """按模块名取 logger（同一名字只配置一次 handler）"""
    logger = logging.getLogger(name)
    if name in _initialized:
        return logger
    logger.setLevel(level)
    logger.propagate = False
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
                            datefmt="%H:%M:%S")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, "运行日志_" +
                            datetime.date.today().strftime("%Y%m%d") + ".log")
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(level)
        logger.addHandler(fh)
        _prune_old_logs()
    except OSError:
        pass
    try:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.setLevel(level)
        logger.addHandler(sh)
    except Exception:
        pass
    _initialized.add(name)
    return logger
