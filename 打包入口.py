# -*- coding: utf-8 -*-
"""
选股系统 EXE 启动器（打包入口）
================================
打包后由 选股系统.exe 调用；源码模式下也可以直接 python 打包入口.py 使用。

运行规则（关键）：
  - EXE 旁边如果有源码（report_server.py 等），就跑这份源码，
    数据（数据\、报告归档\、自选股.txt、日志\）继续落在项目目录，和以前完全一致；
  - 如果只有 EXE（拷到没源码的电脑），就跑打包时内置的那份代码，
    数据落在 EXE 所在目录。

用法：
  选股系统.exe                      菜单（推荐）
  选股系统.exe server [--port 8799] 启动网页服务，参数原样传给 report_server.py
  选股系统.exe report               生成今日量化报告
  选股系统.exe stock 600519 000858  分析指定股票，出单股量化报告
  选股系统.exe hot                  只抓今日热门股
"""
import os
import sys
import runpy

APP_NAME = "选股系统"

COMMANDS = {
    "server": ("report_server.py", "启动网页服务（浏览器打开，局域网可访问）"),
    "report": ("daily_report.py", "生成今日量化报告（热门股/自选股/低值复苏）"),
    "stock": ("quant_stock.py", "分析指定股票（用法：stock 600519 000858）"),
    "hot": ("daily_hot_stocks.py", "只抓今日热门股（30 元以内）"),
}


def _search_dirs():
    """查找脚本的目录顺序：EXE 目录（现成源码优先）→ 打包内置目录 → 本文件目录"""
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)
    dirs.append(os.path.dirname(os.path.abspath(__file__)))
    out, seen = [], set()
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def find_script(name):
    for d in _search_dirs():
        path = os.path.join(d, name)
        if os.path.isfile(path):
            return path
    return None


def run_script(script, args=()):
    """在当前进程里运行指定脚本（保持 __file__ 语义，数据目录跟着脚本走）"""
    path = find_script(script)
    if not path:
        print(f"[{APP_NAME}] 找不到 {script}，无法执行。")
        return 1
    os.chdir(os.path.dirname(path))     # 数据/报告都落在脚本所在目录
    sys.argv = [path] + [str(a) for a in args]
    try:
        runpy.run_path(path, run_name="__main__")
        return 0
    except SystemExit as e:
        try:
            return int(e.code or 0)
        except Exception:
            return 0
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n[{APP_NAME}] 执行出错：{e}")
        return 1


def _menu():
    while True:
        print()
        print("=" * 60)
        print(f" {APP_NAME} · 本地量化选股工具")
        print("=" * 60)
        print(" 1) 启动网页服务（推荐：浏览器打开当日量化报告）")
        print(" 2) 生成今日量化报告")
        print(" 3) 分析指定股票（输入 6 位代码）")
        print(" 4) 只抓今日热门股")
        print(" 5) 报告归档目录位置")
        print(" 0) 退出")
        print("-" * 60)
        try:
            choice = input(" 请选择 [1-5，回车=1]：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice in ("", "1"):
            print("\n正在启动网页服务…… 关闭本窗口即停止服务。\n")
            return run_script(COMMANDS["server"][0])
        if choice == "2":
            print("\n正在生成今日报告，约 1~3 分钟……\n")
            run_script(COMMANDS["report"][0])
            input("\n回车返回菜单……")
        elif choice == "3":
            try:
                raw = input(" 输入股票代码（多个用空格或逗号分隔）：").strip()
            except (EOFError, KeyboardInterrupt):
                return 0
            codes = [c for c in raw.replace(",", " ").replace("，", " ").split() if c]
            if not codes:
                print(" 没输入代码，已取消。")
                continue
            run_script(COMMANDS["stock"][0], codes)
            input("\n回车返回菜单……")
        elif choice == "4":
            print("\n正在抓取今日热门股……\n")
            run_script(COMMANDS["hot"][0])
            input("\n回车返回菜单……")
        elif choice == "5":
            base = os.path.dirname(find_script("report_server.py") or __file__)
            print(f"\n 项目目录：{base}")
            print(f" 每日报告：{os.path.join(base, '报告归档', '每日')}")
            print(f" 低值复苏：{os.path.join(base, '报告归档', '低值复苏')}")
            print(f" 自选股文件：{os.path.join(base, '自选股.txt')}")
            input("\n回车返回菜单……")
        elif choice == "0":
            return 0
        else:
            print(" 无效选项，请重新选择。")


def main(argv):
    if len(argv) > 1:
        cmd = argv[1].strip().lower()
        if cmd in ("-h", "--help", "help", "?"):
            print(__doc__)
            return 0
        if cmd in COMMANDS:
            return run_script(COMMANDS[cmd][0], argv[2:])
        print(f"[{APP_NAME}] 未知命令：{argv[1]}\n")
        print("可用命令：")
        for k, (script, desc) in COMMANDS.items():
            print(f"  {k:<8} {desc}")
        return 2
    return _menu()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
