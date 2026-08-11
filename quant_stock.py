# -*- coding: utf-8 -*-
"""
用户自选股量化分析: python quant_stock.py 600519 000858 300750
输出: 用户选股量化_YYYYMMDD.html
"""
import os
import sys
import datetime

import quant_engine as Q
from daily_report import build_report_html

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "报告归档", "个股")
os.makedirs(OUT_DIR, exist_ok=True)


def main():
    codes = [c for c in sys.argv[1:] if c.isdigit() and len(c) == 6]
    if not codes:
        print("用法: python quant_stock.py 600519 000858 300750 ...")
        return
    today = datetime.date.today().strftime("%Y%m%d")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    analyses = []
    for code in codes:
        try:
            a = Q.analyze(code)
            analyses.append((code, "用户选股", a))
            print(f"完成: {a['quote']['name']}({code}) {a['verdict']} "
                  f"半年{a['half_ret']:+.2f}% 回测{a['bt']['total_ret']}%")
        except Exception as e:
            print(f"[{code}] 失败: {e}")
            analyses.append((code, "用户选股", None))
    title = f"用户选股量化分析 {today}"
    note = "自选股: " + "、".join(codes) + " | 每只含趋势判断/买卖点/策略回测/交易量"
    html = build_report_html(analyses, title, note, now)
    out = os.path.join(OUT_DIR, f"用户选股量化_{today}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("已保存:", out)
    try:
        import archive
        n = archive.archive_old_reports(OUT_DIR)
        if n:
            print(f"已归档 {n} 个旧报告 -> 报告归档 文件夹")
    except Exception:
        pass


if __name__ == "__main__":
    main()
