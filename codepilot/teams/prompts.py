"""团队相关提示文本（纯函数，无 IO）。

Lead 块注入主 Agent 的 volatile 尾块；成员块注入成员 Agent 的 skill_injector。
任务提示把共享任务板内容翻译成一次可执行回合的输入。
"""

from __future__ import annotations

from codepilot.teams.model import (
    MemberRef,
    Task,
    TeamMember,
    TeamMessage,
    TeamMeta,
)


def lead_context_block(
    team: TeamMeta | None,
    members: list[MemberRef],
    task_counts: dict[str, int],
) -> str:
    """主 Agent 的 Lead 团队上下文。"""
    if team is None:
        return (
            "## 团队能力（Team Lead）\n"
            "当前没有小组。需要多人协作时，用 team_create 创建小组，"
            "再用 team_spawn 派生成员、team_task_create 建任务。\n"
            "成员协作工具（task_*/send_message/broadcast）不在你的工具集中；"
            "你使用 team_* 编排工具。")
    lines = [
        "## 团队能力（Team Lead）",
        f"当前小组：{team.name}（负责人 {team.lead}）",
        "成员：",
    ]
    if members:
        for m in members:
            approval = "需审批" if m.requires_approval else "免审批"
            lines.append(
                f"- {m.name} | {m.role} | {m.status.value} | {approval}"
                f" | {m.worktree_path}")
    else:
        lines.append("- （暂无成员）")
    counts = " | ".join(
        f"{k}={task_counts.get(k, 0)}"
        for k in ("pending", "in_progress", "done", "cancelled"))
    lines.append(f"任务：{counts}")
    lines.append(
        "你是 Lead：用 team_* 工具建组/派人/建任务/发消息/审批/终止；"
        "成员协作工具（task_*/send_message/broadcast）不在你的工具集中。"
        "成员消息到达时会收到通知，用 team_inbox 读取全文。"
        "等待成员时不要用 shell sleep 轮询；用 team_show/team_task_list 查看状态，"
        "或结束本回合等待事件通知。")
    return "\n".join(lines)


def pending_approvals_block(items: list[tuple[int, str, str]]) -> str:
    """Lead volatile 块：列出等待审批的计划摘要（state19 设计补充 C）。

    `items`: (task_id, member, plan_summary)。让 Lead 直接看到每份待审批计划
    在哪、内容概要，避免它靠 team_task_list 兜圈去找，从而触发空转误判。
    """
    if not items:
        return ""
    lines = ["## 待审批计划", "以下成员正等待你的 team_decide 决定："]
    for task_id, member, summary in items:
        head = " ".join(summary.split())
        if len(head) > 200:
            head = head[:200] + "…"
        lines.append(f"- 任务 #{task_id}（{member}）等待审批：{head}")
    lines.append(
        "需看计划全文用 team_inbox 读对应 plan_request，批准用 team_decide。")
    return "\n".join(lines)


def member_context_block(
    team: TeamMeta,
    member: TeamMember,
    roster: list[MemberRef],
) -> str:
    """成员系统提示中的团队上下文。"""
    approval = (
        "本成员每个任务开始前必须先提交计划，等待 Lead 批准；"
        "批准前不得写文件或执行任务。"
        if member.requires_approval else
        "本成员免审批，收到任务后可直接开始。"
    )
    lines = [
        "## 团队协作上下文",
        f"小组：{team.name}；成员：{member.name}；角色：{member.role}",
        f"工作目录：{member.worktree_path}",
        f"分支：{member.branch}",
        approval,
        f"同伴：{', '.join(r.name for r in roster) or '（暂无）'}",
        "协作规则：",
        "- 用 task_list/task_get/task_create/task_update/task_delete/task_claim 操作共享任务板。",
        "- 用 send_message 点对点、broadcast 群发；消息在任务边界消费，执行中不打断。",
        "- 完成任务后用 task_update 标记 done 并写 result；之后自然停下，Lead 会唤醒你。",
        "- 你没有 team_* 编排工具，也不能使用 agent/load_skill。",
    ]
    return "\n".join(lines)


def _format_messages(messages: list[TeamMessage]) -> str:
    if not messages:
        return ""
    lines = ["【任务边界收到的消息】"]
    for msg in messages:
        body = msg.body.strip()
        if len(body) > 2000:
            body = body[:2000] + "…（已截断）"
        lines.append(
            f"- 来自 {msg.sender}（{msg.type.value}）"
            f"{f' 任务 #{msg.task_id}' if msg.task_id is not None else ''}：{body}")
    return "\n".join(lines)


def task_execution_prompt(
    task: Task,
    *,
    unread: list[TeamMessage],
    approved_plan: str = "",
) -> str:
    deps = ", ".join(f"#{d}" for d in task.depends_on) or "无"
    lines = [
        f"请执行共享任务 #{task.id}：{task.title}",
        f"描述：{task.description or '（无）'}",
        f"依赖：{deps}",
    ]
    if approved_plan.strip():
        lines.append(f"\n【已批准的计划】\n{approved_plan.strip()}")
    messages = _format_messages(unread)
    if messages:
        lines.append(f"\n{messages}")
    lines.append(
        "\n要求：完成任务后用 task_update 标记 done 并写 result；"
        "不要伪造完成，也不要在未完成时标记 done。")
    return "\n".join(lines)


def task_planning_prompt(
    task: Task, *, unread: list[TeamMessage] | None = None,
) -> str:
    deps = ", ".join(f"#{d}" for d in task.depends_on) or "无"
    lines = [
        f"请为共享任务 #{task.id} 制定执行计划：{task.title}",
        f"描述：{task.description or '（无）'}",
        f"依赖：{deps}",
    ]
    messages = _format_messages(unread or [])
    if messages:
        lines.append(f"\n{messages}")
    lines.append(
        "你可以读取文件、查看任务板，但不得写文件、执行命令、发送消息或修改任务。"
        "只输出计划正文。")
    return "\n".join(lines)


def message_turn_prompt(messages: list[TeamMessage]) -> str:
    """空闲成员被消息唤醒时的一轮输入。"""
    return (
        f"{_format_messages(messages)}\n\n"
        "请根据消息决定是否回复、领取/更新任务。若没有可执行任务，简短说明后保持空闲。")
