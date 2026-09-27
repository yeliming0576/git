# -*- coding: utf-8 -*-
"""入口薄包装：实现已移入 stockapp/research_data.py，双击/命令行用法保持不变。

命令行（与 深度研究.cmd 一致）：
    python research_data.py 600519 [--json|--quick|--force]
    python research_data.py --import-thesis 600519 底稿.md
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stockapp import research_data  # noqa: E402

if __name__ == "__main__":
    sys.exit(research_data.main(sys.argv))
