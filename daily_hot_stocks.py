# -*- coding: utf-8 -*-
"""入口薄包装：实现已移入 stockapp/daily_hot_stocks.py，双击/命令行用法保持不变。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stockapp import daily_hot_stocks  # noqa: E402

if __name__ == "__main__":
    daily_hot_stocks.main()
