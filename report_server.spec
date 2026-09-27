# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把每日量化报告服务打成单个 EXE。

用法:
    pyinstaller report_server.spec --clean --noconfirm

产出:
    dist\\选股报告服务.exe

说明:
  - 数据目录（数据/报告归档/自选股.txt/日志）不打包，而是长在 EXE 旁边，
    由 app_paths.py 在 frozen 模式下把 BASE 指向 EXE 所在目录实现。
  - echarts.min.js 既打进 EXE 内部，也允许放在 EXE 同目录覆盖，
    由 report_server.resource_path() 优先取同目录版本。
"""

# 运行时通过 import 动态加载的模块，PyInstaller 静态分析抓不到，必须显式列出
# 注意：项目自身模块（daily_report/selection/db/...）一律不冻结进 PYZ，
# 而是作为源码 .py 放在 EXE 旁边，运行时从磁盘加载，使各模块 BASE 指向 EXE 目录、
# 数据读写落到正确位置并可正常更新。这里只保留真正的第三方运行时依赖 + 所需的 stdlib。
import ast
import sys
import pathlib


def _project_stdlib_imports():
    roots = [".", "cnfinancialscraper", "紫苏叶选股"]
    stdlib = set(getattr(sys, "stdlib_module_names", set()))
    skip = {"tkinter", "turtle", "test", "unittest", "doctest", "pydoc",
            "idlelib", "lib2to3", "ensurepip", "venv", "distutils",
            "pip", "setuptools", "email"}
    names = set()
    for r in roots:
        for p in pathlib.Path(r).rglob("*.py"):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        names.add(a.name)
                        names.add(a.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names.add(node.module)
                    names.add(node.module.split(".")[0])
    return sorted(m for m in names
                  if m.split(".")[0] in stdlib and m.split(".")[0] not in skip)


HIDDEN = ["requests"] + _project_stdlib_imports()

# 重依赖：主流程用不到，漏进来体积会翻几倍
EXCLUDES = [
    "playwright", "matplotlib", "bs4", "lxml", "openpyxl", "docx", "pypdf",
    "numpy", "pandas", "scipy", "sklearn", "PIL", "tkinter",
    "PySide6", "PyQt5", "PyQt6", "IPython", "pytest", "notebook",
    "selenium", "httpx", "aiohttp", "jinja2", "flask", "tornado",
    "torch", "tensorflow", "cv2", "skimage",
]

a = Analysis(
    ["report_server.py"],
    pathex=[".", "cnfinancialscraper", "紫苏叶选股"],
    binaries=[],
    datas=[("echarts.min.js", ".")],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    cipher=None,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="选股报告服务",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
