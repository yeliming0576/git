# -*- coding: utf-8 -*-
"""入口薄包装：实现已移入 stockapp/lunde_report.py，双击/命令行用法保持不变。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stockapp import lunde_report  # noqa: E402

if __name__ == "__main__":
    lunde_report.main()
