"""成员协作工具：任务 CRUD/领取 + 点对点消息/广播。

这些工具只注册进成员 Agent 的 registry；Lead 与普通子 Agent 永远看不到。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from codepilot.protocols import ToolResult
from codepilot.tools import (
    EditTool,
    GlobTool,
    GrepTool,
    ReadTool,
    RunTool,
    WriteTool,
)
from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from codepilot.teams.manager import TeamManager
    from codepilot.teams.model import Task
    from codepilot.teams.tasks import TaskBoard

MEMBER_TOOL_NAMES = frozenset({
    "task_list", "task_get", "task_create", "task_update",
    "task_delete", "task_claim", "send_message", "broadcast",
})

_BOUND_TOOL_NAMES = frozenset({"read", "write", "edit", "glob", "grep", "run"})
_EXCLUDED_SYSTEM = frozenset({"agent", "load_skill"})


def _task_line(task: "Task") -> str:
    deps = ",".join(f"#{d}" for d in task.depends_on) or "-"
    assignee = task.assignee or "-"
    extra = f" | result: {task.result[:120]}" if task.result else ""
    return (f"#{task.id} [{task.status.value}] {task.title} | "
            f"assignee={assignee} | deps={deps}{extra}")


class _MemberTool(Tool):
    def __init__(
        self,
        board: "TaskBoard",
        manager: "TeamManager",
        member: str,
    ) -> None:
        self._board = board
        self._manager = manager
        self._member = member

    async def execute(self, **kwargs) -> ToolResult:
        try:
            output = self._run(**kwargs)
            return ToolResult(call_id="", name=self.name, output=output)
        except Exception as e:  # noqa: BLE001 - 工具错误回灌模型
            return ToolResult(call_id="", name=self.name, error=str(e))

    def _run(self, **kwargs) -> str:
        raise NotImplementedError


class TaskListTool(_MemberTool):
    name = "task_list"
    description = "列出共享任务板；可按状态或成员过滤。"
    parameters = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "done", "cancelled"],
            },
            "assignee": {"type": "string"},
        },
    }

    def _run(self, **kwargs) -> str:
        tasks = self._board.list(
            status=kwargs.get("status"), assignee=kwargs.get("assignee"))
        if not tasks:
            return "任务板为空（或没有匹配任务）"
        return "\n".join(_task_line(t) for t in tasks)


class TaskGetTool(_MemberTool):
    name = "task_get"
    description = "读取单个共享任务详情。"
    parameters = {
        "type": "object",
        "properties": {"task_id": {"type": "integer"}},
        "required": ["task_id"],
    }

    def _run(self, **kwargs) -> str:
        task = self._board.get(int(kwargs["task_id"]))
        return (f"#{task.id} {task.title}\n状态: {task.status.value}\n"
                f"assignee: {task.assignee or '-'}\n"
                f"依赖: {task.depends_on or '-'}\n"
                f"描述: {task.description or '-'}\n"
                f"结果: {task.result or '-'}")


class TaskCreateTool(_MemberTool):
    name = "task_create"
    side_effect = True
    description = "在共享任务板创建任务（可带依赖）。"
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "depends_on": {
                "type": "array", "items": {"type": "integer"},
            },
        },
        "required": ["title"],
    }

    def _run(self, **kwargs) -> str:
        task = self._board.create(
            title=kwargs["title"],
            description=kwargs.get("description", ""),
            depends_on=kwargs.get("depends_on") or [],
        )
        return f"已创建任务 #{task.id}: {task.title}（deps={task.depends_on or '-'}）"


class TaskUpdateTool(_MemberTool):
    name = "task_update"
    side_effect = True
    description = (
        "更新任务标题/描述/依赖/状态。成员不能改 assignee；"
        "完成后用 status=done 并写 result。")
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "depends_on": {
                "type": "array", "items": {"type": "integer"},
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "done", "cancelled"],
            },
            "result": {"type": "string"},
        },
        "required": ["task_id"],
    }

    def _run(self, **kwargs) -> str:
        task_id = int(kwargs.pop("task_id"))
        fields = {k: v for k, v in kwargs.items() if v is not None}
        task = self._board.update(task_id, **fields)
        return f"任务 #{task.id} 已更新为 {task.status.value}"


class TaskDeleteTool(_MemberTool):
    name = "task_delete"
    side_effect = True
    description = "删除任务；被依赖或执行中的任务不能删除。"
    parameters = {
        "type": "object",
        "properties": {"task_id": {"type": "integer"}},
        "required": ["task_id"],
    }

    def _run(self, **kwargs) -> str:
        task_id = int(kwargs["task_id"])
        self._board.delete(task_id)
        return f"任务 #{task_id} 已删除"


class TaskClaimTool(_MemberTool):
    name = "task_claim"
    side_effect = True
    description = "领取未指派且依赖已完成的 pending 任务。"
    parameters = {
        "type": "object",
        "properties": {"task_id": {"type": "integer"}},
        "required": ["task_id"],
    }

    def _run(self, **kwargs) -> str:
        task = self._board.claim(int(kwargs["task_id"]), self._member)
        return f"已领取任务 #{task.id}: {task.title}"


class SendMessageTool(_MemberTool):
    name = "send_message"
    side_effect = True
    description = "向 Lead 或其他成员发送点对点文本消息。"
    parameters = {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "body"],
    }

    def _run(self, **kwargs) -> str:
        msg = self._manager.send(
            sender=self._member, recipient=kwargs["to"],
            type="text", body=kwargs["body"])
        return f"消息已发送给 {msg.recipient}（{msg.id[:8]}）"


class BroadcastTool(_MemberTool):
    name = "broadcast"
    side_effect = True
    description = "向小组全体成员（含 Lead）广播文本消息。"
    parameters = {
        "type": "object",
        "properties": {"body": {"type": "string"}},
        "required": ["body"],
    }

    def _run(self, **kwargs) -> str:
        delivered, failures = self._manager.broadcast(
            sender=self._member, body=kwargs["body"])
        if failures:
            return (f"广播已送达 {len(delivered)} 人；失败: "
                    + ", ".join(f"{n}（{r}）" for n, r in failures))
        return f"广播已送达 {len(delivered)} 人"


def build_member_tools(
    board: "TaskBoard",
    manager: "TeamManager",
    member: str,
) -> list[Tool]:
    return [
        TaskListTool(board, manager, member),
        TaskGetTool(board, manager, member),
        TaskCreateTool(board, manager, member),
        TaskUpdateTool(board, manager, member),
        TaskDeleteTool(board, manager, member),
        TaskClaimTool(board, manager, member),
        SendMessageTool(board, manager, member),
        BroadcastTool(board, manager, member),
    ]


def build_member_registry(
    base: ToolRegistry,
    workspace: Path,
    board: "TaskBoard",
    manager: "TeamManager",
    member: str,
    protected_roots: tuple[Path, ...] = (),
) -> ToolRegistry:
    """从 base registry 派生成员 registry。

    文件/命令工具重新绑定到成员 worktree；系统工具（agent/load_skill）排除，
    MCP 等其余工具共享；最后加入成员协作工具。
    """
    registry = ToolRegistry()
    registry.register(ReadTool(workspace))
    registry.register(WriteTool(workspace))
    registry.register(EditTool(workspace))
    registry.register(GlobTool(workspace))
    registry.register(GrepTool(workspace, protected_roots=protected_roots))
    registry.register(RunTool(workspace, protected_roots=protected_roots))
    for tool in base.list_all():
        if tool.name in _BOUND_TOOL_NAMES or tool.name in _EXCLUDED_SYSTEM:
            continue
        registry.register(tool)
    for tool in build_member_tools(board, manager, member):
        registry.register(tool)
    return registry
