"""NC/G 代码转换工具：将"联德"设备程序改写为"新昱"设备格式。

用法：
    python revise_nc.py                      # 图形界面，选择单个文件处理
    python revise_nc.py 文件1.nc [文件2.nc ...] [-o 输出目录] [--force]
"""

import argparse
import os
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

# ==================== 规则正则（预编译） ====================
# 规则1：删除包含 IF[ABS 的行
RE_IF_ABS = re.compile(r'IF\[ABS', re.IGNORECASE)
# 规则2：删除 G65P8040 整行
RE_G65_P8040 = re.compile(r'G65\s*P8040', re.IGNORECASE)
# 规则3：M106 → M06
RE_M106 = re.compile(r'\bM106\b', re.IGNORECASE)
# 规则4：M88 → M07
RE_M88 = re.compile(r'\bM88\b', re.IGNORECASE)
# 规则5：M135 → M109
RE_M135 = re.compile(r'\bM135\b', re.IGNORECASE)
# 规则6：G101A54./G101A54 → 查找后续 G54~G59 并合并为 G90G0G5yB0
RE_G101 = re.compile(r'^\s*G101\s*A5[4-9]\.?(?!\d)', re.IGNORECASE)
# G54~G59 可能紧跟轴字母（如 G54X...），但不能跟数字（G540 不算）
RE_G5X = re.compile(r'G(5[4-9])(?![0-9])', re.IGNORECASE)
# 规则7：G104Hn + G43Z...H#505 → G43Z...Hn
RE_G104 = re.compile(r'^\s*G104\s*H(\d+)', re.IGNORECASE)
RE_G43_H505 = re.compile(r'^\s*G43\s*Z(-?[\d.]+)\s*H#505', re.IGNORECASE)
# 规则8：删除 G52 #520 ~ #529（不误删 #5200）
RE_G52_VAR = re.compile(r'^\s*G52\s*#52[0-9](?![0-9])', re.IGNORECASE)
# 规则9：删除 G52 Z0 / G52 Z0.（不误删 G52 Z0.5 / G52 Z01）
RE_G52_Z0 = re.compile(r'^\s*G52\s*Z0\.?(?=$|\s)', re.IGNORECASE)

STAT_KEYS = ("del_if_abs", "del_g65", "del_g52_var", "del_g52_z0",
             "m106", "m88", "m135", "g101", "g104")

STAT_LABELS = (
    ("del_if_abs", "删除 IF[ABS 行"),
    ("del_g65", "删除 G65P8040 行"),
    ("del_g52_var", "删除 G52 #52x 行"),
    ("del_g52_z0", "删除 G52 Z0 行"),
    ("m106", "M106→M06"),
    ("m88", "M88→M07"),
    ("m135", "M135→M109"),
    ("g101", "G101→G90G0G5yB0"),
    ("g104", "G104+G43 合并"),
)


def process_gcode(input_file, output_file):
    """按 9 条规则处理 NC 文件，返回各规则命中次数字典。"""
    try:
        with open(input_file, 'r', encoding='utf-8-sig') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        raise ValueError(
            f"文件编码无法识别（不是 UTF-8/ASCII）：{input_file}") from None

    stats = dict.fromkeys(STAT_KEYS, 0)
    new_lines = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 规则1：删除包含 IF[ABS 的行
        if RE_IF_ABS.search(stripped):
            stats["del_if_abs"] += 1
            i += 1
            continue

        # 规则2：删除 G65P8040 整行
        if RE_G65_P8040.search(stripped):
            stats["del_g65"] += 1
            i += 1
            continue

        # 规则8：删除 G52 #520 ~ #529（G52 #521 / G52 #529 等）
        if RE_G52_VAR.match(stripped):
            stats["del_g52_var"] += 1
            i += 1
            continue

        # 规则9：删除 G52 Z0（固定格式）
        if RE_G52_Z0.match(stripped):
            stats["del_g52_z0"] += 1
            i += 1
            continue

        # 替换规则统一在这里处理（保证所有 M 代码都能替换）
        modified_line = line

        # 规则3：M106 → M06
        modified_line, cnt = RE_M106.subn('M06', modified_line)
        stats["m106"] += cnt
        # 规则4：M88 → M07
        modified_line, cnt = RE_M88.subn('M07', modified_line)
        stats["m88"] += cnt
        # 规则5：M135 → M109 【强制生效】
        modified_line, cnt = RE_M135.subn('M109', modified_line)
        stats["m135"] += cnt

        # 规则6：G101A54.Bxxx / G101A54Bxxx → G90G0G5yB0（保留 G52）
        if RE_G101.match(modified_line.strip()):
            j = i + 1
            found = False
            temp_lines = []
            while j < n:
                next_line_raw = lines[j]
                next_line = next_line_raw.strip()

                if next_line.startswith('('):
                    temp_lines.append(next_line_raw)
                    j += 1
                    continue

                # 只在行首 ( 注释之前搜索 G54~G59
                code_part = next_line.split('(', 1)[0]
                match = RE_G5X.search(code_part)
                if match:
                    g5y = match.group(1)
                    new_lines.extend(temp_lines)
                    new_lines.append(f"G90G0G{g5y}B0\n")
                    i = j
                    found = True
                    break
                else:
                    temp_lines.append(next_line_raw)
                    j += 1

            if not found:
                new_lines.append(modified_line)
                i += 1
            else:
                stats["g101"] += 1
            continue

        # 规则7：G104Hn + G43Z...H#505 → G43Z...Hn
        g104_match = RE_G104.match(modified_line)
        if g104_match:
            h_val = g104_match.group(1)
            j = i + 1
            # 跳过空行与注释行
            while j < n:
                nxt = lines[j].strip()
                if nxt == '' or nxt.startswith('('):
                    j += 1
                    continue
                break
            if j < n:
                g43_match = RE_G43_H505.match(lines[j])
                if g43_match:
                    z_val = g43_match.group(1)
                    new_lines.append(f"G43Z{z_val}H{h_val}\n")
                    i = j + 1
                    stats["g104"] += 1
                    continue
            new_lines.append(modified_line)
            i += 1
            continue

        new_lines.append(modified_line)
        i += 1

    with open(output_file, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    return stats


def format_stats(stats):
    """把统计字典格式化为多行可读文本。"""
    return "\n".join(f"{label}: {stats[key]}" for key, label in STAT_LABELS)


def output_path_for(input_file, output_dir=None):
    """按规则生成输出路径：原文件名_processed.扩展名。"""
    dir_name = output_dir if output_dir else os.path.dirname(input_file)
    base_name = os.path.basename(input_file)
    name, ext = os.path.splitext(base_name)
    return os.path.join(dir_name, f"{name}_processed{ext}")


def run_gui():
    """图形界面入口：选择单个文件处理。"""
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title="选择要处理的 NC 文件",
        filetypes=[("NC files", "*.nc *.NC"), ("Text files", "*.txt"),
                   ("All files", "*.*")]
    )
    if not file_path:
        messagebox.showinfo("提示", "未选择文件，程序退出。")
        return

    output_path = output_path_for(file_path)
    if os.path.exists(output_path):
        if not messagebox.askyesno(
                "确认覆盖",
                f"输出文件已存在：\n{output_path}\n\n是否覆盖？"):
            messagebox.showinfo("提示", "已取消处理。")
            return

    try:
        stats = process_gcode(file_path, output_path)
        messagebox.showinfo(
            "完成",
            f"处理完成！\n输出文件：{output_path}\n\n{format_stats(stats)}")
    except Exception as e:
        messagebox.showerror("错误", f"处理过程中发生错误：\n{e}")


def run_cli(files, output_dir=None, force=False):
    """命令行批量入口，逐文件处理并报告结果，返回进程退出码。"""
    exit_code = 0
    ok = skipped = failed = 0
    for file_path in files:
        if not os.path.isfile(file_path):
            print(f"ERROR  文件不存在: {file_path}", file=sys.stderr)
            failed += 1
            exit_code = 1
            continue

        output_path = output_path_for(file_path, output_dir)
        if os.path.exists(output_path) and not force:
            print(f"SKIP   输出已存在（使用 --force 覆盖）: {output_path}")
            skipped += 1
            continue

        try:
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            stats = process_gcode(file_path, output_path)
            print(f"OK     {file_path} -> {output_path}")
            for line in format_stats(stats).splitlines():
                print(f"       {line}")
            ok += 1
        except Exception as e:
            print(f"ERROR  {file_path}: {e}", file=sys.stderr)
            failed += 1
            exit_code = 1

    print(f"\n完成：成功 {ok} 个，跳过 {skipped} 个，失败 {failed} 个")
    return exit_code


def build_parser():
    parser = argparse.ArgumentParser(
        description="NC/G 代码转换工具（联德 → 新昱）：处理一个或多个 NC 文件。")
    parser.add_argument("files", nargs="+", metavar="NC文件",
                        help="要处理的 NC 文件，可指定多个")
    parser.add_argument("-o", "--output-dir", metavar="目录",
                        default=None, help="输出目录（默认与源文件同目录）")
    parser.add_argument("--force", action="store_true",
                        help="输出文件已存在时强制覆盖")
    return parser


def main(argv=None):
    """命令行入口（不直接调用 GUI）。"""
    args = build_parser().parse_args(argv)
    return run_cli(args.files, args.output_dir, args.force)


def entry(argv=None):
    """统一入口：有参数走命令行，无参数走图形界面。"""
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        sys.exit(main(argv))
    run_gui()


if __name__ == "__main__":
    entry()
