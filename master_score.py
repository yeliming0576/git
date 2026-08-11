# -*- coding: utf-8 -*-
"""
四大师定性框架速评（规则化近似）
================================
把 AI-Berkshire 四大师框架（段永平/巴菲特/芒格/李录）转成可由财务数据
驱动的确定性评分，用于：
  - 每日报告“四大师定性速评”节（自动生成，不改选股逻辑）
  - 深度研究任务包中的“规则化速评”表（供 Codex 做真正定性研究时参考）

重要声明：本模块只是“数据代理的初筛近似”，不是真正的定性判断。
真实四大师分析仍需把任务包交给 Codex 按 investment-team/investment-research 执行。

评分范围：每维 0~5 分，综合 = 四维加权平均；分数越高越偏正面。
数据不足的维度给 2.5 分并标注低置信度，绝不臆测。
"""
import datetime
import html
import statistics

FINANCIAL_KEYWORDS = ("银行", "保险", "证券", "信托", "金融")


def _safe(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _series(fins, key):
    return [f[key] for f in fins if f.get(key) is not None]


def _clamp(v, lo=0.0, hi=5.0):
    return max(lo, min(hi, v))


def _star(n):
    n = max(0, min(5, round(n)))
    return "★" * n + "☆" * (5 - n)


def _score_business(name, fins):
    """段永平：生意本质（好生意、差异化、定价权、轻资产代理）"""
    gross = _series(fins, "gross_margin")
    netm = _series(fins, "net_margin")
    roes = _series(fins, "roe")
    debt = _series(fins, "debt_ratio")
    revg = _series(fins, "rev_growth")
    npg = _series(fins, "profit_growth")
    financial = any(k in name for k in FINANCIAL_KEYWORDS)
    notes = []
    s = 2.5
    if gross:
        g = gross[-1]
        if g >= 40:
            s += 1.0
            notes.append(f"毛利率 {g:.1f}% 显示定价权")
        elif g >= 25:
            s += 0.5
            notes.append(f"毛利率 {g:.1f}% 尚可")
        else:
            notes.append(f"毛利率 {g:.1f}% 偏低，议价能力存疑")
    if netm:
        n = netm[-1]
        if n >= 10:
            s += 1.0
        elif n >= 5:
            s += 0.5
        else:
            s -= 0.5
        notes.append(f"净利率 {n:.1f}%")
    if roes:
        avg = sum(roes) / len(roes)
        if avg >= 15:
            s += 1.0
            notes.append(f"ROE均值 {avg:.1f}% 优秀")
        elif avg >= 10:
            s += 0.5
        else:
            s -= 0.5
    if debt and not financial:
        d = debt[-1]
        if d < 40:
            s += 0.5
        elif d > 60:
            s -= 0.5
        notes.append(f"负债率 {d:.1f}%")
    if revg and revg[-1] < -10:
        s -= 1.0
        notes.append("最新营收同比下滑超10%")
    if npg and npg[-1] < -10:
        s -= 1.0
        notes.append("最新净利同比下滑超10%")
    return round(_clamp(s), 1), notes or ["财务数据不足，无法给出生意本质判断"]


def _score_moat(name, fins, pe, pb, price, low52, high52):
    """巴菲特：护城河与财务质量（盈利质量 + 负债 + 估值安全边际代理）"""
    gross = _series(fins, "gross_margin")
    roes = _series(fins, "roe")
    debt = _series(fins, "debt_ratio")
    ocf = _series(fins, "ocf_ps")
    financial = any(k in name for k in FINANCIAL_KEYWORDS)
    notes = []
    s = 2.5
    if roes:
        avg = sum(roes) / len(roes)
        if avg >= 20:
            s += 1.0
        elif avg >= 10:
            s += 0.5
        elif avg < 8:
            s -= 1.0
        notes.append(f"ROE均值 {avg:.1f}%")
        if len(roes) >= 3:
            try:
                cv = statistics.pstdev(roes) / max(abs(sum(roes) / len(roes)), 0.01)
                if cv < 0.25:
                    s += 0.5
                    notes.append("ROE 稳定性较好")
                elif cv > 0.6:
                    s -= 0.5
                    notes.append("ROE 波动较大")
            except Exception:
                pass
    if gross:
        g = gross[-1]
        if g >= 40:
            s += 1.0
        elif g >= 20:
            s += 0.5
        notes.append(f"毛利率 {g:.1f}%")
    if debt and not financial:
        d = debt[-1]
        if d < 40:
            s += 1.0
        elif d <= 60:
            s += 0.5
        elif d > 70:
            s -= 1.0
        notes.append(f"负债率 {d:.1f}%")
    if ocf and ocf[-1] and ocf[-1] > 0:
        s += 0.5
        notes.append("每股经营现金流为正")
    pe_v = _safe(pe)
    if pe_v:
        if 0 < pe_v <= 25:
            s += 0.5
        elif pe_v > 60:
            s -= 1.0
            notes.append(f"PE {pe_v:.1f} 偏高，安全边际薄")
    if price and low52 and high52 and high52 > low52:
        pos = (price - low52) / (high52 - low52)
        if pos <= 0.5:
            s += 0.5
            notes.append(f"52周位置 {pos * 100:.0f}%，估值压力相对小")
        elif pos >= 0.9:
            s -= 0.5
            notes.append(f"52周位置 {pos * 100:.0f}%，注意追高风险")
    return round(_clamp(s), 1), notes or ["护城河数据代理不足"]


def _score_risk(name, fins, turnover, price, low52, high52):
    """芒格：逆向思考与风险（负债/亏损/拥挤度代理，分越高越安全）"""
    debt = _series(fins, "debt_ratio")
    revg = _series(fins, "rev_growth")
    npg = _series(fins, "profit_growth")
    financial = any(k in name for k in FINANCIAL_KEYWORDS)
    notes = []
    s = 3.0
    if "ST" in name.upper() or "退" in name:
        return 1.0, ["ST/退市风险，直接降级"]
    if debt and not financial:
        d = debt[-1]
        if d >= 70:
            s -= 1.5
            notes.append(f"负债率 {d:.1f}% 偏高")
        elif d >= 55:
            s -= 0.5
    if revg and revg[-1] < 0:
        s -= 1.0
        notes.append("最新营收同比负增长")
    if npg and npg[-1] < 0:
        s -= 1.0
        notes.append("最新净利同比负增长")
    if _safe(turnover) and _safe(turnover) >= 15:
        s -= 0.5
        notes.append(f"换手 {_safe(turnover):.1f}% 偏热，拥挤风险")
    if price and low52 and high52 and high52 > low52:
        pos = (price - low52) / (high52 - low52)
        if pos >= 0.95:
            s -= 0.5
            notes.append("52周接近最高，追高风险")
    return round(_clamp(s), 1), notes or ["未发现明显风险信号（数据代理）"]


def _score_trend(fins):
    """李录：行业与文明趋势（成长性与趋势代理，数据不足时低置信度）"""
    revg = _series(fins, "rev_growth")
    npg = _series(fins, "profit_growth")
    gross = _series(fins, "gross_margin")
    notes = []
    s = 2.5
    if len(revg) < 3 or len(npg) < 3:
        return 2.5, ["成长数据窗口不足（<3年），无法判断行业趋势，低置信度"]
    r_avg = sum(revg[-3:]) / 3
    n_avg = sum(npg[-3:]) / 3
    if r_avg >= 15:
        s += 1.0
    elif r_avg >= 5:
        s += 0.5
    elif r_avg < 0:
        s -= 1.0
    notes.append(f"近3年营收增速均值 {r_avg:.1f}%")
    if n_avg >= 15:
        s += 1.0
    elif n_avg >= 5:
        s += 0.5
    elif n_avg < 0:
        s -= 1.0
    notes.append(f"近3年净利增速均值 {n_avg:.1f}%")
    if len(gross) >= 3 and gross[-1] > sum(gross[:-1]) / len(gross[:-1]):
        s += 0.5
        notes.append("毛利率趋势向上")
    return round(_clamp(s), 1), notes


def master_scores(pack, quant=None):
    """输入 research_data 数据底稿（pack）+ 可选量化摘要（quant），返回四维评分。"""
    name = pack.get("name", "")
    fins = pack.get("financials") or []
    business, b_notes = _score_business(name, fins)
    moat, m_notes = _score_moat(name, fins, pack.get("pe"), pack.get("pb"),
                                pack.get("price"), pack.get("low_52w"),
                                pack.get("high_52w"))
    risk, r_notes = _score_risk(name, fins, pack.get("turnover_rate"),
                                pack.get("price"), pack.get("low_52w"),
                                pack.get("high_52w"))
    trend, t_notes = _score_trend(fins)
    overall = round((business + moat + risk + trend) / 4, 2)
    if overall >= 3.5:
        verdict = "值得深度研究"
    elif overall >= 2.5:
        verdict = "观察"
    else:
        verdict = "暂不关注"
    dims = [
        {"key": "生意本质", "master": "段永平", "score": business, "notes": b_notes},
        {"key": "护城河质量", "master": "巴菲特", "score": moat, "notes": m_notes},
        {"key": "风险逆向", "master": "芒格", "score": risk, "notes": r_notes},
        {"key": "行业趋势", "master": "李录", "score": trend, "notes": t_notes},
    ]
    return {
        "dimensions": dims,
        "overall": overall,
        "verdict": verdict,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "quant": quant,
        "warning": "规则化近似：用财务数据代理四大师框架，非真实定性判断；深度研究需按任务包由 Codex 执行。",
    }


def build_master_review_html(packs, quant_map=None):
    """生成每日报告“四大师定性速评”HTML 片段"""
    rows = []
    for p in packs:
        if not p:
            continue
        quant = (quant_map or {}).get(p.get("code"))
        m = master_scores(p, quant=quant)
        cells = "".join(
            f"<td>{_star(d['score'])} <span style='color:#6b7280'>{d['score']:.1f}</span>"
            f"<div class='hint'>{html.escape(d['notes'][0])}</div></td>"
            for d in m["dimensions"])
        rows.append(
            f"<tr><td>{html.escape(p['code'])}</td><td>{html.escape(p['name'])}</td>{cells}"
            f"<td><b>{m['overall']:.2f}</b></td>"
            f"<td><b>{html.escape(m['verdict'])}</b></td></tr>")
    if not rows:
        return ""
    return f"""
  <h2>四大师定性速评（规则化近似）</h2>
  <div class="sub">段永平·生意本质 / 巴菲特·护城河质量 / 芒格·风险逆向 / 李录·行业趋势，各 0~5 分（★）；由财务数据自动代理计算，<b>仅初筛参考，非真实定性判断</b>。综合 ≥3.5 值得深度研究，2.5~3.5 观察，&lt;2.5 暂不关注。</div>
  <div class="panel">
    <table><tr><th>代码</th><th>名称</th><th>生意本质<br>段永平</th><th>护城河质量<br>巴菲特</th><th>风险逆向<br>芒格</th><th>行业趋势<br>李录</th><th>综合</th><th>初步结论</th></tr>{''.join(rows)}</table>
    <div class="hint">深度定性分析：把该股研究任务包发给 Codex，按 investment-team + investment-research 执行。</div>
  </div>"""
