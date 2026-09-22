"""UI 调色板：每工具图标/强调色 + 每模式状态色。

集中定义,供 tcss 与 markup 字符串共享,避免散落内联色值。
"""
from __future__ import annotations

# —— 工具图标与强调色 ——
TOOL_ICONS: dict[str, str] = {
    "read": "📄",
    "write": "✍️",
    "edit": "✏️",
    "glob": "🔎",
    "grep": "🔍",
    "run": "🛠️",
}

# Textual/Rich 支持的标准色名
TOOL_COLORS: dict[str, str] = {
    "read": "cyan",
    "write": "green",
    "edit": "yellow",
    "glob": "magenta",
    "grep": "cyan",
    "run": "orange",
}

# —— 模式状态色 ——
TOOL_AXIS_COLORS = {
    "plan": "purple",     # plan 只读
    "build": "green",     # build 全工具
}

RELEASE_COLORS = {
    "manual": "cyan",     # 未命中→问
    "auto": "orange",     # 未命中→放
    "strict": "red",      # 未命中→拒
}

ACCEPT_EDITS_COLOR = "green"


def tool_icon(name: str) -> str:
    return TOOL_ICONS.get(name, "🔧")


def tool_color(name: str) -> str:
    return TOOL_COLORS.get(name, "cyan")


def axis_color(axis: str) -> str:
    return TOOL_AXIS_COLORS.get(axis, "green")


def release_color(policy: str) -> str:
    return RELEASE_COLORS.get(policy, "cyan")