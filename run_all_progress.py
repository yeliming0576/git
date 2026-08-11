# -*- coding: utf-8 -*-
"""一键运行（带进度条）：每日报告 + 组合模拟盘，实时显示进度"""
import datetime
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import progress  # noqa: E402

LOG = os.path.join(BASE, "自动运行日志.txt")


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def start_web():
    """启动网页服务（后台）并打开浏览器；已运行时直接打开"""
    port = 8765
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        already = sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()
    if already:
        webbrowser.open(f"http://127.0.0.1:{port}/")
        return "网页服务已在运行，浏览器已打开"
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    subprocess.Popen(
        [pythonw, os.path.join(BASE, "report_server.py"), "--no-browser",
         "--port", str(port)],
        cwd=BASE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(2.5)
    webbrowser.open(f"http://127.0.0.1:{port}/")
    return "网页服务已启动，浏览器已打开（可刷新/编辑/局域网共享）"


def worker():
    try:
        log("==== 手动一键运行开始 ====")
        import daily_report
        daily_report.main()
        import paper_trade
        paper_trade.main()
        log("==== 手动一键运行完成 ====")
        msg = start_web()
        log(msg)
        progress.report("完成", 100, msg)
    except Exception as e:
        log(f"运行出错: {e}")
        progress.report("出错", 0, f"运行出错: {e}")


def main():
    root = tk.Tk()
    root.title("选股系统 · 一键运行")
    root.geometry("540x200")
    root.resizable(False, False)

    stage_var = tk.StringVar(value="准备开始...")
    msg_var = tk.StringVar(value="")
    tk.Label(root, text="选股系统一键运行", font=("Microsoft YaHei", 14, "bold")).pack(pady=(18, 6))
    tk.Label(root, textvariable=stage_var, font=("Microsoft YaHei", 10)).pack()
    bar = ttk.Progressbar(root, maximum=100, length=470, mode="determinate")
    bar.pack(pady=12)
    tk.Label(root, textvariable=msg_var, fg="#4b5563", font=("Microsoft YaHei", 9)).pack()
    tk.Label(root, text="运行中请勿关闭窗口（约3~5分钟）", fg="#9aa4b2",
             font=("Microsoft YaHei", 9)).pack(pady=(10, 0))

    def update(stage, pct, msg):
        stage_var.set(f"{stage}  {pct:.0f}%")
        bar["value"] = pct
        msg_var.set(msg)
        if stage == "完成":
            stage_var.set("完成  100%")
            msg_var.set("报告已更新，可查看：自动运行日志.txt")
            root.after(2500, root.destroy)
        elif stage == "出错":
            msg_var.set(msg)
            root.after(6000, root.destroy)

    def on_progress(stage, pct, msg):
        root.after(0, lambda: update(stage, pct, msg))

    progress.set_callback(on_progress)
    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
