# -*- coding: utf-8 -*-
"""
紫苏叶方向选股：行业方向自选入口
================================
用户输入方向（如 AI算力 / 白酒 / 固态电池）：
  1. 在 研究方向/ 里查找现成研究底稿 → 有则直接运行 bottleneck_picker 出看板；
  2. 没有则生成"研究任务单"（待研究/），把任务单发给 Codex，
     按 bottleneck-hunter 产出 JSON 研究底稿放回 研究方向/，下次输入同方向即可出结果。

用法：
  python direction_picker.py "AI算力"
  python direction_picker.py --list
"""
import datetime
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import bottleneck_picker as BP  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DRAFT_DIR = os.path.join(BASE, "研究方向")
TODO_DIR = os.path.join(DRAFT_DIR, "待研究")


def _slug(direction):
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", direction.strip())
    return s[:40] or "方向"


def available_directions():
    """研究方向库里已有底稿的主题列表"""
    if not os.path.isdir(DRAFT_DIR):
        return []
    out = []
    for f in os.listdir(DRAFT_DIR):
        if not (f.endswith(".json") and "研究底稿" in f):
            continue
        try:
            with open(os.path.join(DRAFT_DIR, f), encoding="utf-8") as fh:
                out.append(json.load(fh).get("topic", f))
        except Exception:
            continue
    return out


def find_draft(direction):
    """按方向名查找底稿文件路径；找不到返回 None"""
    if not os.path.isdir(DRAFT_DIR):
        return None
    direction = direction.strip()
    for f in os.listdir(DRAFT_DIR):
        if not (f.endswith(".json") and "研究底稿" in f):
            continue
        path = os.path.join(DRAFT_DIR, f)
        try:
            with open(path, encoding="utf-8") as fh:
                topic = json.load(fh).get("topic", "")
        except Exception:
            topic = f
        if direction in topic or topic in direction:
            return path
    return None


def make_task_order(direction):
    """生成研究任务单，返回文件路径"""
    os.makedirs(TODO_DIR, exist_ok=True)
    today = datetime.date.today().strftime("%Y-%m-%d")
    path = os.path.join(TODO_DIR, f"{_slug(direction)}_研究任务单.md")
    text = f"""# 紫苏叶方向研究任务单：{direction}

> 生成时间：{today}

请按 bottleneck-hunter skill 对「{direction}」方向执行完整研究，并输出 JSON 研究底稿：

1. **超级趋势确认**：验证事件（至少3个，带日期/来源）、资本开支规模、结论（可追踪/证据不足）；
2. **供应链物理拆解**：从终端需求逐层下钻，标出 Layer 1~4；
3. **瓶颈识别**：按6条判定（供给集中度/扩产周期/替代难度/产能利用率/需求增速/客户验证周期）给出 S/A/B 评级；
4. **候选 A 股**：每个瓶颈 1~3 只候选，含代码/名称/市场份额/瓶颈业务占比/交叉验证状态（客户/收入/产能/价格）/催化剂/风险；
5. **反证清单**：该方向可能被证伪的条件；
6. **输出格式**：参照 紫苏叶选股/研究底稿模板.json，保存为：
   `紫苏叶选股/研究方向/{_slug(direction)}_研究底稿.json`

研究完成后，回到本系统输入方向「{direction}」即可自动生成看板。
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def run_direction(direction, outdir=None):
    """主入口：找到底稿则直接出看板；否则生成任务单。返回 dict 结果"""
    direction = direction.strip()
    path = find_draft(direction)
    if path:
        outdir, slug, results = BP.run_draft(path, topic=direction, outdir=outdir)
        return {"ok": True, "direction": direction, "draft": path,
                "outdir": outdir, "slug": slug, "results": results}
    order = make_task_order(direction)
    return {"ok": False, "direction": direction, "task_order": order,
            "available": available_directions()}


def main(argv):
    if len(argv) >= 2 and argv[1] == "--list":
        for d in available_directions():
            print(" -", d)
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 1
    direction = argv[1]
    outdir = None
    if len(argv) >= 4 and argv[2] == "--outdir":
        outdir = argv[3]
    r = run_direction(direction, outdir=outdir)
    if r["ok"]:
        print(f"✅ 方向「{direction}」已有研究底稿，看板已生成：")
        print("  ", r["outdir"])
        for x in r["results"]:
            print(f"   {x['code']} {x['name']} [{x['rating']}] {x['strength']}★ {x['verdict']}")
    else:
        print(f"⚠ 方向「{direction}」还没有研究底稿。")
        print("   已生成研究任务单：", r["task_order"])
        print("   请把该任务单内容发给 Codex，按 bottleneck-hunter 生成底稿后放回 研究方向/。")
        if r["available"]:
            print("   当前已有方向：", "、".join(r["available"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
