# -*- coding: utf-8 -*-
"""
自动近似模式：无需 Codex，直接为方向生成研究底稿
=================================================
1. 优先匹配 方向候选库.json（内置方向，含候选A股）；
2. 未匹配时，用活跃截面快照按关键词扫描股票名称（≥3只才启用）；
3. 生成底稿标记"自动近似"，看板明确提示未做产业链一手核验。

仅供初筛，瓶颈评级/份额均为近似值，禁止据此重仓。
"""
import datetime
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

DRAFT_DIR = os.path.join(BASE, "研究方向")
LIBRARY = os.path.join(BASE, "方向候选库.json")

STOPWORDS = ("方向", "板块", "概念", "产业链", "相关", "龙头", "公司",
             "股票", "自选", "分析", "研究", "主题", "的", "和", "与")

DEFAULT_CRITERIA = {
    "supply_concentration": 4, "expansion_months": 12,
    "substitutability": "部分", "utilization": 0.8,
    "demand_growth": 0.3, "validation_months": 9,
}


def _slug(direction):
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", direction.strip())
    return s[:40] or "方向"


def _tokens(direction):
    s = re.sub(r"[\s\-_/（）()【】]+", "", direction)
    for w in STOPWORDS:
        s = s.replace(w, "")
    if not s:
        return []
    out = [s[i:i + 2] for i in range(len(s) - 1)]
    if len(s) >= 2:
        out.append(s)
    return list(set(out))


def _build(direction, entry, scanned=False):
    os.makedirs(DRAFT_DIR, exist_ok=True)
    today = datetime.date.today().strftime("%Y-%m-%d")
    cands = entry.get("candidates", [])
    draft = {
        "topic": direction + "（自动近似）",
        "date": today,
        "trend": {
            "name": direction,
            "verified_events": [
                "自动近似模式：候选来自内置方向库/关键词扫描，未做公告、认证、客户验证等一手核验" if scanned
                else "自动近似模式：候选来自内置方向库，未做公告、认证、客户验证等一手核验"
            ],
            "capex_scale": "—",
            "conclusion": "可追踪（自动近似，仅供初筛）",
        },
        "bottlenecks": [{
            "link": entry.get("link", direction + "相关板块"),
            "layer": entry.get("layer", "Layer 2"),
            "rating": entry.get("rating") or "B",
            "criteria": entry.get("criteria") or dict(DEFAULT_CRITERIA),
            "tam_yi": entry.get("tam_yi"),
            "candidates": cands,
        }],
        "rebuttals": [
            "自动近似候选未经供给集中度/客户名单/扩产周期核验，禁止据此重仓",
            "若候选股与真实瓶颈环节偏离，本看板结论失效",
            "建议用公告/认证/客户验证补充核验后，再升级为正式研究底稿",
        ],
    }
    path = os.path.join(DRAFT_DIR, f"{_slug(direction)}_研究底稿.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=1)
    return path


def auto_draft_for(direction):
    """为方向生成自动近似底稿；失败返回 None"""
    direction = direction.strip()
    if not direction:
        return None
    try:
        with open(LIBRARY, encoding="utf-8") as f:
            lib = json.load(f)
    except Exception:
        lib = {}
    for key, entry in lib.items():
        if direction in key or key in direction:
            return _build(direction, entry, scanned=False)
    # 关键词扫描兜底
    toks = [t for t in _tokens(direction) if len(t) >= 2]
    rows = []
    try:
        import market_snapshot
        snap = market_snapshot.get_snapshot()
        rows = (snap or {}).get("rows") or []
    except Exception:
        pass
    cands = [r for r in rows if any(t in str(r.get("name", "")) for t in toks)]
    cands.sort(key=lambda r: -(r.get("amount") or 0))
    cands = cands[:15]
    if len(cands) >= 3:
        entry = {
            "link": direction + "相关板块",
            "layer": "方向板块",
            "rating": "B",
            "criteria": dict(DEFAULT_CRITERIA),
            "tam_yi": None,
            "candidates": [{
                "code": r["code"], "name": r.get("name", ""),
                "share_mkt": "关键词自动扫描（待核验）",
                "revenue_ratio": None,
                "verified": {},
                "catalyst": "—",
                "risks": ["需人工核验产业链位置与瓶颈逻辑"],
                "notes": "自动扫描候选",
            } for r in cands],
        }
        return _build(direction, entry, scanned=True)
    return None
