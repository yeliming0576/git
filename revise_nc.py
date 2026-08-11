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

import renumber_nc

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


def _ask_settings():
    """设置窗口（直接作为主窗口弹出）；取消返回 None。"""
    top = tk.Tk()
    top.title("后处理设置")
    top.resizable(False, False)
    top.attributes('-topmost', True)

    tk.Label(top, text="刀库容量（最大刀号）:").grid(
        row=0, column=0, padx=8, pady=6, sticky="w")
    max_var = tk.StringVar(value="60")
    tk.Entry(top, textvariable=max_var, width=12).grid(
        row=0, column=1, padx=8, pady=6)

    tk.Label(top, text="大直径阈值:").grid(
        row=1, column=0, padx=8, pady=6, sticky="w")
    diam_var = tk.StringVar(value="125")
    tk.Entry(top, textvariable=diam_var, width=12).grid(
        row=1, column=1, padx=8, pady=6)

    probe_var = tk.BooleanVar(value=True)
    tk.Checkbutton(top, text="删除探头(TAN-TOU)程序段",
                   variable=probe_var).grid(
        row=2, column=0, columnspan=2, padx=8, pady=6, sticky="w")

    repeat_var = tk.BooleanVar(value=True)
    tk.Checkbutton(top, text="换刀前重复备刀",
                   variable=repeat_var).grid(
        row=3, column=0, columnspan=2, padx=8, pady=6, sticky="w")

    result = {}

    def on_ok():
        try:
            max_tools = int(max_var.get().strip())
            large_diameter = float(diam_var.get().strip())
            if max_tools < 1 or large_diameter < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "刀库容量需为正整数，大直径阈值需为非负数",
                                 parent=top)
            return
        result['max_tools'] = max_tools
        result['large_diameter'] = large_diameter
        result['remove_probe'] = probe_var.get()
        result['repeat_stage'] = repeat_var.get()
        top.destroy()

    def on_cancel():
        top.destroy()

    tk.Button(top, text="确定", command=on_ok, width=8).grid(
        row=4, column=0, padx=8, pady=8)
    tk.Button(top, text="取消", command=on_cancel, width=8).grid(
        row=4, column=1, padx=8, pady=8)
    top.update_idletasks()
    w = top.winfo_width()
    h = top.winfo_height()
    x = (top.winfo_screenwidth() - w) // 2
    y = (top.winfo_screenheight() - h) // 3
    top.geometry(f"+{x}+{y}")
    top.deiconify()
    top.lift()
    top.focus_force()
    top.wait_window()
    return result or None


def run_gui():
    """图形界面入口：可选择多个文件一起处理，并设置后处理参数。"""
    print("正在打开后处理设置窗口…")
    try:
        settings = _ask_settings()
    except Exception as e:
        print(f"无法打开图形界面：{e}")
        print("可改用命令行方式：python revise_nc.py 文件1 文件2 ...")
        return
    if settings is None:
        print("已取消。")
        return

    root = tk.Tk()
    root.withdraw()
    file_paths = filedialog.askopenfilenames(
        parent=root,
        title="选择要处理的 NC 文件（可多选，作为同一组处理）",
        filetypes=[("NC files", "*.nc *.NC"), ("Text files", "*.txt"),
                   ("All files", "*.*")]
    )
    root.destroy()
    if not file_paths:
        print("未选择文件，程序退出。")
        return

    print(f"已选择 {len(file_paths)} 个文件，开始处理…")
    processed = []
    for file_path in file_paths:
        output_path = output_path_for(file_path)
        if os.path.exists(output_path):
            if not messagebox.askyesno(
                    "确认覆盖",
                    f"输出文件已存在：\n{output_path}\n\n是否覆盖？"):
                print(f"跳过（输出已存在）: {output_path}")
                continue
        try:
            stats = process_gcode(file_path, output_path)
            print(f"OK     {file_path} -> {output_path}")
            processed.append(output_path)
        except Exception as e:
            messagebox.showerror("错误", f"处理失败：{file_path}\n{e}")

    if not processed:
        print("没有文件被处理。")
        return

    try:
        summary = renumber_nc.run(
            processed,
            max_tools=settings['max_tools'],
            large_diameter=settings['large_diameter'],
            remove_probe=settings['remove_probe'],
            repeat_stage=settings['repeat_stage'])
        print(f"后处理完成，报告：{summary['report_path']}")
        messagebox.showinfo(
            "完成",
            f"处理与后处理完成！\n共 {len(processed)} 个文件。\n"
            f"报告：{summary['report_path']}\n\n"
            f"（探头删除、刀号重排、空行清理明细见 tool_summary.txt）")
    except Exception as e:
        print(f"后处理失败：{e}")
        messagebox.showerror("错误", f"后处理失败：\n{e}")


def run_cli(files, output_dir=None, force=False, max_tools=60,
            large_diameter=125.0, remove_probe=True, repeat_stage=True,
            renumber=True):
    """命令行批量入口，逐文件处理并报告结果，返回进程退出码。"""
    exit_code = 0
    ok = skipped = failed = 0
    processed = []
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
            processed.append(output_path)
        except Exception as e:
            print(f"ERROR  {file_path}: {e}", file=sys.stderr)
            failed += 1
            exit_code = 1

    if renumber and processed:
        try:
            summary = renumber_nc.run(
                processed,
                max_tools=max_tools,
                large_diameter=large_diameter,
                remove_probe=remove_probe,
                repeat_stage=repeat_stage)
            print(f"\n后处理（探头删除/刀号重排/空行清理）：{len(processed)} 个文件")
            print(f"报告：{summary['report_path']}")
        except Exception as e:
            print(f"ERROR  后处理失败: {e}", file=sys.stderr)
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
    parser.add_argument("--max-tools", type=int, default=60, metavar="数量",
                        help="刀库容量/最大刀号（默认 60）")
    parser.add_argument("--large-diameter", type=float, default=125.0,
                        metavar="直径", help="大直径刀具阈值（默认 125）")
    parser.add_argument("--no-remove-probe", action="store_true",
                        help="不删除 TAN-TOU 探头程序段")
    parser.add_argument("--no-repeat-stage", action="store_true",
                        help="换刀 M06 前不重复备刀")
    parser.add_argument("--no-renumber", action="store_true",
                        help="处理后不执行刀号重排/探头删除后处理")
    return parser


def main(argv=None):
    """命令行入口（不直接调用 GUI）。"""
    args = build_parser().parse_args(argv)
    return run_cli(
        args.files,
        output_dir=args.output_dir,
        force=args.force,
        max_tools=args.max_tools,
        large_diameter=args.large_diameter,
        remove_probe=not args.no_remove_probe,
        repeat_stage=not args.no_repeat_stage,
        renumber=not args.no_renumber)


def entry(argv=None):
    """统一入口：有参数走命令行，无参数走图形界面。"""
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        sys.exit(main(argv))
    run_gui()


if __name__ == "__main__":
    entry()
