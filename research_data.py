# -*- coding: utf-8 -*-
"""
研究数据层（ai-berkshire Skill 融合）
====================================
职责：
  1. A 股基本面采集：腾讯行情（现价/市值/PE/PB）+ 东方财富 F10 主要财务指标
     （近 5 年年报：营收/归母净利/EPS/BPS/ROE/增速/毛利率/净利率/负债率）+ 52 周高低
  2. 输出三样东西：
     - JSON 数据底稿（报告归档/研究/<代码>/数据底稿_*.json）
     - Markdown 研究任务包（供 Codex 按 investment-team + investment-research 执行）
     - HTML 基本面速评片段（每日报告嵌入用）
  3. 缓存：SQLite 表 fundamental_snapshots，同一天不重复抓取；失败降级读缓存
  4. 市值验算：股价×总股本 vs 报告市值，偏差>5% 标注

用法：
  python research_data.py 600519            # 生成任务包并打印
  python research_data.py 600519 --json     # 只输出 JSON 数据底稿
  python research_data.py 600519 --quick    # 只输出 HTML 速评片段
"""
import datetime
import html
import json
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_EVEN

import requests

import db

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

db.init_db()

BASE = os.path.dirname(os.path.abspath(__file__))
RESEARCH_DIR = os.path.join(BASE, "报告归档", "研究")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TIMEOUT = 15

# 东财 F10 主要财务指标字段映射（RPT_F10_FINANCE_MAINFINADATA）
FIN_FIELDS = {
    "revenue": "TOTALOPERATEREVE",          # 营业总收入
    "rev_growth": "TOTALOPERATEREVETZ",     # 营收同比
    "net_profit": "PARENTNETPROFIT",        # 归母净利润
    "profit_growth": "PARENTNETPROFITTZ",   # 净利润同比
    "eps": "EPSJB",                          # 基本每股收益
    "bps": "BPS",                            # 每股净资产
    "roe": "ROEJQ",                          # 加权 ROE
    "gross_margin": "XSMLL",                 # 销售毛利率
    "net_margin": "XSJLL",                   # 销售净利率
    "debt_ratio": "ZCFZL",                   # 资产负债率
    "ocf_ps": "MGJYXJJE",                    # 每股经营现金流
}

FINANCIAL_KEYWORDS = ("银行", "保险", "证券", "信托", "金融")


# ---------------- 基础工具 ----------------
def _market_prefix(code):
    code = code.strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if code.startswith(("6", "9", "5")):
        return "SH", "sh"
    if code.startswith(("4", "8")):
        return "BJ", "bj"
    return "SZ", "sz"


def _fmt(v, nd=2):
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 1e8:
        return f"{f / 1e8:.2f}亿"
    if abs(f) >= 1e4:
        return f"{f / 1e4:.2f}万"
    return f"{f:.{nd}f}"


def _fmt_pct(v):
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return str(v)


# ---------------- 数据抓取 ----------------
def fetch_quote(code):
    """腾讯实时行情：现价/名称/总市值(亿)/流通市值(亿)/PE/PB/换手/成交额/涨跌幅"""
    market, prefix = _market_prefix(code)
    raw = requests.get(f"https://qt.gtimg.cn/q={prefix}{code}", headers=HEADERS,
                       timeout=TIMEOUT).content
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    start, end = text.find('"'), text.rfind('"')
    if start < 0 or end <= start:
        raise RuntimeError(f"腾讯行情解析失败: {code}")
    fields = text[start + 1:end].split("~")
    if len(fields) < 46:
        raise RuntimeError(f"腾讯行情字段不足: {code}")

    def _f(idx):
        try:
            return float(fields[idx])
        except (ValueError, IndexError):
            return None

    price = _f(3)
    if not price or price <= 0:
        raise RuntimeError(f"腾讯行情无有效价格: {code}")
    return {
        "code": code,
        "name": fields[1] or code,
        "price": price,
        "change_pct": _f(32),
        "amount_yi": _f(37) / 1e4 if _f(37) is not None else None,   # 成交额(万元)->亿
        "turnover_rate": _f(38),
        "pe": _f(39),
        "float_cap_yi": _f(44),
        "market_cap_yi": _f(45),
        "pb": _f(46),
    }


def fetch_52w(code):
    """东财 52 周最高/最低（push2delay 优先，失败回退 push2）"""
    market, _ = _market_prefix(code)
    secid = f"1.{code}" if market == "SH" else f"0.{code}"
    query = (f"api/qt/stock/get?secid={secid}&fields=f174,f175&invt=2&fltt=2")
    for host in ("push2delay.eastmoney.com", "push2.eastmoney.com"):
        try:
            r = requests.get(f"https://{host}/{query}", headers=HEADERS, timeout=TIMEOUT)
            data = (r.json() or {}).get("data") or {}
            high, low = data.get("f174"), data.get("f175")
            if high not in (None, "-", "") and low not in (None, "-", ""):
                return float(high), float(low)
        except Exception:
            continue
    return None, None


def fetch_financials(code):
    """东财 datacenter 主要财务指标（年报，最多 5 年）"""
    market, _ = _market_prefix(code)
    url = "https://datacenter.eastmoney.com/securities/api/data/get"
    params = {
        "type": "RPT_F10_FINANCE_MAINFINADATA",
        "sty": "ALL",
        "filter": f'(SECUCODE="{code}.{market}")(REPORT_TYPE="年报")',
        "p": "1", "ps": "5", "sr": "-1", "st": "REPORT_DATE",
        "source": "HSF10", "client": "PC",
    }
    reports = []
    try:
        data = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT).json()
        reports = (data.get("result") or {}).get("data") or []
    except Exception:
        reports = []
    if not reports:
        params["filter"] = f'(SECUCODE="{code}.{market}")'
        try:
            data = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT).json()
            reports = (data.get("result") or {}).get("data") or []
        except Exception:
            reports = []
    out = []
    for r in reports or []:
        row = {"report_date": (r.get("REPORT_DATE") or "")[:10],
               "report_name": r.get("REPORT_DATE_NAME") or ""}
        for key, field in FIN_FIELDS.items():
            v = r.get(field)
            row[key] = float(v) if v not in (None, "-", "") else None
        out.append(row)
    return out


# ---------------- 市值验算 ----------------
def verify_market_cap(price, market_cap_yi):
    """股价×推算总股本 vs 报告总市值，返回验算记录"""
    if not price or not market_cap_yi:
        return {"ok": None, "note": "缺少价格或市值，无法验算"}
    try:
        p = Decimal(str(price))
        cap = Decimal(str(market_cap_yi)) * Decimal("1e8")
        shares = cap / p
        calc = p * shares
        deviation = abs(float(calc - cap) / float(cap)) * 100
        return {
            "ok": deviation <= 5,
            "calculated_cap_yi": round(float(calc) / 1e8, 2),
            "reported_cap_yi": market_cap_yi,
            "derived_shares": round(float(shares), 0),
            "deviation_pct": round(deviation, 2),
            "note": "偏差≤5% ✅" if deviation <= 5 else f"偏差 {deviation:.2f}% ⚠️ 需核对",
        }
    except Exception as e:
        return {"ok": None, "note": f"验算失败: {e}"}


# ---------------- 数据底稿 ----------------
def build_pack(code, force=False):
    """生成某股票基本面数据底稿（dict）；优先读当天缓存"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    cached = db.load_fundamental_snapshot(code, today)
    if cached and not force:
        cached = dict(cached)
        cached["from_cache"] = True
        return cached

    quote = fetch_quote(code)
    fins = fetch_financials(code)
    high52, low52 = fetch_52w(code)
    verify = verify_market_cap(quote["price"], quote.get("market_cap_yi"))
    pack = {
        "code": code,
        "name": quote["name"],
        "price": quote["price"],
        "change_pct": quote["change_pct"],
        "amount_yi": quote["amount_yi"],
        "turnover_rate": quote["turnover_rate"],
        "pe": quote["pe"],
        "pb": quote["pb"],
        "market_cap_yi": quote["market_cap_yi"],
        "float_cap_yi": quote["float_cap_yi"],
        "high_52w": high52,
        "low_52w": low52,
        "verify": verify,
        "financials": fins,
        "report_date": today,
        "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from_cache": False,
        "notes": [],
    }
    if not fins:
        pack["notes"].append("东财主要财务指标暂不可用，深度研究时需按 financial-data 规范双源补数")
    try:
        db.save_fundamental_snapshot(code, today, pack)
    except Exception:
        pass
    _write_pack_files(pack)
    return pack


def get_pack(code, force=False):
    """取数入口：优先缓存，缓存缺失才联网；联网失败且无缓存时抛错"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    cached = db.load_fundamental_snapshot(code, today)
    if cached and not force:
        cached = dict(cached)
        cached["from_cache"] = True
        return cached
    try:
        return build_pack(code, force=force)
    except Exception as e:
        latest = db.load_fundamental_snapshot(code)
        if latest:
            latest = dict(latest)
            latest["from_cache"] = True
            latest["notes"] = list(latest.get("notes") or []) + [f"联网刷新失败，使用最近缓存（{e}）"]
            return latest
        raise


def _write_pack_files(pack):
    """把数据底稿写入 报告归档/研究/<代码>/（仅 JSON；任务包由 CLI/网页生成）"""
    try:
        d = os.path.join(RESEARCH_DIR, pack["code"])
        os.makedirs(d, exist_ok=True)
        date8 = datetime.date.today().strftime("%Y%m%d")
        with open(os.path.join(d, f"数据底稿_{pack['code']}_{date8}.json"),
                  "w", encoding="utf-8") as f:
            json.dump(pack, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# ---------------- 基本面速评 ----------------
def quality_verdict(pack):
    """按 quality-screen 7 条指标的可计算子集做初筛，返回 (结论, 明细列表)"""
    fins = pack.get("financials") or []
    name = pack.get("name", "")
    financial = any(k in name for k in FINANCIAL_KEYWORDS)
    roes = [f["roe"] for f in fins if f.get("roe") is not None]
    gross = [f["gross_margin"] for f in fins if f.get("gross_margin") is not None]
    netm = [f["net_margin"] for f in fins if f.get("net_margin") is not None]
    debt = [f["debt_ratio"] for f in fins if f.get("debt_ratio") is not None]

    items = []
    if roes:
        avg = sum(roes) / len(roes)
        items.append({"label": "ROE均值", "value": f"{avg:.2f}%",
                      "std": "≥8%", "pass": avg >= 8,
                      "note": f"可用{len(roes)}年年报"})
    if gross:
        v = gross[-1]
        items.append({"label": "最新毛利率", "value": f"{v:.2f}%",
                      "std": "≥15%", "pass": v >= 15, "note": "年报口径"})
    if netm:
        v = netm[-1]
        items.append({"label": "最新净利率", "value": f"{v:.2f}%",
                      "std": "≥5%", "pass": v >= 5, "note": "年报口径"})
    if debt and not financial:
        v = debt[-1]
        items.append({"label": "最新负债率", "value": f"{v:.2f}%",
                      "std": "<60%", "pass": v < 60, "note": "金融行业豁免"})
    elif financial:
        items.append({"label": "最新负债率", "value": "金融豁免", "std": "不适用",
                      "pass": None, "note": "银行/保险/证券等行业不适用"})
    for key, label, std in (("ocf_ps", "每股经营现金流", "可用年报数"),
                            ("interest", "利息覆盖", "金融豁免"),
                            ("fcf5", "5年FCF", "需现金流量表"),
                            ("dilution", "5年股本膨胀", "需股本历史")):
        items.append({"label": label, "value": "—", "std": std,
                      "pass": None, "note": "暂无法获取，深度研究时补齐"})
    if not roes and not gross and not netm:
        return "⚠️ 数据不足", items
    assessable = [i for i in items if i["pass"] is not None]
    if assessable and all(i["pass"] for i in assessable):
        return "✅ 初筛通过", items
    if assessable:
        return "❌ 有指标不达标", items
    return "⚠️ 数据不足", items


def build_quick_review_html(packs):
    """生成“基本面速评”HTML 片段（嵌入每日报告总览页）"""
    rows = []
    for p in packs:
        if not p:
            continue
        verdict, items = quality_verdict(p)
        pe = f"{p['pe']:.2f}" if p.get("pe") else "—"
        pb = f"{p['pb']:.2f}" if p.get("pb") else "—"
        pos = "—"
        if p.get("high_52w") and p.get("low_52w") and p.get("price"):
            rng = p["high_52w"] - p["low_52w"]
            if rng > 0:
                pos = f"{(p['price'] - p['low_52w']) / rng * 100:.0f}%"
        def cell(it):
            mark = {"pass": "✅", "no": "❌", "unknown": "•"}[
                "pass" if it["pass"] is True else ("no" if it["pass"] is False else "unknown")]
            return f"{mark} {it['value']}"
        rows.append(
            f"<tr><td>{html.escape(p['code'])}</td><td>{html.escape(p['name'])}</td>"
            f"<td>{cell(items[0]) if items else '—'}</td>"
            f"<td>{cell(items[1]) if len(items) > 1 else '—'}</td>"
            f"<td>{cell(items[2]) if len(items) > 2 else '—'}</td>"
            f"<td>{cell(items[3]) if len(items) > 3 else '—'}</td>"
            f"<td>{pe}</td><td>{pb}</td><td>{pos}</td>"
            f"<td><b>{html.escape(verdict)}</b></td></tr>")
    if not rows:
        return ""
    return f"""
  <h2>基本面速评（自动初筛）</h2>
  <div class="sub">按 quality-screen 规则自动计算：ROE均值 / 毛利率 / 净利率 / 负债率（金融行业豁免），PE/PB 与 52 周位置仅展示不判好坏。数据来自东财+腾讯，单源未经双源核验；<b>仅初筛，非投资结论</b>，深度研究请用“深度研究”入口。</div>
  <div class="panel">
    <table><tr><th>代码</th><th>名称</th><th>ROE均值</th><th>毛利率</th><th>净利率</th><th>负债率</th><th>PE</th><th>PB</th><th>52周位置</th><th>初筛结论</th></tr>{''.join(rows)}</table>
    <div class="hint">• = 该指标暂无法从免费接口获取（需现金流量表/股本历史），深度研究时按 financial-data 规范补齐；同一天数据自动缓存，不重复抓取。</div>
  </div>"""


# ---------------- 研究任务包 ----------------
def _quant_summary(code):
    """复用项目已有量化分析：趋势状态 / L3 评分 / 回测总收益（失败返回错误信息）"""
    try:
        import quant_engine as Q
        import v2
        a = Q.analyze(code)
        a2 = None
        try:
            a2 = v2.analyze_v2(code, a["quote"]["name"])
        except Exception:
            a2 = None
        return {
            "status": a2["status"] if a2 else a["verdict"],
            "l3": a2["l3"] if a2 else None,
            "trend_verdict": a["verdict"],
            "backtest_total_ret": a["bt"]["total_ret"],
            "price": a["quote"]["price"],
            "change_pct": a["quote"]["change_pct"],
        }
    except Exception as e:
        return {"error": str(e)}


def build_task_pack(pack, quant=None):
    """生成 Markdown 研究任务包：数据底稿 + 量化面 + 四大师规则速评 + 执行要求"""
    code = pack["code"]
    name = pack.get("name", code)
    date8 = datetime.date.today().strftime("%Y%m%d")
    verify = pack.get("verify") or {}
    fins = pack.get("financials") or []
    if quant is None:
        quant = _quant_summary(code)
    master = None
    try:
        import master_score
        master = master_score.master_scores(
            pack, quant=quant if isinstance(quant, dict) and "error" not in quant else None)
    except Exception:
        master = None

    fin_rows = "\n".join(
        f"| {f['report_date']} | {_fmt(f['revenue'])} | {_fmt_pct(f['rev_growth'])} "
        f"| {_fmt(f['net_profit'])} | {_fmt_pct(f['profit_growth'])} | {_fmt(f['eps'], 2)} "
        f"| {_fmt(f['bps'], 2)} | {_fmt_pct(f['roe'])} | {_fmt_pct(f['gross_margin'])} "
        f"| {_fmt_pct(f['net_margin'])} | {_fmt_pct(f['debt_ratio'])} |"
        for f in fins) or "| — |"

    return f"""# 研究任务包：{name}（{code}）

> 生成时间：{pack.get('fetched_at', '')} · 数据截止：{pack.get('report_date', '')}
> 请把本任务包交给 Codex，并附言：**按 investment-team + investment-research 执行深度研究**。

## 一、项目侧已准备好的数据底稿

| 项目 | 数值 |
|---|---|
| 现价 | {pack.get('price', '—')} 元（{_fmt_pct(pack.get('change_pct'))}） |
| 总市值 | {_fmt(pack.get('market_cap_yi'), 2)} 亿 |
| 流通市值 | {_fmt(pack.get('float_cap_yi'), 2)} 亿 |
| PE / PB | {pack.get('pe') or '—'} / {pack.get('pb') or '—'} |
| 换手率 / 成交额 | {_fmt_pct(pack.get('turnover_rate'))} / {_fmt(pack.get('amount_yi'), 2)} 亿 |
| 52 周区间 | {pack.get('low_52w') or '—'} ~ {pack.get('high_52w') or '—'} |
| 市值验算 | {verify.get('note', '—')} |

### 近 5 年年报主要财务指标（东财 F10，单源）

| 报告期 | 营收 | 营收同比 | 归母净利 | 净利同比 | EPS | BPS | ROE | 毛利率 | 净利率 | 负债率 |
|---|---|---|---|---|---|---|---|---|---|---|
{fin_rows}

> ⚠️ 以上为单源数据（东财），未做双源交叉验证。深度研究时按 financial-data 规范补充巨潮/年报原文，误差>1% 须标记。

### 项目量化面速览（选股系统已有分析，供对照）

| 项目 | 数值 |
|---|---|
| 趋势状态 | {quant.get('status', '—') if isinstance(quant, dict) else '—'} |
| 趋势结论（v2） | {quant.get('trend_verdict', '—') if isinstance(quant, dict) else '—'} |
| L3 择时评分 | {quant.get('l3', '—') if isinstance(quant, dict) else '—'} |
| 策略回测总收益 | {quant.get('backtest_total_ret', '—') if isinstance(quant, dict) else '—'} |
| 现价 / 当日涨跌 | {quant.get('price', '—') if isinstance(quant, dict) else '—'} / {quant.get('change_pct', '—') if isinstance(quant, dict) else '—'}% |

> 若显示“—”，说明量化分析暂不可用（联网失败或数据不足），深度研究时可直接以行情/财务数据为准。

### 四大师规则化速评（数据代理近似，供参考）

{_master_markdown(master) if master else '> 速评暂不可用（数据不足）。'}

### 研究元数据（生成内部底稿时必填，用于回写 journal）

Codex 生成内部 Markdown 底稿时，请在文件开头保留以下元数据块并如实填写：

```text
<!-- thesis-metadata
code: {code}
report_date: {datetime.date.today().strftime('%Y-%m-%d')}
verdict: 值得深度研究 / 观察 / 暂不关注
overall_score: 0~5
red_lines: 红线1；红线2；红线3（触发即重审）
valuation: 乐观 价格 / 中性 价格 / 悲观 价格
-->
```

填写后执行回写：`python research_data.py --import-thesis {code} "报告归档\\研究\\{code}\\内部底稿.md"`

## 二、研究执行要求（引用两个 Skill 的核心流程）

1. **信息丰富度评级**：先给出 A/B/C 评级并说明对研究策略的影响（A 级重点做反面检验；C 级用第一性原理）。
2. **四大师五维分析**（investment-research）：
   - 生意本质（段永平）：一句话定义生意、收入结构、定价权、复购/粘性；
   - 护城河（巴菲特）：品牌/转换成本/网络效应/规模/技术壁垒，逐一验证并判断 5-10 年趋势；
   - 逆向思考（芒格）：列出失败路径与概率/影响、聪明人为什么不买、最可能错在哪；
   - 管理层（段永平+巴菲特）：关键决策复盘、资本配置、诚信、利益一致性；
   - 行业与文明趋势（李录）：行业所处阶段、产业链位置、10-20 年终局。
3. **数据双源交叉验证**（financial-data）：A 股主源东财、副源巨潮/年报原文；市值验算用 financial_rigor.py，偏差>5% 必须排查。
4. **估值三情景**（investment-checklist）：用 financial_rigor.py three-scenario 精确计算，给出乐观/中性/悲观价格区间与安全边际判断。
5. **报告输出要求**：
   - 对外仅 HTML（按 investment-memo-craft 排版：结论在后、决策表在末尾、克制排版）；
   - 同一目录保留内部 Markdown 底稿（不对外展示），供 report_audit.py 抽检准出；
   - 报告开头含信息丰富度评级与 AI 研究局限声明；结尾区分“AI 分析置信度”与“投资确定性”。
6. **数据抽检**：内部 Markdown 底稿必须执行 `python tools/report_audit.py extract` + `verdict`，通过（准出）后才可发布 HTML。

## 三、产出位置

- HTML 报告：`报告归档/研究/{code}/{name}研究报告_{date8}.html`
- 内部底稿：`报告归档/研究/{code}/{name}研究报告_{date8}.md`
"""


def _master_markdown(master):
    rows = "\n".join(
        f"| {d['key']}（{d['master']}） | {d['score']:.1f}/5 | {d['confidence']} "
        f"| {d['notes'][0] if d['notes'] else '—'} |"
        for d in master["dimensions"])
    return (f"| 维度 | 得分 | 置信度 | 要点 |\n|---|---|---|---|\n{rows}\n"
            f"| **综合** | **{master['overall']:.2f}/5** | **{master['overall_confidence']}** "
            f"| **{master['verdict']}** |\n\n"
            f"> {master['warning']}")


def import_thesis(code, md_path):
    """解析内部底稿开头的 thesis-metadata 元数据块并回写 journal。"""
    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"<!--\s*thesis-metadata(.*?)-->", text, re.S)
    if not m:
        print("❌ 未找到 thesis-metadata 元数据块，无法回写")
        return 1
    kv = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip().lower()] = v.strip()
    code = kv.get("code") or code
    report_date = kv.get("report_date") or datetime.date.today().strftime("%Y-%m-%d")
    try:
        score = float(kv.get("overall_score") or 0)
    except ValueError:
        score = 0.0
    db.save_thesis(code, report_date, kv.get("verdict") or "", score, text,
                   kv.get("red_lines") or "", kv.get("valuation") or "")
    print(f"✅ 已回写论文：{code} @ {report_date} 结论={kv.get('verdict', '')} "
          f"综合={score} 红线={kv.get('red_lines', '')}")
    return 0


# ---------------- CLI ----------------
def main(argv):
    if len(argv) >= 2 and argv[1] == "--import-thesis":
        if len(argv) < 4:
            print("用法: python research_data.py --import-thesis <代码> <内部底稿.md>")
            return 1
        return import_thesis(argv[2], argv[3])
    if len(argv) >= 2 and argv[1] == "--thesis":
        if len(argv) < 3:
            print("用法: python research_data.py --thesis <代码>")
            return 1
        t = db.load_latest_thesis(argv[2])
        print(json.dumps(t, ensure_ascii=False, indent=1) if t else "暂无论文记录")
        return 0
    if len(argv) < 2:
        print("用法: python research_data.py <6位股票代码> [--json|--task|--quick] [--force]")
        print("      python research_data.py --import-thesis <代码> <内部底稿.md>")
        print("      python research_data.py --thesis <代码>")
        return 1
    code = argv[1].strip()
    if not (code.isdigit() and len(code) == 6):
        print("请提供 6 位股票代码")
        return 1
    force = "--force" in argv
    try:
        pack = get_pack(code, force=force)
    except Exception as e:
        print(f"❌ 数据获取失败：{e}")
        return 1
    if "--json" in argv:
        print(json.dumps(pack, ensure_ascii=False, indent=1))
    elif "--quick" in argv:
        print(build_quick_review_html([pack]))
    else:
        print(build_task_pack(pack))
        try:
            d = os.path.join(RESEARCH_DIR, code)
            os.makedirs(d, exist_ok=True)
            date8 = datetime.date.today().strftime("%Y%m%d")
            path = os.path.join(d, f"研究任务包_{code}_{date8}.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(build_task_pack(pack))
            print(f"\n✅ 任务包已保存: {path}")
            print("下一步：把本任务包内容发给 Codex，并附言按 investment-team + investment-research 执行深度研究。")
        except Exception as e:
            print(f"\n⚠️ 任务包写入失败：{e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
