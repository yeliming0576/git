# -*- coding: utf-8 -*-
"""
紫苏叶瓶颈选股脚本（独立模块）
================================
按"紫苏叶逻辑 + bottleneck-hunter skill 模板"把研究底稿转成可复核的选股看板：

  1. 输入：bottleneck-hunter 产出的研究底稿 JSON（趋势确认/瓶颈环节/候选股/反证清单）
  2. 取数：只读复用项目 research_data（行情+财务+52周）与 market_snapshot（活跃池截面），
     失败自动用缓存；不写任何项目文件
  3. 打分：瓶颈评级 + 财务快照 + 估值红黄绿灯 + 10年25xPE退出年化粗算 + 信号强度★
  4. 输出：HTML 看板 + Markdown 报告 + JSON 结果，全部在 紫苏叶选股/输出/

用法：
  python bottleneck_picker.py <研究底稿.json> [--topic 主题] [--outdir 输出目录]
                                        [--export-watch 清单.txt]

重要声明：本脚本只做研究与筛选展示，不构成任何投资建议。
"""
import datetime
import html
import json
import os
import re
import statistics
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, BASE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_OUTDIR = os.path.join(BASE, "输出")


# ---------------- 输入解析 ----------------
def load_draft(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _slug(topic):
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", topic.strip())
    return s[:40] or "主题"


# ---------------- 瓶颈评级（6条判定） ----------------
def criteria_rating(criteria):
    """按 bottleneck-hunter 6 条标准：≥4红=S级，3红=A级，1-2红=B级，0红=非瓶颈"""
    c = criteria or {}
    reds = 0
    def _red(cond):
        nonlocal reds
        if cond:
            reds += 1
    try:
        _red(int(c.get("supply_concentration", 99)) <= 2)          # 供给集中度
        _red(int(c.get("expansion_months", 0)) > 24)               # 扩产周期
        _red(str(c.get("substitutability", "易")) == "低")         # 替代难度
        try:
            _red(float(c.get("utilization", 0)) > 0.9)             # 产能利用率
        except (TypeError, ValueError):
            pass
        try:
            _red(float(c.get("demand_growth", 0)) > 0.5)           # 需求增速
        except (TypeError, ValueError):
            pass
        _red(int(c.get("validation_months", 0)) > 12)              # 客户验证周期
    except (TypeError, ValueError):
        pass
    if reds >= 4:
        return "S", reds
    if reds == 3:
        return "A", reds
    if reds >= 1:
        return "B", reds
    return "B(非瓶颈，需重审)", reds


def count_reds(criteria):
    """安全统计6条判定中的红色项数（字段缺失不计）"""
    c = criteria or {}
    n = 0
    try:
        if int(c.get("supply_concentration", 99)) <= 2:
            n += 1
    except (TypeError, ValueError):
        pass
    try:
        if int(c.get("expansion_months", 0)) > 24:
            n += 1
    except (TypeError, ValueError):
        pass
    if str(c.get("substitutability", "易")) == "低":
        n += 1
    try:
        if float(c.get("utilization", 0)) > 0.9:
            n += 1
    except (TypeError, ValueError):
        pass
    try:
        if float(c.get("demand_growth", 0)) > 0.5:
            n += 1
    except (TypeError, ValueError):
        pass
    try:
        if int(c.get("validation_months", 0)) > 12:
            n += 1
    except (TypeError, ValueError):
        pass
    return n


# ---------------- 取数（只读复用项目模块） ----------------
def fetch_pack(code):
    """返回 (pack, cs)；失败返回 (None, None)，不抛错"""
    try:
        import research_data
        pack = research_data.get_pack(code)
    except Exception:
        pack = None
    try:
        import market_snapshot
        cs = market_snapshot.cross_sectional(code)
    except Exception:
        cs = None
    return pack, cs


def _series(fins, key):
    return [f[key] for f in fins if f.get(key) is not None]


def _latest(fins, key):
    s = _series(fins, key)
    return s[-1] if s else None


# ---------------- 估值与评分 ----------------
def valuate(pack):
    """估值红黄绿灯 + 10年25xPE退出年化粗算。返回 dict。"""
    fins = (pack or {}).get("financials") or []
    revenue_yi = _latest(fins, "revenue")
    revenue_yi = revenue_yi / 1e8 if revenue_yi else None
    net_profit_yi = _latest(fins, "net_profit")
    net_profit_yi = net_profit_yi / 1e8 if net_profit_yi else None
    growth = _latest(fins, "rev_growth")          # %
    cap_yi = (pack or {}).get("market_cap_yi")
    pe = (pack or {}).get("pe")
    losing = (net_profit_yi is not None and net_profit_yi <= 0) or (pe is not None and pe < 0)
    ps = (cap_yi / revenue_yi) if (cap_yi and revenue_yi) else None
    g = growth if growth is not None else 0.0
    rev5 = revenue_yi * ((1 + max(g, 0) / 100) ** 5) if revenue_yi else None
    tam_yi = None  # 由瓶颈层传入，见 evaluate_candidate

    reds, yellows, greens = [], [], []
    # 红灯
    if tam_yi and cap_yi and cap_yi > tam_yi * 0.20:
        reds.append(f"市值 {cap_yi:.0f}亿 > TAM 20%（{tam_yi * 0.20:.0f}亿）")
    if ps is not None and ps > 30 and g < 100:
        reds.append(f"PS {ps:.1f}x > 30x 且收入增速 {g:.1f}% < 100%")
    if rev5 and cap_yi and cap_yi > rev5 * 10:
        reds.append("市值 > 5年乐观收入（按当前增速粗算）的10倍")
    # 黄灯
    if losing and ps is not None and ps > 15:
        yellows.append(f"亏损 + PS {ps:.1f}x > 15x")
    if pe is not None and pe > 80:
        yellows.append(f"PE {pe:.1f} > 80x")
    # 绿灯
    if ps is not None and ps < 10 and g > 0:
        greens.append(f"PS {ps:.1f}x < 10x 且收入增长 {g:.1f}%")

    # 10年25xPE退出年化（仅盈利股可算）
    annual = None
    if net_profit_yi and net_profit_yi > 0 and cap_yi:
        target = net_profit_yi * 25
        if target > 0 and cap_yi > 0:
            annual = (target / cap_yi) ** (1 / 10) - 1

    if reds:
        light = "红灯"
    elif yellows:
        light = "黄灯"
    elif greens:
        light = "绿灯"
    else:
        light = "中性"
    return {
        "revenue_yi": revenue_yi, "net_profit_yi": net_profit_yi,
        "growth": growth, "cap_yi": cap_yi, "pe": pe, "ps": ps,
        "losing": losing, "rev5_yi": rev5, "light": light,
        "reds": reds, "yellows": yellows, "greens": greens,
        "annual_10y": annual,
    }


def strength_of(rating, verified, val):
    """信号强度★1~5：瓶颈评级 + 交叉验证 + 估值灯修正；估值红灯封顶★★"""
    base = {"S": 3, "A": 2, "B": 1}.get(str(rating)[0], 1)
    n_ok = sum(1 for v in (verified or {}).values() if v)
    if n_ok >= 3:
        base += 1
    elif n_ok == 2:
        base += 0.5
    if val["light"] == "绿灯":
        base += 1
    elif val["light"] == "黄灯":
        base -= 0.5
    if val["light"] == "红灯":
        base = min(base, 2)          # 估值红灯封顶★★
    return max(1, min(5, round(base)))


def verdict_of(strength, val, has_data):
    if not has_data:
        return "数据不足，待验证"
    if val["light"] == "红灯":
        return "⚠估值透支（暂不追踪）"
    if strength >= 4:
        return "值得深入研究"
    if strength >= 3:
        return "加入观察"
    return "暂不追踪"


def fin_snapshot(pack):
    fins = (pack or {}).get("financials") or []
    roes = _series(fins, "roe")
    return {
        "roe_avg": round(sum(roes) / len(roes), 2) if roes else None,
        "gross": _latest(fins, "gross_margin"),
        "net_margin": _latest(fins, "net_margin"),
        "debt": _latest(fins, "debt_ratio"),
        "rev_growth": _latest(fins, "rev_growth"),
    }


def evaluate_candidate(link, rating, tam_yi, cand, pack, cs):
    val = valuate(pack)
    val["tam_yi"] = tam_yi
    # 带 TAM 后重算红灯（市值>TAM20%）
    if tam_yi and val["cap_yi"] and val["cap_yi"] > tam_yi * 0.20:
        if "市值" not in "".join(val["reds"]):
            val["reds"].insert(0, f"市值 {val['cap_yi']:.0f}亿 > TAM 20%（{tam_yi * 0.20:.0f}亿）")
            val["light"] = "红灯"
    verified = cand.get("verified") or {}
    strength = strength_of(rating, verified, val)
    has_data = pack is not None and bool(pack.get("financials"))
    if not has_data:
        strength = min(strength, 2)   # 无财务数据时信号强度不得超过★★
    return {
        "code": cand.get("code", ""),
        "name": cand.get("name", ""),
        "link": link,
        "rating": rating,
        "share_mkt": cand.get("share_mkt", ""),
        "revenue_ratio": cand.get("revenue_ratio"),
        "verified": verified,
        "catalyst": cand.get("catalyst", ""),
        "risks": cand.get("risks", []),
        "notes": cand.get("notes", ""),
        "pack": pack,
        "cs": cs,
        "fin": fin_snapshot(pack),
        "val": val,
        "strength": strength,
        "verdict": verdict_of(strength, val, has_data),
    }


# ---------------- 格式化 ----------------
def _f(v, nd=2, suffix=""):
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v):
    return _f(v, 2, "%")


def _stars(n):
    n = max(0, min(5, int(n)))
    return "★" * n + "☆" * (5 - n)


def _light_badge(light):
    color = {"红灯": "#dc2626", "黄灯": "#d97706", "绿灯": "#059669", "中性": "#6b7280"}.get(light, "#6b7280")
    return f"<span style='color:{color};font-weight:700'>{light}</span>"


def _verified_md(verified):
    if not verified:
        return "—"
    labels = {"customer": "客户", "revenue": "收入", "capacity": "产能", "price": "价格"}
    return "、".join(f"{labels.get(k, k)}:✅" if v else f"{labels.get(k, k)}:❌"
                     for k, v in verified.items())


# ---------------- 报告生成 ----------------
def build_html(draft, results, bottlenecks, generated):
    topic = html.escape(draft.get("topic", ""))
    trend = draft.get("trend") or {}
    trend_rows = "".join(
        f"<tr><td>{html.escape(x)}</td></tr>" for x in trend.get("verified_events", [])) \
        or "<tr><td>—</td></tr>"
    bn_rows = "".join(
        f"<tr><td>{html.escape(b['link'])}</td><td>{html.escape(b.get('layer',''))}</td>"
        f"<td><b>{html.escape(b['rating'])}</b></td><td>{b.get('criteria_reds','')}</td>"
        f"<td>{html.escape(b.get('tam_desc',''))}</td><td>{len(b.get('candidates',[]))}</td></tr>"
        for b in bottlenecks) or "<tr><td colspan='6'>—</td></tr>"
    rank_rows = "".join(
        f"<tr><td>{i}</td><td>{html.escape(r['name'])}</td><td>{html.escape(r['code'])}</td>"
        f"<td>{_f(r['val']['cap_yi'],0)}</td><td>{_f(r['val']['revenue_yi'],0)}</td>"
        f"<td>{_f(r['val']['ps'],1)}x</td><td>{_f(r['val']['pe'],1)}x</td>"
        f"<td>{html.escape(r['link'])}</td><td>{html.escape(r['rating'])}</td>"
        f"<td>{html.escape(r['share_mkt'][:20])}</td><td>{_pct(r['val']['growth'])}</td>"
        f"<td>{_stars(r['strength'])}</td><td>{_light_badge(r['val']['light'])}</td></tr>"
        for i, r in enumerate(results, 1)) or "<tr><td colspan='13'>无候选</td></tr>"
    cards = ""
    for r in results:
        val = r["val"]
        fin = r["fin"]
        cache_tag = "（缓存数据）" if (r["pack"] or {}).get("from_cache") else ""
        cs_txt = ""
        if r["cs"]:
            cs_txt = (f"活跃池截面：60日动量 {_f(r['cs'].get('mom60_pct'),0)}分位 / "
                      f"当日涨幅 {_f(r['cs'].get('chg_pct'),0)}分位 / "
                      f"成交额 {_f(r['cs'].get('amount_pct'),0)}分位")
        verified_txt = "、".join(
            f"{k}:{'✅' if v else '❌'}" for k, v in r["verified"].items()) or "—"
        annual = _f(val["annual_10y"] * 100, 1, "%") if val["annual_10y"] is not None else \
            ("不可算（亏损）" if val["losing"] else "—")
        reds = "；".join(val["reds"]) or "无"
        yellows = "；".join(val["yellows"]) or "无"
        greens = "；".join(val["greens"]) or "无"
        cards += f"""
  <div class="card">
    <h3>{html.escape(r['name'])}（{html.escape(r['code'])}）— {html.escape(r['link'])} <span class="tag">{html.escape(r['rating'])}级瓶颈</span></h3>
    <p><b>一句话定位</b>：{html.escape(r['share_mkt'] or '—')}</p>
    <p><b>为什么是瓶颈</b>：{html.escape(r['notes'] or '见研究底稿')} {cache_tag}</p>
    <p><b>催化剂</b>：{html.escape(r['catalyst'] or '—')}</p>
    <p><b>主要风险</b>：{html.escape('；'.join(r['risks']) or '—')}</p>
    <table>
      <tr><th>市值(亿)</th><th>年收入(亿)</th><th>PS</th><th>PE</th><th>收入增速</th><th>ROE均值</th><th>毛利率</th><th>净利率</th><th>负债率</th></tr>
      <tr><td>{_f(val['cap_yi'],0)}</td><td>{_f(val['revenue_yi'],0)}</td><td>{_f(val['ps'],1)}x</td>
          <td>{_f(val['pe'],1)}x</td><td>{_pct(val['growth'])}</td><td>{_pct(fin['roe_avg'])}</td>
          <td>{_pct(fin['gross'])}</td><td>{_pct(fin['net_margin'])}</td><td>{_pct(fin['debt'])}</td></tr>
    </table>
    <p><b>估值检查</b>：{_light_badge(val['light'])} ｜ 红灯：{html.escape(reds)} ｜ 黄灯：{html.escape(yellows)} ｜ 绿灯：{html.escape(greens)}</p>
    <p><b>安全边际粗算</b>：10年25xPE退出年化 ≈ {annual} ｜ 市值验算：{html.escape(((r['pack'] or {}).get('verify') or {}).get('note','—'))}</p>
    <p><b>交叉验证</b>：{html.escape(verified_txt)} ｜ {html.escape(cs_txt)}</p>
    <p><b>结论</b>：<b>{html.escape(r['verdict'])}</b> ｜ 信号强度 {_stars(r['strength'])}（{r['strength']}/5）</p>
  </div>"""
    rebuttals = "".join(f"<li>{html.escape(x)}</li>" for x in draft.get("rebuttals", [])) \
        or "<li>—</li>"
    deep = "、".join(r["code"] for r in results if r["verdict"] == "值得深入研究") or "无"
    watch = "、".join(r["code"] for r in results if r["verdict"] == "加入观察") or "无"
    drop = "、".join(r["code"] for r in results if "暂不" in r["verdict"]) or "无"
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>紫苏叶瓶颈机会看板：{html.escape(draft.get('topic',''))}</title>
<style>
body{{background:#f5f6f8;color:#1f2937;font-family:"Microsoft YaHei",sans-serif;padding:28px;}}
.wrap{{max-width:1180px;margin:0 auto;}}
.topbar{{display:flex;gap:10px;align-items:center;margin-bottom:14px;}}
.backbtn{{background:#fff;color:#2563eb;border:1px solid #bfdbfe;border-radius:999px;padding:8px 18px;text-decoration:none;font-size:14px;font-weight:600;}}
.backbtn:hover{{background:#eff6ff;}}
h1{{font-size:24px;}} h2{{font-size:18px;color:#2563eb;border-left:3px solid #2563eb;padding-left:8px;margin:26px 0 6px;}}
h3{{font-size:16px;margin:0 0 8px;}}
.sub{{color:#6b7280;font-size:13px;margin:6px 0 12px;line-height:1.7;}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;margin:10px 0 16px;font-size:13px;}}
th,td{{padding:8px 10px;border-bottom:1px solid #eef1f6;text-align:right;white-space:nowrap;}}
th{{background:#f1f5f9;}} td:first-child,th:first-child{{text-align:left;}}
.card{{background:#fff;border:1px solid #e6e9ef;border-radius:14px;padding:16px 20px;margin:14px 0;}}
.card p{{font-size:13px;color:#374151;line-height:1.8;margin:6px 0;}}
.tag{{display:inline-block;background:#eff6ff;color:#2563eb;border:1px solid #bfdbfe;border-radius:999px;padding:2px 10px;font-size:12px;}}
.warn{{background:#fffbeb;border:1px solid #fde68a;border-radius:12px;padding:12px 16px;font-size:13px;color:#92400e;line-height:1.8;}}
li{{font-size:13px;line-height:1.9;}}
</style></head><body><div class="wrap">
<div class="topbar">
  <a class="backbtn" href="/">← 返回主报告</a>
  <span class="sub">紫苏叶瓶颈机会看板（独立模块）</span>
</div>
<h1>紫苏叶瓶颈机会看板：{html.escape(draft.get('topic',''))}</h1>
<div class="sub">生成时间 {generated} ｜ 底稿日期 {html.escape(str(draft.get('date','')))} ｜ 仅供产业链研究参考，不构成投资建议</div>

<h2>一、超级趋势确认</h2>
<div class="card"><p><b>趋势</b>：{html.escape(trend.get('name','—'))}</p>
<p><b>资本开支规模</b>：{html.escape(trend.get('capex_scale','—'))}</p>
<p><b>已发生的验证事件</b>：</p><ul>{trend_rows}</ul>
<p><b>结论</b>：{html.escape(trend.get('conclusion','—'))}</p></div>

<h2>二、瓶颈地图</h2>
<table><tr><th>瓶颈环节</th><th>层级</th><th>评级</th><th>判定(红项数)</th><th>TAM</th><th>候选数</th></tr>{bn_rows}</table>

<h2>三、瓶颈机会排名表</h2>
<table><tr><th>排名</th><th>公司</th><th>代码</th><th>市值(亿)</th><th>年收入(亿)</th><th>PS</th><th>PE</th><th>瓶颈环节</th><th>瓶颈评级</th><th>市场份额</th><th>收入增速</th><th>信号强度</th><th>估值判断</th></tr>{rank_rows}</table>

<h2>四、标的一页纸</h2>
{cards}

<h2>五、反证清单</h2>
<div class="card"><ul>{rebuttals}</ul></div>

<h2>六、行动建议</h2>
<div class="card"><p><b>值得深入研究</b>：{html.escape(deep)}</p>
<p><b>加入观察</b>：{html.escape(watch)}</p>
<p><b>暂不追踪/估值透支</b>：{html.escape(drop)}</p>
<p class="sub">候选代码可导出后手动加入项目自选股：--export-watch 清单.txt</p></div>

<h2>七、AI 研究偏见声明与数据说明</h2>
<div class="warn">瓶颈真实性（供给集中度/客户名单/扩产周期等）来自研究底稿，需用公告、认证、客户验证持续核验，禁止以 K 线或热度倒推瓶颈。财务与行情来自东财/腾讯免费接口（单源未双源核验），估值红灯为硬门槛。紫苏叶逻辑存在幸存者偏差与伪瓶颈风险，本看板不是买入建议。</div>
</div></body></html>"""


def build_md(draft, results, bottlenecks, generated):
    L = []
    A = L.append
    A(f"# 紫苏叶瓶颈机会看板：{draft.get('topic','')}")
    A(f"> 生成时间 {generated} ｜ 底稿日期 {draft.get('date','')} ｜ 仅供产业链研究参考，不构成投资建议")
    A("")
    trend = draft.get("trend") or {}
    A("## 一、超级趋势确认")
    A(f"- 趋势：{trend.get('name','—')}")
    A(f"- 资本开支规模：{trend.get('capex_scale','—')}")
    A("- 已发生的验证事件：")
    for x in trend.get("verified_events", []):
        A(f"  - {x}")
    A(f"- 结论：{trend.get('conclusion','—')}")
    A("")
    A("## 二、瓶颈地图")
    A("| 瓶颈环节 | 层级 | 评级 | 判定(红项数) | TAM | 候选数 |")
    A("|---|---|---|---|---|---|")
    for b in bottlenecks:
        A(f"| {b['link']} | {b.get('layer','—')} | {b['rating']} | {b.get('criteria_reds','—')} "
          f"| {b.get('tam_desc','—')} | {len(b.get('candidates',[]))} |")
    A("")
    A("## 三、瓶颈机会排名表")
    A("| 排名 | 公司 | 代码 | 市值(亿) | 年收入(亿) | PS | PE | 瓶颈环节 | 瓶颈评级 | 市场份额 | 收入增速 | 信号强度 | 估值判断 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(results, 1):
        v = r["val"]
        A(f"| {i} | {r['name']} | {r['code']} | {_f(v['cap_yi'],0)} | {_f(v['revenue_yi'],0)} "
          f"| {_f(v['ps'],1)}x | {_f(v['pe'],1)}x | {r['link']} | {r['rating']} "
          f"| {r['share_mkt'][:20]} | {_pct(v['growth'])} | {_stars(r['strength'])} | {v['light']} |")
    A("")
    A("## 四、标的一页纸")
    for r in results:
        v = r["val"]
        fin = r["fin"]
        annual = _f(v["annual_10y"] * 100, 1, "%") if v["annual_10y"] is not None else \
            ("不可算（亏损）" if v["losing"] else "—")
        cache_tag = "（缓存数据）" if (r["pack"] or {}).get("from_cache") else ""
        cs_txt = ""
        if r["cs"]:
            cs_txt = (f"活跃池截面：60日动量{_f(r['cs'].get('mom60_pct'),0)}分位 / "
                      f"当日涨幅{_f(r['cs'].get('chg_pct'),0)}分位 / "
                      f"成交额{_f(r['cs'].get('amount_pct'),0)}分位")
        verified_txt = "、".join(f"{k}:{'✅' if vv else '❌'}" for k, vv in r["verified"].items()) or "—"
        A(f"### {r['name']}（{r['code']}）— {r['link']} [{r['rating']}级瓶颈]{cache_tag}")
        A(f"- 一句话定位：{r['share_mkt'] or '—'}")
        A(f"- 为什么是瓶颈：{r['notes'] or '见研究底稿'}")
        A(f"- 催化剂：{r['catalyst'] or '—'}")
        A(f"- 主要风险：{'；'.join(r['risks']) or '—'}")
        A(f"- 关键数据：市值 {_f(v['cap_yi'],0)}亿 / 年收入 {_f(v['revenue_yi'],0)}亿 / "
          f"PS {_f(v['ps'],1)}x / PE {_f(v['pe'],1)}x / 收入增速 {_pct(v['growth'])} / "
          f"ROE均值 {_pct(fin['roe_avg'])} / 毛利率 {_pct(fin['gross'])} / "
          f"净利率 {_pct(fin['net_margin'])} / 负债率 {_pct(fin['debt'])}")
        A(f"- 估值检查：{v['light']}（红灯：{'；'.join(v['reds']) or '无'}；黄灯：{'；'.join(v['yellows']) or '无'}；绿灯：{'；'.join(v['greens']) or '无'}）")
        A(f"- 安全边际检验：10年25xPE退出年化 ≈ {annual}；市值验算：{((r['pack'] or {}).get('verify') or {}).get('note','—')}")
        A(f"- 交叉验证状态：{verified_txt}；{cs_txt}")
        A(f"- **结论**：{r['verdict']} ｜ 信号强度 {_stars(r['strength'])}（{r['strength']}/5）")
        A("")
    A("## 五、反证清单")
    for x in draft.get("rebuttals", []):
        A(f"- {x}")
    A("")
    A("## 六、行动建议")
    A(f"- 值得深入研究：{'、'.join(r['code'] for r in results if r['verdict'] == '值得深入研究') or '无'}")
    A(f"- 加入观察：{'、'.join(r['code'] for r in results if r['verdict'] == '加入观察') or '无'}")
    A(f"- 暂不追踪/估值透支：{'、'.join(r['code'] for r in results if '暂不' in r['verdict']) or '无'}")
    A("")
    A("## 七、AI 研究偏见声明与数据说明")
    A("- 瓶颈真实性来自研究底稿，需公告/认证/客户验证持续核验，禁止以K线或热度倒推瓶颈")
    A("- 财务与行情来自东财/腾讯免费接口（单源未双源核验）")
    A("- 估值红灯为硬门槛；紫苏叶逻辑存在幸存者偏差与伪瓶颈风险")
    A("- 本看板不构成投资建议")
    return "\n".join(L)


# ---------------- 主流程 ----------------
def main(argv):
    if len(argv) < 2:
        print("用法: python bottleneck_picker.py <研究底稿.json> "
              "[--topic 主题] [--outdir 目录] [--export-watch 清单.txt]")
        return 1
    draft = load_draft(argv[1])
    topic = None
    outdir = DEFAULT_OUTDIR
    export_watch = None
    i = 2
    while i < len(argv):
        if argv[i] == "--topic" and i + 1 < len(argv):
            topic = argv[i + 1]
            i += 2
        elif argv[i] == "--outdir" and i + 1 < len(argv):
            outdir = argv[i + 1]
            i += 2
        elif argv[i] == "--export-watch" and i + 1 < len(argv):
            export_watch = argv[i + 1]
            i += 2
        else:
            i += 1
    topic = topic or draft.get("topic", "主题")
    os.makedirs(outdir, exist_ok=True)
    slug = _slug(topic)
    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    date8 = datetime.date.today().strftime("%Y%m%d")

    bottlenecks = []
    results = []
    for b in draft.get("bottlenecks", []):
        criteria = b.get("criteria") or {}
        rating = b.get("rating") or criteria_rating(criteria)[0]
        reds_n = count_reds(criteria)
        tam_yi = b.get("tam_yi")
        tam_desc = f"{tam_yi:.0f}亿" if tam_yi else "—"
        for cand in b.get("candidates", []):
            pack, cs = fetch_pack(cand.get("code", ""))
            r = evaluate_candidate(b["link"], rating, tam_yi, cand, pack, cs)
            results.append(r)
        bottlenecks.append({
            "link": b["link"], "layer": b.get("layer", ""), "rating": rating,
            "criteria_reds": f"{reds_n}/6", "tam_desc": tam_desc,
            "candidates": b.get("candidates", []),
        })

    results.sort(key=lambda r: (-r["strength"], {"S": 0, "A": 1, "B": 2}.get(str(r["rating"])[0], 3)))
    html_text = build_html(draft, results, bottlenecks, generated)
    md_text = build_md(draft, results, bottlenecks, generated)
    json_out = {
        "topic": topic, "generated_at": generated,
        "results": [{k: v for k, v in r.items() if k not in ("pack", "cs")} for r in results],
    }
    for fname, text in ((f"{slug}_瓶颈机会看板_{date8}.html", html_text),
                        (f"{slug}_瓶颈机会看板_{date8}.md", md_text),
                        (f"{slug}_结果_{date8}.json", json.dumps(json_out, ensure_ascii=False, indent=1))):
        with open(os.path.join(outdir, fname), "w", encoding="utf-8") as f:
            f.write(text)
        print("已生成:", os.path.join(outdir, fname))
    print("\n候选清单：")
    for r in results:
        print(f"  {r['code']} {r['name']} [{r['rating']}] {r['strength']}★ {r['verdict']}")
    if export_watch:
        with open(export_watch, "w", encoding="utf-8") as f:
            f.write("\n".join(r["code"] for r in results) + "\n")
        print("已导出自选清单:", export_watch)
        print("提示：核对后再手动加入项目 自选股.txt（本脚本不直接修改它）。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
