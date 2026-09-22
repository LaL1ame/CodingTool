"""正交模式状态机 —— 对齐 Claude Code 的模式语言。

三个互相独立的开关(不是单态循环):
- ToolAxis.tool_axis: PLAN(只读) / BUILD(全工具)   ← Shift+Tab 切换
- ReleasePolicy.release: MANUAL / AUTO / STRICT     ← 未命中规则的兜底
- bool.accept_edits: 是否自动应用 write/edit(不弹框)

ModeState 是纯逻辑(无 PermissionManager/Agent 依赖),便于单测;
TUI 与后端之间的翻译(映射到 PermissionMode / set_plan_mode)由 App 负责(DIP)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ToolAxis(Enum):
    PLAN = "plan"
    BUILD = "build"


class ReleasePolicy(Enum):
    MANUAL = "manual"
    AUTO = "auto"
    STRICT = "strict"


# ReleasePolicy → PermissionMode 名映射(App 据此选 PermissionMode)
RELEASE_TO_PERMISSION = {
    ReleasePolicy.MANUAL: "DEFAULT",
    ReleasePolicy.AUTO: "PERMISSIVE",
    ReleasePolicy.STRICT: "STRICT",
}


@dataclass
class ModeState:
    tool_axis: ToolAxis = ToolAxis.BUILD
    release: ReleasePolicy = ReleasePolicy.MANUAL
    accept_edits: bool = False
    # 保留用户显式 /auto /manual /strict 历史,便于命令面反馈
    _command_names: set[str] = field(default_factory=set)

    # —— 工具轴(Shift+Tab) ——
    def cycle_tool_axis(self) -> ToolAxis:
        """PLAN ⇄ BUILD 双向切换,返回切换后的轴。"""
        self.tool_axis = (
            ToolAxis.PLAN if self.tool_axis is ToolAxis.BUILD else ToolAxis.BUILD
        )
        return self.tool_axis

    def set_axis(self, axis: ToolAxis) -> None:
        self.tool_axis = axis

    # —— 放行策略 ——
    def set_release(self, policy: ReleasePolicy) -> None:
        self.release = policy

    def release_permission_name(self) -> str:
        """映射为 PermissionMode 名(由 App 转换成枚举)。"""
        return RELEASE_TO_PERMISSION[self.release]

    # —— 自动编辑 ——
    def toggle_accept_edits(self, on: None | bool = None) -> bool:
        self.accept_edits = on if on is not None else not self.accept_edits
        return self.accept_edits

    # —— 序列化(resume 用) ——
    def snapshot(self) -> dict:
        return {
            "tool_axis": self.tool_axis.value,
            "release": self.release.value,
            "accept_edits": self.accept_edits,
        }

    def restore(self, data: dict) -> None:
        self.tool_axis = ToolAxis(data.get("tool_axis", ToolAxis.BUILD.value))
        self.release = ReleasePolicy(data.get("release", ReleasePolicy.MANUAL.value))
        self.accept_edits = bool(data.get("accept_edits", False))