"""Lead 编排工具：建组/派人/终止/消息/审批/任务板。

只注册进主 Agent 的 Lead registry；成员与普通子 Agent 不可见。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codepilot.protocols import ToolResult
from codepilot.tools.base import Tool

if TYPE_CHECKING:
    from codepilot.teams.manager import TeamManager

LEAD_TOOL_NAMES = frozenset({
    "team_create", "team_list", "team_use", "team_show",
    "team_spawn", "team_terminate", "team_send", "team_broadcast",
    "team_decide", "team_inbox", "team_task_create", "team_task_update",
    "team_task_list",
})


class _LeadTool(Tool):
    system_exempt = True

    def __init__(self, manager: "TeamManager") -> None:
        self._manager = manager

    async def execute(self, **kwargs) -> ToolResult:
        try:
            output = await self._run(**kwargs)
            return ToolResult(call_id="", name=self.name, output=output)
        except Exception as e:  # noqa: BLE001 - 工具错误回灌模型
            return ToolResult(call_id="", name=self.name, error=str(e))

    async def _run(self, **kwargs) -> str:
        raise NotImplementedError


class TeamCreateTool(_LeadTool):
    name = "team_create"
    description = "创建一个长期小组并设为当前组。名称限小写字母/数字/._-，最长 24。"
    side_effect = True
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    async def _run(self, **kwargs) -> str:
        meta = self._manager.create_team(kwargs["name"])
        return f"已创建小组 {meta.name}（负责人 {meta.lead}）"


class TeamListTool(_LeadTool):
    name = "team_list"
    description = "列出用户目录下的所有小组与当前组。"
    parameters = {"type": "object", "properties": {}}

    async def _run(self, **kwargs) -> str:
        teams = self._manager.list_teams()
        if not teams:
            return "还没有小组。用 team_create 创建。"
        current = self._manager.team_name
        return "\n".join(
            f"{'*' if t == current else ' '} {t}" for t in teams)


class TeamUseTool(_LeadTool):
    name = "team_use"
    description = "切换当前小组（会停止原组正在运行的成员并置 offline）。"
    side_effect = True
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    async def _run(self, **kwargs) -> str:
        meta = self._manager.use_team(kwargs["name"])
        return f"当前小组：{meta.name}"


class TeamShowTool(_LeadTool):
    name = "team_show"
    description = "查看当前小组的成员状态、任务计数与 worktree 路径。"
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
    }

    async def _run(self, **kwargs) -> str:
        name = kwargs.get("name")
        current = self._manager.team_name
        if name and name != current:
            raise ValueError(f"只能查看当前小组 {current!r}；先 team_use {name}")
        return self._manager.team_summary()


class TeamSpawnTool(_LeadTool):
    name = "team_spawn"
    description = (
        "派生团队成员。backend=auto 选择已实现后端；terminal 本阶段未实现会报错。"
        "requires_approval=true 时成员每个任务需先提交计划。")
    side_effect = True
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "role": {"type": "string"},
            "backend": {
                "type": "string", "enum": ["auto", "inprocess", "terminal"],
            },
            "requires_approval": {"type": "boolean"},
        },
        "required": ["name", "role"],
    }

    async def _run(self, **kwargs) -> str:
        member = await self._manager.spawn_member(
            name=kwargs["name"], role=kwargs["role"],
            backend=kwargs.get("backend", "auto"),
            requires_approval=bool(kwargs.get("requires_approval", False)))
        return (f"已派生成员 {member.name}（{member.role}，"
                f"{member.backend.value}）：{member.worktree_path}")


class TeamTerminateTool(_LeadTool):
    name = "team_terminate"
    description = "终止成员；停止协程但保留其 worktree 与分支。"
    side_effect = True
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    async def _run(self, **kwargs) -> str:
        member = await self._manager.terminate_member(kwargs["name"])
        return (f"已终止成员 {member.name}；worktree 保留："
                f"{member.worktree_path}（{member.branch}）")


class TeamSendTool(_LeadTool):
    name = "team_send"
    description = "以 Lead 身份向某个成员发送点对点文本消息。"
    side_effect = True
    parameters = {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "body"],
    }

    async def _run(self, **kwargs) -> str:
        msg = self._manager.send(
            sender="lead", recipient=kwargs["to"], type="text",
            body=kwargs["body"])
        return f"消息已发送给 {msg.recipient}（{msg.id[:8]}）"


class TeamBroadcastTool(_LeadTool):
    name = "team_broadcast"
    description = "以 Lead 身份向全体成员广播文本消息。"
    side_effect = True
    parameters = {
        "type": "object",
        "properties": {"body": {"type": "string"}},
        "required": ["body"],
    }

    async def _run(self, **kwargs) -> str:
        delivered, failures = self._manager.broadcast(
            sender="lead", body=kwargs["body"])
        text = f"广播送达 {len(delivered)} 人"
        if failures:
            text += "；失败: " + ", ".join(f"{n}（{r}）" for n, r in failures)
        return text


class TeamDecideTool(_LeadTool):
    name = "team_decide"
    description = "批准/驳成员针对某个任务的计划（plan_decision）。"
    side_effect = True
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer"},
            "approved": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["task_id", "approved"],
    }

    async def _run(self, **kwargs) -> str:
        msg = self._manager.decide(
            task_id=int(kwargs["task_id"]),
            approved=bool(kwargs["approved"]),
            reason=kwargs.get("reason", ""))
        verdict = "批准" if msg.approved else "驳回"
        return f"已{verdict}任务 #{msg.task_id} 的计划（发给 {msg.recipient}）"


class TeamInboxTool(_LeadTool):
    name = "team_inbox"
    description = "读取 Lead 邮箱中的未读消息（标记为已读）。"
    side_effect = True
    parameters = {
        "type": "object",
        "properties": {"limit": {"type": "integer"}},
    }

    async def _run(self, **kwargs) -> str:
        limit = int(kwargs.get("limit", 20))
        messages = self._manager.lead_inbox(limit=limit)
        if not messages:
            return "Lead 邮箱没有未读消息"
        lines = []
        for m in messages:
            task = f" task=#{m.task_id}" if m.task_id is not None else ""
            extra = f" approved={m.approved}" if m.type.value == (
                "plan_decision") else ""
            lines.append(
                f"[{m.type.value}] {m.sender}{task}{extra}：{m.body}")
        return "\n".join(lines)


class TeamTaskCreateTool(_LeadTool):
    name = "team_task_create"
    description = "在当前小组创建共享任务，可带依赖与初始 assignee。"
    side_effect = True
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "depends_on": {"type": "array", "items": {"type": "integer"}},
            "assignee": {"type": "string"},
        },
        "required": ["title"],
    }

    async def _run(self, **kwargs) -> str:
        task = self._manager.create_task(
            title=kwargs["title"],
            description=kwargs.get("description", ""),
            depends_on=kwargs.get("depends_on") or [],
            assignee=kwargs.get("assignee"))
        return (f"已创建任务 #{task.id}: {task.title}"
                f"（assignee={task.assignee or '-'}）")


class TeamTaskUpdateTool(_LeadTool):
    name = "team_task_update"
    description = "更新任务标题/描述/依赖/assignee/状态；Lead 可重开已完成任务。"
    side_effect = True
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "depends_on": {"type": "array", "items": {"type": "integer"}},
            "assignee": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "done", "cancelled"],
            },
            "result": {"type": "string"},
        },
        "required": ["task_id"],
    }

    async def _run(self, **kwargs) -> str:
        task_id = int(kwargs.pop("task_id"))
        fields = {k: v for k, v in kwargs.items() if v is not None}
        task = self._manager.update_task(task_id, **fields)
        return f"任务 #{task.id} 已更新（status={task.status.value}）"


class TeamTaskListTool(_LeadTool):
    name = "team_task_list"
    description = "查看当前小组的任务板。"
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

    async def _run(self, **kwargs) -> str:
        tasks = self._manager.list_tasks(
            status=kwargs.get("status"), assignee=kwargs.get("assignee"))
        if not tasks:
            return "任务板为空（或没有匹配任务）"
        return "\n".join(
            f"#{t.id} [{t.status.value}] {t.title} | "
            f"assignee={t.assignee or '-'} | deps={t.depends_on or '-'}"
            for t in tasks)


def build_lead_tools(manager: "TeamManager") -> list[Tool]:
    return [
        TeamCreateTool(manager),
        TeamListTool(manager),
        TeamUseTool(manager),
        TeamShowTool(manager),
        TeamSpawnTool(manager),
        TeamTerminateTool(manager),
        TeamSendTool(manager),
        TeamBroadcastTool(manager),
        TeamDecideTool(manager),
        TeamInboxTool(manager),
        TeamTaskCreateTool(manager),
        TeamTaskUpdateTool(manager),
        TeamTaskListTool(manager),
    ]
