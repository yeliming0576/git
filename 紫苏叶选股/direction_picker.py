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

# 系统内置推荐方向（按当前市场关注度排序，可自行增删）
PRESET_DIRECTIONS = [
    {"name": "AI算力-光通信", "reason": "全球AI资本开支高增，光模块/光芯片为确定性瓶颈环节"},
    {"name": "固态电池", "reason": "产业化临近，电解质、干法电极设备等环节卡脖子"},
    {"name": "人形机器人", "reason": "丝杠、减速器、力传感器等核心部件国产化瓶颈"},
    {"name": "创新药-GLP-1", "reason": "多肽原料药、注射笔供应链存在稀缺环节"},
    {"name": "半导体材料", "reason": "先进封装材料、电子特气等国产替代空间大"},
    {"name": "电力设备-电网", "reason": "AI用电与新能源并网推动特高压/配网投资上行"},
    {"name": "白酒消费", "reason": "高端白酒品牌壁垒深厚，现金流质量高"},
    {"name": "高股息-红利", "reason": "低利率环境下现金流稳定资产的防御方向"},
]


def recommend_direction():
    """系统推荐方向：优先返回已有底稿的方向；否则返回首个内置推荐。"""
    for p in PRESET_DIRECTIONS:
        if find_draft(p["name"]):
            return {"name": p["name"], "reason": p["reason"], "mode": "ready"}
    p = PRESET_DIRECTIONS[0]
    return {"name": p["name"], "reason": p["reason"], "mode": "task"}


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
    """按方向名查找底稿文件路径；优先正式底稿（非自动近似）；找不到返回 None"""
    if not os.path.isdir(DRAFT_DIR):
        return None
    direction = direction.strip()
    best = None
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
            if "自动近似" not in topic:
                return path
            best = best or path
    return best


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
    auto = False
    if not path:
        try:
            import auto_draft
            path = auto_draft.auto_draft_for(direction)
            auto = path is not None
        except Exception:
            path = None
    if path:
        outdir, slug, results = BP.run_draft(path, topic=None if auto else direction,
                                             outdir=outdir)
        return {"ok": True, "direction": direction, "draft": path,
                "outdir": outdir, "slug": slug, "results": results, "auto": auto}
    order = make_task_order(direction)
    return {"ok": False, "direction": direction, "task_order": order,
            "available": available_directions()}


def main(argv):
    if len(argv) >= 2 and argv[1] == "--list":
        for d in available_directions():
            print(" -", d)
        return 0
    if len(argv) < 2 or not argv[1].strip():
        rec = recommend_direction()
        direction = rec["name"]
        mode_txt = "已有底稿，可直接出看板" if rec["mode"] == "ready" else "暂无底稿，将生成研究任务单"
        print(f"⚡ 未输入方向，使用系统推荐：{direction}")
        print(f"   推荐理由：{rec['reason']}（{mode_txt}）")
    else:
        direction = argv[1]
    outdir = None
    if len(argv) >= 4 and argv[2] == "--outdir":
        outdir = argv[3]
    r = run_direction(direction, outdir=outdir)
    if r["ok"]:
        if r.get("auto"):
            print(f"✅ 已自动生成近似底稿并出看板（自动近似模式，未经一手核验）：")
        else:
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
