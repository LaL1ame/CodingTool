"""agent 工具 —— 子 Agent 委派入口（F2）。

常驻系统级工具：system_exempt=True（不受 Skill/角色白名单约束）、
side_effect=False（委派动作本身不改文件，D8）。参数分流 defined/fork。
工具自身永不抛错——失败以 ToolResult(error=...) 形式返回（F2）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codepilot.protocols import ToolResult
from codepilot.tools.base import Tool

if TYPE_CHECKING:
    from codepilot.subagents.manager import SubAgentManager


class AgentTool(Tool):
    """统一 agent 工具：主 Agent 用它委派子任务给独立子 Agent。"""

    name = "agent"

    @property
    def description(self) -> str:
        roles = "、".join(sorted(self._manager.known)) or "无"
        return (
            "委派子任务给独立的子 Agent（干净上下文、受限工具集）。"
            "defined：按角色执行（需 role + prompt）；"
            "fork：继承当前对话历史与工具集（强制后台，prompt 必填）。"
            "可设 background 显式转后台、timeout 前台超时自动转后台。"
            "声明 isolation: worktree 的角色可传 worktree 指定隔离目录名"
            "（小写、可含斜杠，如 auth-fix 或 feature/login）；省略则自动命名。"
            f"当前可用角色：{roles}。"
        )

    @property
    def parameters(self) -> dict:
        roles = sorted(self._manager.known)
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string", "enum": ["defined", "fork"],
                    "description": "defined=按角色执行；fork=继承当前上下文执行",
                },
                "role": {
                    "type": "string",
                    **({"enum": roles} if roles else {}),
                    "description": "defined 模式的角色名（frontmatter name）",
                },
                "prompt": {
                    "type": "string",
                    "description": "委派给子 Agent 的任务描述",
                },
                "background": {
                    "type": "boolean",
                    "description": "true=立即转后台执行，不阻塞当前循环",
                },
                "timeout": {
                    "type": "number",
                    "description": "前台执行超时（秒），超时自动转后台",
                },
                "worktree": {
                    "type": "string",
                    "description": (
                        "隔离工作目录名（仅 isolation: worktree 的角色可用）。"
                        "小写分段，可含斜杠；省略则自动生成 <role>-<task_id>。"
                    ),
                },
            },
            "required": ["mode", "prompt"],
        }

    @property
    def system_exempt(self) -> bool:
        return True  # 系统级工具，任何白名单/plan 下常驻可见（F2/AC2）

    @property
    def side_effect(self) -> bool:
        return False  # 委派动作本身不改文件（D8）

    def __init__(self, manager: "SubAgentManager") -> None:
        self._manager = manager

    async def execute(self, **kwargs) -> ToolResult:
        try:
            mode = kwargs.get("mode")
            prompt = kwargs.get("prompt", "")
            role = kwargs.get("role")
            background = bool(kwargs.get("background", False))
            timeout = kwargs.get("timeout")
            worktree = kwargs.get("worktree")
            worktree = str(worktree) if worktree not in (None, "") else None
            if not mode:
                return ToolResult(call_id="", name=self.name, error="agent 工具需指定 mode（defined/fork）")
            if not prompt or not str(prompt).strip():
                return ToolResult(call_id="", name=self.name, error="agent 工具需指定 prompt")
            timeout = float(timeout) if timeout not in (None, "") else None
            return await self._manager.delegate(
                mode, str(prompt), role=role,
                background=background, timeout=timeout, worktree=worktree)
        except Exception as e:  # 工具自身永不抛错（F2）
            return ToolResult(call_id="", name=self.name, error=f"agent 工具异常: {e}")
