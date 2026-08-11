# -*- coding: utf-8 -*-
"""每日热门股筛选（选股系统 v2）：Z-score热度 / 多日持续性 / 自适应过滤 / 市场环境 / 行业分散"""
import datetime
import os

import selection

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    today = datetime.date.today().strftime("%Y%m%d")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    result = selection.pick_hot_stocks(3)
    picks = result["picks"]
    meta = result["meta"]
    print("热门股TOP3:", [(p["code"], p["name"]) for p in picks])
    for w in meta.get("warnings", []):
        print("提示:", w)

    lines = [f"每日热门股（选股系统 v2） {today}", "=" * 46]
    for idx, r in enumerate(picks, 1):
        pe_s = f"{r['pe']:.1f}" if r.get("pe") else "—"
        line = (f"{idx}. {r['name']}({r['code']})  {r['price']:.2f}元  "
                f"{r['change_pct']:+.2f}%  成交额{r['amount']:.1f}亿  "
                f"换手{r['turnover']:.1f}%  PE{pe_s}  "
                f"总市值{r.get('total_mv', 0):.0f}亿  热度分{r.get('score', 0)}")
        lines.append(line)
        print(line)
    lines.append("=" * 46)
    for w in meta.get("warnings", []):
        lines.append("提示: " + w)
    for n in meta.get("notes", [])[:2]:
        lines.append("说明: " + n)
    lines.append("=" * 46)
    lines.append(f"数据来源: 新浪/东财行情 | 生成时间: {now} | 仅供研究参考,不构成投资建议")

    out = os.path.join(OUT_DIR, f"每日热门股_{today}.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("已保存:", out)


if __name__ == "__main__":
    main()
