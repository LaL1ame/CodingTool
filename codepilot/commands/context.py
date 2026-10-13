"""命令界面控制接口(state14) —— CommandContext 协议 + StatusSnapshot。

命令实现只依赖本协议,不 import 具体渲染框架(Textual)。当前由
`codepilot.ui.app` 的 `_CommandContextImpl` 提供实现;以后换渲染框架
另实现一套本协议即可复用全部命令(F4)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from codepilot.modes import ReleasePolicy, ToolAxis


@dataclass
class StatusSnapshot:
    """查询命令('/status' '/permission' '/session')读取的运行状态快照。"""

    axis_label: str          # "DEFAULT"(执行) | "PLAN"(计划)
    release: str             # manual | auto | strict
    accept_edits: bool
    model: str
    tokens_in: int
    tokens_out: int
    ctx_usage: str
    elapsed_s: float
    session_file: str | None


class CommandContext(Protocol):
    """命令触达界面的唯一通道。命令通过它 show/切换/查询,不碰 App/Textual。"""

    def show(self, message: str) -> None:
        """写一行消息到会话展示区(RichLog)。"""

    def submit_to_ai(self, text: str) -> None:
        """把一条用户消息送进 AI 循环(等价在输入框直接发送该文本)。"""

    def set_axis(self, axis: ToolAxis, *, announce: bool = False) -> None:
        """切换工具轴(PLAN 计划只读 / BUILD 执行全工具)。"""

    def set_release(self, policy: ReleasePolicy) -> None:
        """设置放行策略(manual/auto/strict)。"""

    def set_accept_edits(self, on: bool | None = None) -> None:
        """设置自动编辑开关;None 表示翻转当前值。"""

    def compact(self) -> None:
        """触发异步上下文压缩(编排内部 run_worker,不阻塞)。"""

    def resume_loop(self) -> None:
        """续接暂停的 Agent 循环(/continue 编排,内部 run_worker)。"""

    def delete_memory(self, keyword: str) -> int:
        """删除含关键词的记忆条目,返回删除数。"""

    def memory_report(self) -> str | None:
        """返回当前记忆分组展示块;记忆未启用时返回 None。"""

    def clear_display(self) -> None:
        """清空屏幕显示(RichLog),保留会话历史与上下文。"""

    def snapshot(self) -> StatusSnapshot:
        """返回当前运行状态快照。"""

    def exit_app(self) -> None:
        """退出应用。"""