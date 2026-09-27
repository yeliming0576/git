# -*- coding: utf-8 -*-
"""
统一的项目根目录定位（模块分层 + 打包成 EXE 后依然正确）
==========================================================
背景：核心模块都在 stockapp/ 包里，若各自用 dirname(__file__) 当 BASE，
数据目录会跟着代码跑进 stockapp/；PyInstaller onefile 模式下更糟：
__file__ 指向 %TEMP%\\_MEIxxxxx（随机目录、退出即删），数据关掉程序就丢。

本模块统一解析：
  - 源码运行：BASE = stockapp/ 的上一级 = 项目根目录
  - EXE 运行：BASE = EXE 所在目录，数据长在 EXE 旁边，整个文件夹拷走即可

用法：
      from stockapp.app_paths import BASE
      DATA_DIR = os.path.join(BASE, "数据")
"""
import os
import sys


def _resolve():
    # frozen：PyInstaller 打包后，数据与配置必须落在 EXE 旁边（可写、路径固定）
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # 源码运行：本文件在 stockapp 包里，项目根 = 包的上一级目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE = _resolve()


def sub(*parts):
    """BASE 下的子路径拼接，如 sub('数据', '选股数据.db')"""
    return os.path.join(BASE, *parts)
