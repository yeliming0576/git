# -*- coding: utf-8 -*-
"""进度上报模块：主流程调用 report()，GUI 用 set_callback() 接收"""
_callback = None


def set_callback(cb):
    global _callback
    _callback = cb


def report(stage, pct, msg=""):
    if _callback:
        try:
            _callback(stage, float(pct), str(msg))
        except Exception:
            pass
