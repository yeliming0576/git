# -*- coding: utf-8 -*-
"""用法: python 清理股票数据.py 600519 000858 [更多代码]
删除指定股票在本地数据库中的 K线缓存和 v2 分析缓存（不影响选股历史与交易账本）。
下次分析该股票时会自动重新下载数据。"""
import sys

import db


def main():
    codes = [c for c in sys.argv[1:] if c.isdigit() and len(c) == 6]
    if not codes:
        print("用法: python 清理股票数据.py 600519 000858 ...")
        return
    for c in codes:
        db.purge_code(c)
        print(f"已清理 {c} 的本地缓存（K线/v2分析）")
    print("完成。")


if __name__ == "__main__":
    main()
