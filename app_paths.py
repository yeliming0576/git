# -*- coding: utf-8 -*-
"""
统一的项目根目录定位（打包成 EXE 后依然正确）
================================================
背景：项目里多个模块都写 `BASE = os.path.dirname(os.path.abspath(__file__))`。
PyInstaller onefile 模式下 __file__ 指向 %TEMP%\\_MEIxxxxx（随机目录、退出即删），
数据 / 报告归档 / 自选股.txt 会被写进临时目录，关掉程序就丢。

改用本模块后：
  - 源码运行：BASE = 本文件所在目录（即项目根目录），行为与之前完全一致
  - EXE 运行：BASE = EXE 所在目录，数据长在 EXE 旁边，迁移时整个文件夹拷走即可

用法：把原来的
      try:
          from app_paths import BASE
      except ImportError:  # 兼容单独运行本文件的场景
          BASE = os.path.dirname(os.path.abspath(__file__))
  换成
      try:
          from app_paths import BASE
      except ImportError:
          BASE = os.path.dirname(os.path.abspath(__file__))
"""
import os
import sys


def _resolve():
    # frozen：PyInstaller 打包后，数据与配置必须落在 EXE 旁边（可写、路径固定）
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE = _resolve()


def sub(*parts):
    """BASE 下的子路径拼接，如 sub('数据', '选股数据.db')"""
    return os.path.join(BASE, *parts)
