"""正交模式状态机 —— 对齐 Claude Code 的模式语言。

三个互相独立的开关(不是单态循环):
- ToolAxis.tool_axis: PLAN(只读) / BUILD(全工具)   ← 由 /plan /do 切换
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

# 放行模式循环(Claude Code 式:放行策略 + 自动编辑合并演进)
RELEASE_CYCLE: list[tuple[ReleasePolicy, bool]] = [
    (ReleasePolicy.MANUAL, False),  # 默认:每次询问 + 手动确认编辑
    (ReleasePolicy.AUTO, True),     # 自由:未命中即放行 + 自动应用编辑
    (ReleasePolicy.STRICT, False),  # 严格:收窄 + 手动确认编辑
]


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

    def cycle_release(self) -> tuple[ReleasePolicy, bool]:
        """Claude Code 式循环:MANUAL→AUTO→STRICT,并同步 accept_edits。"""
        idx = next(i for i, (p, _) in enumerate(RELEASE_CYCLE)
                   if p is self.release)
        next_release, next_ae = RELEASE_CYCLE[(idx + 1) % len(RELEASE_CYCLE)]
        self.release = next_release
        self.accept_edits = next_ae
        return next_release, next_ae

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