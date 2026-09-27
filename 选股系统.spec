# -*- mode: python ; coding: utf-8 -*-
"""
选股系统 · PyInstaller 打包配置（onedir）
==========================================
打包： pyinstaller --noconfirm --clean 选股系统.spec
产物： dist\\选股系统\\选股系统.exe（整个 dist\\选股系统 文件夹就是绿色版）

说明：
  - 源码 .py 作为数据文件一起打包，EXE 启动时用 runpy 运行它们，
    这样各模块里 BASE = dirname(__file__) 的路径假设全部保持原样，
    数据（数据\\、报告归档\\、自选股.txt、日志\\）落在 EXE 所在目录。
  - EXE 旁边若已存在源码（例如直接放在项目根目录），则优先跑那份源码，
    直接沿用项目里现有的数据和报告归档。
"""
import glob
import os
import shutil
import ast
import sys
import pathlib

ROOT = os.path.abspath(os.getcwd())


def _project_stdlib_imports():
    """扫描项目所有 .py，找出运行时需要的「标准库」顶层模块，
    用于 hiddenimports：项目自身模块改为从磁盘加载（不冻结进 PYZ）后，
    这些 stdlib 不会再被自动带入冻结环境，必须显式列出，否则运行时会
    ModuleNotFoundError（如 webbrowser）。"""
    roots = [".", "紫苏叶选股", "tools", "cnfinancialscraper"]
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
                        names.add(a.name)                 # 完整名，如 http.server
                        names.add(a.name.split(".")[0])   # 顶层，如 http
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names.add(node.module)
                    names.add(node.module.split(".")[0])
    # 只要顶层属于标准库即视为需打包（其下子模块皆为 stdlib）
    return sorted(m for m in names
                  if m.split(".")[0] in stdlib and m.split(".")[0] not in skip)


def _collect():
    datas = []
    # 根目录源码与说明文件
    for pat in ("*.py", "*.cmd", "*.md", "*.txt", "*.js"):
        for path in glob.glob(os.path.join(ROOT, pat)):
            if os.path.basename(path) == "选股系统.spec":
                continue
            datas.append((path, "."))
    # 紫苏叶选股（子模块 + 底稿库/模板/样例）
    for pat in ("*.py", "*.json", "*.md"):
        for path in glob.glob(os.path.join(ROOT, "紫苏叶选股", pat)):
            datas.append((path, "紫苏叶选股"))
    for path in glob.glob(os.path.join(ROOT, "紫苏叶选股", "样例", "*")):
        datas.append((path, os.path.join("紫苏叶选股", "样例")))
    # 工具
    for path in glob.glob(os.path.join(ROOT, "tools", "*")):
        if os.path.isfile(path):
            datas.append((path, "tools"))
    # 主数据源抓取器（cnfinancialscraper）：缺失时 daily_report 会降级到 eastmoney.py，
    # 但打包进来可用到更完整的行情抓取
    for path in glob.glob(os.path.join(ROOT, "cnfinancialscraper", "**", "*"), recursive=True):
        if os.path.isfile(path):
            rel = os.path.relpath(path, ROOT).replace("/", os.sep)
            datas.append((path, os.path.dirname(rel)))
    return datas


a = Analysis(
    ["打包入口.py"],
    pathex=[ROOT],
    binaries=[],
    datas=_collect(),
    # 注意：项目自身模块（daily_report/selection/db/...）一律不冻结进 PYZ，
    # 而是作为源码 .py 放在 EXE 旁边，运行时由 打包入口 用 runpy 从磁盘加载。
    # 这样它们各自的 BASE = dirname(__file__) 都指向 EXE 目录，
    # 数据（数据\、报告归档\、自选股.txt）才会落在正确位置并可正常读写更新。
    # 只冻结真正的第三方运行时依赖 + 项目运行所需的 stdlib；
    # 项目模块若在 PYZ 里，其 __file__ 会指向 _internal 临时目录，
    # 导致数据被写到临时目录、页面读到的却始终是旧的。
    hiddenimports=["requests"] + _project_stdlib_imports(),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PyQt5", "PySide6",
              "IPython", "notebook", "pytest", "setuptools", "pip"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="选股系统",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="选股系统",
)

# ---- 打包后处理：把源码/资源同时放一份到 EXE 旁边 ----
# 这样 EXE 启动时找到的是"身边的源码"，数据（数据\、报告归档\、自选股.txt、日志\）
# 就直接落在 EXE 所在目录，用户一眼能找到；改源码也能立刻生效。
_dest_root = os.path.join(DISTPATH, "选股系统")
for _src, _rel in _collect():
    _dst_dir = _dest_root if _rel == "." else os.path.join(_dest_root, _rel)
    os.makedirs(_dst_dir, exist_ok=True)
    try:
        shutil.copy2(_src, os.path.join(_dst_dir, os.path.basename(_src)))
    except Exception as _e:
        print(f"[spec] 复制失败 {_src}: {_e}")
print(f"[spec] 已把源码/资源复制到 {_dest_root}（EXE 旁，数据将落在此目录）")
