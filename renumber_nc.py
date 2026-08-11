# -*- coding: utf-8 -*-
"""新昱程序刀号重排与探头删除（revise_nc.py 的后处理）。

用法：
    python renumber_nc.py 文件1_processed [文件2_processed ...] \
        [--max-tools 60] [--large-diameter 125] [--no-remove-probe]

功能：
1. 删除 TAN-TOU 探头程序段（新机床无探头，默认执行）。
2. 汇总 T/H 关系，并按“保留旧号、最小改动”重排刀号：
   - 新刀号不超过刀库容量（默认 60）；
   - 每段 H 与当前刀 T 保持一致；
   - 组内（一次选中的多个文件）共用刀具刀号一致；
   - 直径超过阈值的刀具相邻刀位必须空出；
   - 槽位不足时大直径刀具改为 T0 手动换刀，插入 M0(shou_gong_huan_dao)。
3. 同步改写 #13xxx 变量与 G41/G42/G65P524 的 D 补偿号。
4. 最终写回时删除全部空行/纯空白行，并生成 tool_summary.txt 报告。
"""

import argparse
import os
import re
import sys
from collections import OrderedDict
from datetime import datetime

# ==================== 正则 ====================
# T 号 + 第一对小括号内容为刀名（忽略 (1111) 等尾缀）
T_RE = re.compile(r'^\s*T(\d+)\s*\(([^)]*)\)')
# 换刀：M06 或 M6（不误匹配 M106/M60）
M06_RE = re.compile(r'\bM0?6\b')
# 纯 M1 行（探头段前紧邻的可选停）
M1_RE = re.compile(r'^\s*M1\s*$')
# N 序列号标签（如 N4321(WEI-YAN-ZHENG)）
N_LABEL_RE = re.compile(r'^\s*N\d+\s*(?:\(.*\))?\s*$')
# H 号：G43Z475.H29 / G43Z460.007H9 均可匹配，不匹配 H#505
H_RE = re.compile(r'H(\d+)\b')
# 变量：#13 + 三位补零刀号（#13009、#13107、#13054）
VAR_RE = re.compile(r'#13(\d{3})(?!\d)')
# 刀名中的直径：第一个独立 D 后的数字
DIAM_RE = re.compile(r'(?<![A-Za-z0-9])D(\d+(?:\.\d+)?)')
# G41/G42/G65P524 行中的 D 补偿号
DCODE_RE = re.compile(r'D(\d+(?:\.\d+)?)')
DCODE_LINE_RE = re.compile(r'G4[12](?![0-9])|G65P524')

M0_MANUAL = 'M0(shou_gong_huan_dao)'


def normalize_tool_name(name):
    """去除空格与连字符后比较刀名（兼容 TAN-TOU / TAN TOU）。"""
    return re.sub(r'[\s\-]', '', name).upper()


def is_probe(name):
    return normalize_tool_name(name) == 'TANTOU'


def extract_diameter(name):
    m = DIAM_RE.search(name)
    return float(m.group(1)) if m else 0.0


def _newline_of(lines):
    return '\r\n' if lines and lines[0].endswith('\r\n') else '\n'


class Block:
    """一个加工段：M06 装入 staged 刀具，直到下一个 M06。"""

    __slots__ = ('old_t', 'name', 'staging_idx', 'start', 'end',
                 'h_refs', 'var_refs', 'd_refs', 'order')

    def __init__(self, old_t, name, staging_idx, start, end, order):
        self.old_t = old_t
        self.name = name
        self.staging_idx = staging_idx
        self.start = start          # M06 所在行（0 起）
        self.end = end              # 下一个 M06 行（0 起，独占）
        self.h_refs = set()
        self.var_refs = set()
        self.d_refs = set()
        self.order = order


class Tool:
    """组内的一把刀具（按 刀名+旧T 唯一）。"""

    __slots__ = ('key', 'name', 'old_t', 'diam', 'files', 'blocks', 'order',
                 'large')

    def __init__(self, key, name, old_t, diam, order):
        self.key = key
        self.name = name
        self.old_t = old_t
        self.diam = diam
        self.files = set()
        self.blocks = []
        self.order = order
        self.large = False


def parse_file(path, remove_probe=True):
    """解析一个 _processed 文件，返回行、加工段、探头删除范围等。"""
    with open(path, 'r', encoding='utf-8-sig') as f:
        lines = f.readlines()
    nl = _newline_of(lines)
    n = len(lines)
    blocks = []
    warnings = []
    deleted = set()
    deletions = []

    staged = None          # (old_t, name, staging_idx)
    current = None         # 当前正在收集的 Block

    for i, line in enumerate(lines):
        s = line.strip()
        m = T_RE.match(s)
        if m:
            staged = (int(m.group(1)), m.group(2).strip(), i)
            continue
        if M06_RE.search(s):
            if current is not None:
                current.end = i
            if staged is None:
                warnings.append(f"{path}: 第{i+1}行 M06 前没有预选 T 号")
                current = None
            else:
                current = Block(staged[0], staged[1], staged[2], i, n, len(blocks))
                blocks.append(current)
                staged = None
            continue
        if current is not None:
            current.h_refs.update(int(x) for x in H_RE.findall(s))
            current.var_refs.update(int(x) for x in VAR_RE.findall(s))
            if DCODE_LINE_RE.search(s):
                current.d_refs.update(int(float(x)) for x in DCODE_RE.findall(s))

    # ---- 校验：H/#13xxx/D 应与当前刀一致（探头段除外，探头段将被删除） ----
    for b in blocks:
        if remove_probe and is_probe(b.name):
            continue
        for h in sorted(b.h_refs):
            if h != b.old_t:
                warnings.append(
                    f"{path}: T{b.old_t}({b.name}) 段内出现 H{h}，与当前刀不符，保留原样")
        for v in sorted(b.var_refs):
            if v != b.old_t:
                warnings.append(
                    f"{path}: T{b.old_t}({b.name}) 段内出现 #13{v:03d}，与当前刀不符，保留原样")
        for d in sorted(b.d_refs):
            if d != b.old_t:
                warnings.append(
                    f"{path}: T{b.old_t}({b.name}) 段内出现 D{d}，与当前刀不符，保留原样")

    # ---- 探头程序段删除范围 ----
    if remove_probe:
        for b in blocks:
            if not is_probe(b.name):
                continue
            indices = {b.staging_idx}
            # 预选行与 M06 之间的纯 M1 行
            for j in range(b.staging_idx + 1, b.start):
                if M1_RE.match(lines[j].strip()):
                    indices.add(j)
            # M06 本身
            indices.add(b.start)
            # M06 之后到下一个 M06：保留第一条 T 预选行（下一把真实刀具），其余删除
            kept_t = False
            for j in range(b.start + 1, b.end):
                if lines[j].strip() == '%':
                    continue
                if not kept_t and T_RE.match(lines[j].strip()):
                    kept_t = True
                    continue
                indices.add(j)
            # 直接紧邻在预选行前的 N 标签（如 N4321）一并删除
            if b.staging_idx > 0 and N_LABEL_RE.match(lines[b.staging_idx - 1].strip()):
                indices.add(b.staging_idx - 1)
            deleted.update(indices)
            deletions.append({
                'kind': 'probe_block',
                'start_line': min(indices) + 1,
                'end_line': max(indices) + 1,
                'count': len(indices),
                'name': b.name,
            })
        # 仅预选、未实际加载的探头行（文件末尾）
        if staged is not None and is_probe(staged[1]):
            indices = {staged[2]}
            if staged[2] > 0 and N_LABEL_RE.match(lines[staged[2] - 1].strip()):
                indices.add(staged[2] - 1)
            deleted.update(indices)
            deletions.append({
                'kind': 'unused_staging',
                'start_line': min(indices) + 1,
                'end_line': max(indices) + 1,
                'count': len(indices),
                'name': staged[1],
            })

    return {
        'path': path,
        'lines': lines,
        'newline': nl,
        'blocks': blocks,
        'deleted': deleted,
        'deletions': deletions,
        'warnings': warnings,
    }


def build_instances(parsed_files, remove_probe):
    """汇总组内刀具实例：(刀名, 旧T) 为一把；返回实例列表与警告。"""
    instances = OrderedDict()
    name_info = {}
    warnings = []
    for pf in parsed_files:
        base = os.path.basename(pf['path'])
        for b in pf['blocks']:
            if remove_probe and is_probe(b.name):
                continue
            key = (b.name, b.old_t)
            inst = instances.get(key)
            if inst is None:
                inst = Tool(key, b.name, b.old_t,
                            extract_diameter(b.name), len(instances))
                instances[key] = inst
            inst.files.add(base)
            inst.blocks.append((pf, b))
            info = name_info.setdefault(b.name, {'t': set(), 'files': set()})
            info['t'].add(b.old_t)
            info['files'].add(base)
    for name, info in name_info.items():
        if len(info['t']) > 1:
            warnings.append(
                f"刀名「{name}」在组内出现多个旧刀号 {sorted(info['t'])}，"
                f"按多把刀具处理，不自动合并")
    return list(instances.values()), warnings


def _combos(k, m):
    """从 1..m 中选 k 个槽位，相邻间距 >= 2（大刀具不能相邻）。"""
    if k == 0:
        yield ()
        return

    def rec(start, chosen):
        if len(chosen) == k:
            yield tuple(chosen)
            return
        remaining = k - len(chosen) - 1
        for s in range(start, m - 2 * remaining + 1):
            chosen.append(s)
            yield from rec(s + 2, chosen)
            chosen.pop()

    yield from rec(1, [])


def _try_assign(instances, large, max_tools):
    """尝试为全部刀具分配槽位；返回 (assignment, displaced) 或 None。"""
    large_set = set(large)
    nonlarge = sorted((i for i in instances if i not in large_set),
                      key=lambda i: i.order)

    # 同号冲突：同一旧槽位的非大刀具，只保留第一把
    dup = set()
    seen_t = {}
    for inst in nonlarge:
        t = inst.old_t
        if 1 <= t <= max_tools:
            if t in seen_t:
                dup.add(inst)
            else:
                seen_t[t] = inst

    large_by_old = {}
    for inst in large:
        t = inst.old_t
        if 1 <= t <= max_tools:
            large_by_old[t] = large_by_old.get(t, 0) + 1

    best = None
    for combo in _combos(len(large), max_tools):
        forbidden = set(combo)
        for s in combo:
            if s > 1:
                forbidden.add(s - 1)
            if s < max_tools:
                forbidden.add(s + 1)

        displaced = set(dup)
        for inst in nonlarge:
            if inst in displaced:
                continue
            if inst.old_t < 1 or inst.old_t > max_tools \
                    or inst.old_t in forbidden:
                displaced.add(inst)

        kept_slots = {inst.old_t for inst in nonlarge if inst not in displaced}
        free = [s for s in range(1, max_tools + 1)
                if s not in forbidden and s not in kept_slots]
        if len(free) < len(displaced):
            continue

        large_moved = len(large) - sum(
            1 for s in combo if large_by_old.get(s, 0) >= 1)
        cost = large_moved + len(displaced)
        key = (cost, sum(combo), combo)
        if best is None or key < best[0]:
            best = (key, combo, displaced, free)

    if best is None:
        return None

    _, combo, displaced, free = best
    assign = {}

    # 大刀具：优先保留旧号，其余按顺序填入组合槽位
    combo_slots = sorted(combo)
    taken = set()
    for inst in sorted(large, key=lambda i: i.order):
        t = inst.old_t
        if 1 <= t <= max_tools and t in combo_slots and t not in taken:
            assign[inst] = t
            taken.add(t)
    for inst in sorted(large, key=lambda i: i.order):
        if inst in assign:
            continue
        for s in combo_slots:
            if s not in taken:
                assign[inst] = s
                taken.add(s)
                break

    # 非大刀具：保留的保持旧号，被挤出的按首次出现顺序填最低空位
    for inst in nonlarge:
        if inst not in displaced:
            assign[inst] = inst.old_t
    free_iter = iter(sorted(free))
    for inst in sorted(displaced, key=lambda i: i.order):
        try:
            assign[inst] = next(free_iter)
        except StopIteration:
            return None
    return assign, displaced


def assign_slots(instances, max_tools, large_diameter):
    """分配刀号；槽位不足时把最大直径大刀具改为 T0，直到可容纳。"""
    if max_tools < 1:
        raise ValueError(f"刀库容量必须 >= 1，当前 {max_tools}")
    for inst in instances:
        inst.large = inst.diam > large_diameter
    large = sorted((i for i in instances if i.large),
                   key=lambda i: (-i.diam, i.order))
    t0_list = []
    while True:
        remaining = [i for i in large if i not in t0_list]
        res = _try_assign(instances, remaining, max_tools)
        if res is not None:
            assign, displaced = res
            for inst in t0_list:
                assign[inst] = 0
            return assign, displaced, t0_list
        if not remaining:
            raise ValueError(
                f"刀库容量 {max_tools} 不足以容纳 {len(instances)} 把刀具")
        t0_list.append(remaining[0])


def _replace_refs(line, current, key_map):
    """按当前刀替换 H、#13xxx、G41/G42/G65P524 的 D 号。"""
    name, old_t = current
    new_t = key_map.get((name, old_t))
    if new_t is None:
        return line

    def h_sub(m):
        v = int(m.group(1))
        return f'H{new_t}' if v == old_t else m.group(0)

    line = H_RE.sub(h_sub, line)

    def v_sub(m):
        v = int(m.group(1))
        return f'#13{new_t:03d}' if v == old_t else m.group(0)

    line = VAR_RE.sub(v_sub, line)

    if DCODE_LINE_RE.search(line):
        def d_sub(m):
            v = int(float(m.group(1)))
            return f'D{new_t}' if v == old_t else m.group(0)

        line = DCODE_RE.sub(d_sub, line)
    return line


def rewrite_file(path, parsed, key_map, write=True, repeat_stage=True):
    """应用删除/改号/插入 M0/重复备刀/空行清理，返回结果信息。"""
    lines = parsed['lines']
    deleted = parsed['deleted']
    nl = parsed['newline']
    out = []
    warnings = []
    staged = None          # (name, old_t)
    staged_line = None     # 改写后的备刀行文本（用于重复插入）
    current = None
    blank_removed = 0
    m06_written = 0
    repeat_count = 0

    for i, line in enumerate(lines):
        if i in deleted:
            continue
        s = line.strip()
        m = T_RE.match(s)
        if m:
            old_t = int(m.group(1))
            name = m.group(2).strip()
            new_t = key_map.get((name, old_t))
            if new_t is None:
                warnings.append(
                    f"{path}: 第{i+1}行 T{old_t}({name}) 不在刀具表中，保持原样")
                new_t = old_t
            if new_t == 0:
                out.extend([M0_MANUAL + nl] * 3)
            rewritten = re.sub(r'^(\s*T)\s*\d+',
                               lambda mm: mm.group(1) + str(new_t),
                               line, count=1)
            out.append(rewritten)
            staged = (name, old_t)
            staged_line = rewritten
            continue
        if M06_RE.search(s):
            if staged is None:
                warnings.append(f"{path}: 第{i+1}行 M06 前没有预选 T 号")
                current = None
            else:
                current = staged
                new_t = key_map.get(current)
                if new_t == 0:
                    out.extend([M0_MANUAL + nl] * 3)
                elif repeat_stage and m06_written > 0 and staged_line is not None:
                    # 换刀前重复备刀：紧贴 M06 之前插入同一把刀的备刀行
                    out.append(staged_line)
                    repeat_count += 1
                staged = None
            m06_written += 1
            out.append(line)
            continue
        if current is not None:
            line = _replace_refs(line, current, key_map)
        if s == '':
            blank_removed += 1
            continue
        out.append(line)

    new_text = ''.join(out)
    changed = new_text != ''.join(lines)
    if write and changed:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_text)
    return {
        'path': path,
        'changed': changed,
        'blank_removed': blank_removed,
        'repeat_count': repeat_count,
        'warnings': warnings,
        'deletions': parsed['deletions'],
    }


def _build_reasons(assign, displaced, max_tools, t0_list):
    reasons = {}
    for inst, new_t in assign.items():
        if new_t == 0:
            reasons[inst] = '槽位不足，改为 T0 手动换刀'
        elif new_t == inst.old_t:
            reasons[inst] = '保留原号'
        elif inst.old_t > max_tools:
            reasons[inst] = f'旧号 {inst.old_t} 超过刀库容量 {max_tools}，重排'
        elif inst.large:
            reasons[inst] = '大刀具移入空位'
        elif inst in displaced:
            reasons[inst] = '原刀位被占用（大刀具邻位或同号冲突），移入空位'
        else:
            reasons[inst] = '重排'
    return reasons


def build_report(files, results, instances, assign, reasons, warnings,
                 max_tools, large_diameter, remove_probe, repeat_stage):
    """生成文本报告。"""
    lines = []
    lines.append('新昱程序后处理报告')
    lines.append(f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f'参数: 刀库容量={max_tools}, 大直径阈值={large_diameter}, '
                 f'删除探头程序={"是" if remove_probe else "否"}, '
                 f'重复备刀={"是" if repeat_stage else "否"}')
    lines.append('输入文件: ' + ', '.join(os.path.basename(f) for f in files))
    lines.append('')

    lines.append('【探头程序段删除】')
    any_del = False
    for res in results:
        for d in res['deletions']:
            any_del = True
            kind = '探头加工段' if d['kind'] == 'probe_block' else '未使用的探头预选行'
            lines.append(
                f"{os.path.basename(res['path'])}: 删除{kind} "
                f"（第 {d['start_line']}-{d['end_line']} 行，共 {d['count']} 行）")
    if not any_del:
        lines.append('无')
    lines.append('')

    lines.append('【空行清理】')
    for res in results:
        lines.append(f"{os.path.basename(res['path'])}: "
                     f"删除空行 {res['blank_removed']} 行")
    lines.append('')

    lines.append('【重复备刀】')
    if repeat_stage:
        for res in results:
            lines.append(f"{os.path.basename(res['path'])}: "
                         f"插入 {res['repeat_count']} 行")
    else:
        lines.append('未开启')
    lines.append('')

    lines.append('【刀具汇总】')
    header = f"{'刀名':<42}{'直径':>8} {'大直径':<4} {'旧T':>4} {'新T':>4} {'文件':<18} {'段数':>3} 原因"
    lines.append(header)
    for inst in sorted(instances, key=lambda i: i.order):
        files_str = ','.join(sorted(inst.files))
        lines.append(
            f"{inst.name:<42}{inst.diam:>8.3f} "
            f"{'是' if inst.large else '否':<4} "
            f"{inst.old_t:>4} {assign[inst]:>4} "
            f"{files_str:<18} {len(inst.blocks):>3} "
            f"{reasons.get(inst, '')}")
    lines.append('')

    lines.append('【规则校验】')
    occupied = {v for v in assign.values() if v != 0}
    large_ok = True
    for inst in instances:
        if not inst.large or assign[inst] == 0:
            continue
        s = assign[inst]
        for nb in (s - 1, s + 1):
            if 1 <= nb <= max_tools and nb in occupied:
                large_ok = False
                lines.append(f"警告: {inst.name}（新T{s}）邻位 {nb} 仍被占用")
    lines.append(f"- 新刀号均不超过 {max_tools}: "
                 f"{'通过' if all(v == 0 or 1 <= v <= max_tools for v in assign.values()) else '未通过'}")
    lines.append(f"- 大刀具相邻刀位空出: {'通过' if large_ok else '未通过'}")
    shared = [i for i in instances if len(i.files) > 1]
    lines.append(f"- 组内共用刀具刀号一致: 通过（{len(shared)} 把共用）")
    if t0 := [i for i in instances if assign[i] == 0]:
        lines.append(f"- T0 手动换刀: {len(t0)} 把（"
                     + '、'.join(f"{i.name}" for i in t0) + '）')
    lines.append('')

    lines.append('【警告】')
    if warnings:
        for w in warnings:
            lines.append('- ' + w)
    else:
        lines.append('无')
    return '\n'.join(lines) + '\n'


def run(files, max_tools=60, large_diameter=125.0, remove_probe=True,
        repeat_stage=True, write=True):
    """对一组 _processed 文件执行完整后处理。"""
    for f in files:
        if not os.path.isfile(f):
            raise ValueError(f"文件不存在: {f}")
    parsed = [parse_file(f, remove_probe) for f in files]
    instances, warnings = build_instances(parsed, remove_probe)
    assign, displaced, t0_list = assign_slots(instances, max_tools, large_diameter)
    reasons = _build_reasons(assign, displaced, max_tools, t0_list)
    key_map = {(i.name, i.old_t): new for i, new in assign.items()}

    results = []
    for f, pf in zip(files, parsed):
        res = rewrite_file(f, pf, key_map, write=write,
                           repeat_stage=repeat_stage)
        results.append(res)
        warnings.extend(pf['warnings'])
        warnings.extend(res['warnings'])

    report_text = build_report(files, results, instances, assign, reasons,
                               warnings, max_tools, large_diameter,
                               remove_probe, repeat_stage)
    report_path = os.path.join(os.path.dirname(os.path.abspath(files[0])),
                               'tool_summary.txt')
    if write:
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_text)
    return {
        'report_text': report_text,
        'report_path': report_path,
        'results': results,
        'instances': instances,
        'assign': assign,
        'warnings': warnings,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        description='新昱程序刀号重排与探头删除（revise_nc.py 后处理）。')
    parser.add_argument('files', nargs='+', metavar='_processed文件',
                        help='要处理的清洗后文件，可多个，作为同一组统一分配刀号')
    parser.add_argument('--max-tools', type=int, default=60, metavar='数量',
                        help='刀库容量/最大刀号（默认 60）')
    parser.add_argument('--large-diameter', type=float, default=125.0,
                        metavar='直径', help='大直径刀具阈值（默认 125）')
    parser.add_argument('--no-remove-probe', action='store_true',
                        help='不删除 TAN-TOU 探头程序段')
    parser.add_argument('--no-repeat-stage', action='store_true',
                        help='换刀 M06 前不重复备刀')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    summary = run(args.files,
                  max_tools=args.max_tools,
                  large_diameter=args.large_diameter,
                  remove_probe=not args.no_remove_probe,
                  repeat_stage=not args.no_repeat_stage)
    print(summary['report_text'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
