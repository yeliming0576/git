# -*- coding: utf-8 -*-
"""入口薄包装：实现已移入 stockapp/fetch_lunde.py，双击/命令行用法保持不变。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stockapp import fetch_lunde  # noqa: E402

if __name__ == "__main__":
    fetch_lunde.main()
