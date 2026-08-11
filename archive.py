# -*- coding: utf-8 -*-
"""
报告归档：超过保留天数的旧报告自动按月压缩进 报告归档/YYYY-MM.zip 并删除原件，
防止 HTML 报告越积越多占用磁盘。
"""
import datetime
import os
import re
import zipfile

ARCHIVE_DIR_NAME = "报告归档"
DEFAULT_KEEP_DAYS = 30
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ARCHIVE_DIR = os.path.join(PROJECT_ROOT, ARCHIVE_DIR_NAME)
PATTERNS = [
    r"每日量化选股报告_(\d{8})\.html",
    r"每日热门股_(\d{8})\.txt",
    r"用户选股量化_(\d{8})\.html",
]


def archive_old_reports(base, keep_days=DEFAULT_KEEP_DAYS,
                        archive_dir=DEFAULT_ARCHIVE_DIR):
    """把 base 下超过 keep_days 天的报告文件移入项目根 报告归档/YYYY-MM.zip"""
    today = datetime.date.today()
    os.makedirs(archive_dir, exist_ok=True)
    archived = 0
    for name in os.listdir(base):
        m = None
        for pat in PATTERNS:
            m = re.fullmatch(pat, name)
            if m:
                break
        if not m:
            continue
        try:
            d = datetime.datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if (today - d).days <= keep_days:
            continue
        path = os.path.join(base, name)
        month = d.strftime("%Y-%m")
        zip_path = os.path.join(archive_dir, f"{month}.zip")
        entries = {}
        if os.path.exists(zip_path):
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    for info in zf.infolist():
                        entries[info.filename] = zf.read(info.filename)
            except Exception:
                entries = {}
        with open(path, "rb") as f:
            entries[name] = f.read()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, data in entries.items():
                zf.writestr(fname, data)
        os.remove(path)
        archived += 1
    return archived
