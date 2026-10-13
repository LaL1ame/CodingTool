"""state13 长期记忆子包：指令文件 + 自动笔记 + 注入。

惰性导出(PEP 562):子模块按需加载,部分开发/单测阶段不会因未写全
某模块而整体导入失败。
"""
from __future__ import annotations

import importlib

_MODULES = ("resolver", "store", "auto", "inject")
_SYMBOLS = ("CATEGORIES", "NoteStore", "MemoryInjector")


_SYMBOL_MODULE = {"CATEGORIES": "store", "NoteStore": "store", "MemoryInjector": "inject"}


def __getattr__(name: str):
    if name in _MODULES:
        return importlib.import_module(f"codepilot.memory.{name}")
    if name in _SYMBOL_MODULE:
        mod = importlib.import_module(f"codepilot.memory.{_SYMBOL_MODULE[name]}")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(_MODULES + _SYMBOLS)