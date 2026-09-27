# -*- coding: utf-8 -*-
"""入口薄包装：实现已移入 stockapp/run_all_progress.py，双击/命令行用法保持不变。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stockapp import run_all_progress  # noqa: E402

if __name__ == "__main__":
    run_all_progress.main()
