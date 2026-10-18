"""成员运行时：长期存活的 Agent + 任务循环 + 审批等待 + idle/唤醒。

一个成员实例只创建一次 Agent，`self._msgs` 长期保留，因此 Lead 后续指派
新任务时能在同一上下文继续，而不是重新 spawn。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from codepilot.teams.model import (
    TeamMember,
    TeamMemberStatus,
    TeamMeta,
    TeamMessageType,
    TeamSettings,
)
from codepilot.teams.prompts import (
    message_turn_prompt,
    task_execution_prompt,
    task_planning_prompt,
)
from codepilot.teams.tools_member import MEMBER_TOOL_NAMES

if TYPE_CHECKING:
    from codepilot.agent import Agent
    from codepilot.subagents.model import AgentRole
    from codepilot.teams.mailbox import Mailbox
    from codepilot.teams.manager import TeamManager
    from codepilot.teams.tasks import TaskBoard

logger = logging.getLogger("codepilot.teams.runtime")


class MemberPhase:
    """工具过滤的阶段开关：plan 只读，execute 全量。"""

    PLAN = "plan"
    EXECUTE = "execute"

    def __init__(self) -> None:
        self.value = self.EXECUTE


def build_member_tool_filter(role: "AgentRole", phase: MemberPhase):
    """成员工具多层过滤：角色白名单 ∪ 协作工具 → blocked → 禁 agent。"""

    def _filter(tools):
        if phase.value == MemberPhase.PLAN:
            tools = [t for t in tools if not t.side_effect]
        elif role.tools:
            tools = [
                t for t in tools
                if t.name in role.tools
                or t.name in MEMBER_TOOL_NAMES
                or t.system_exempt
            ]
        if role.blocked:
            tools = [t for t in tools if t.name not in role.blocked]
        return [t for t in tools if t.name != "agent"]

    return _filter


@dataclass
class AgentOutcome:
    text: str
    error: str | None = None
    paused_reason: str | None = None


class MemberRuntime:
    def __init__(
        self,
        *,
        member: TeamMember,
        team: TeamMeta,
        agent: "Agent",
        mailbox: "Mailbox",
        board: "TaskBoard",
        manager: "TeamManager",
        settings: TeamSettings,
        phase: MemberPhase | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._member = member
        self._team = team
        self._agent = agent
        self._mailbox = mailbox
        self._board = board
        self._manager = manager
        self._settings = settings
        self._phase = phase or MemberPhase()
        self._clock = clock
        self._sleep = sleep
        self._msgs: list[dict] = []
        self._wake = asyncio.Event()
        self._stopped = False
        self._task: asyncio.Task | None = None
        self._status = member.status
        self._last_status_key: tuple | None = None

    @property
    def name(self) -> str:
        return self._member.name

    @property
    def status(self) -> TeamMemberStatus:
        return self._status

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def wake(self) -> None:
        self._wake.set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._loop(), name=f"team-member-{self._member.name}")

    def stop_now(self, *, reason: str = "terminated") -> None:
        """同步请求停止：设置标志并取消 task，不等待收尾（小组切换用）。"""
        if self._stopped:
            return
        self._stopped = True
        self._wake.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        logger.info("成员 %s 请求停止: %s", self._member.name, reason)

    async def stop(self, *, reason: str = "terminated") -> None:
        if self._stopped:
            return
        self._stopped = True
        self._wake.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - 终止不应外抛
                logger.exception("成员 %s 停止时异常", self._member.name)
        logger.info("成员 %s 已停止: %s", self._member.name, reason)

    # —— 状态与通知 ——
    def _set_status(
        self,
        status: TeamMemberStatus,
        *,
        task_id: int | None = None,
        note: str = "",
    ) -> None:
        key = (status, task_id, note)
        if key == self._last_status_key:
            return
        self._last_status_key = key
        self._status = status
        try:
            self._manager.set_member_status(
                self._member.name, status, task_id=task_id, note=note)
        except Exception:  # noqa: BLE001 - 状态上报失败不打断成员
            logger.exception("成员 %s 状态上报失败", self._member.name)

    def _fail(self, message: str, task_id: int | None = None) -> None:
        logger.error("成员 %s 失败: %s", self._member.name, message)
        self._set_status(
            TeamMemberStatus.FAILED, task_id=task_id, note=message[:500])
        self._stopped = True

    # —— 主循环 ——
    async def _loop(self) -> None:
        try:
            self._set_status(TeamMemberStatus.IDLE)
            self._wake.set()
            while not self._stopped:
                await self._wake.wait()
                self._wake.clear()
                if self._stopped:
                    break
                await self._cycle()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - 故障隔离
            logger.exception("成员 %s 循环异常", self._member.name)
            self._fail(f"成员循环异常: {e}")

    def _split_runnable(self, tasks):
        runnable = []
        blocked: list[str] = []
        for task in tasks:
            gaps = self._board.deps_satisfied(task)[1]
            if gaps:
                blocked.append(
                    f"任务 #{task.id} 依赖未完成: "
                    + ", ".join(f"#{d}" for d in gaps))
            else:
                runnable.append(task)
        return runnable, blocked

    async def _cycle(self) -> None:
        unread = self._mailbox.consume_unread(
            limit=self._settings.max_unread_per_wakeup)
        tasks = self._board.list(
            assignee=self._member.name,
            status="pending")
        runnable, blocked = self._split_runnable(tasks)

        # 有可执行任务时，未读消息并入任务提示（审批任务并入计划提示），
        # 不再单独跑消息回合 —— 否则需审批成员会先以 text 发一份计划，
        # 运行时随后又发一次正式 plan_request。
        if not runnable:
            if unread:
                outcome = await self._run_agent(message_turn_prompt(unread))
                if outcome.error:
                    self._fail(outcome.error)
                    return
                if outcome.paused_reason:
                    self._set_status(
                        TeamMemberStatus.BLOCKED,
                        note=f"消息回合暂停: {outcome.paused_reason}")
                    return
                # 消息回合可能领取/创建了新任务，重新读取一次。
                tasks = self._board.list(
                    assignee=self._member.name,
                    status="pending")
                runnable, blocked = self._split_runnable(tasks)
            if not runnable:
                if blocked:
                    self._set_status(
                        TeamMemberStatus.BLOCKED,
                        note="; ".join(blocked)[:500])
                else:
                    self._set_status(TeamMemberStatus.IDLE)
                return

        first = True
        executed = 0
        for task in runnable:
            if self._stopped:
                return
            approved_plan = ""
            exec_unread = unread if first else []
            if self._member.requires_approval:
                approved_plan = await self._approval_flow(
                    task, unread=exec_unread)
                exec_unread = []
                if approved_plan is None:
                    if self._stopped:
                        return
                    continue
            ok = await self._execute_task(
                task,
                unread=exec_unread,
                approved_plan=approved_plan,
            )
            first = False
            executed += 1
            if not ok:
                return
        if blocked and not executed:
            self._set_status(
                TeamMemberStatus.BLOCKED, note="; ".join(blocked)[:500])
            return
        self._set_status(TeamMemberStatus.IDLE)

    # —— 单回合 Agent 执行 ——
    async def _run_agent(self, prompt: str) -> AgentOutcome:
        self._msgs.append({"role": "user", "content": prompt})
        chunks: list[str] = []
        try:
            async for delta in self._agent.run(self._msgs):
                if delta.error:
                    return AgentOutcome("".join(chunks), error=delta.error)
                if delta.cancelled:
                    return AgentOutcome("".join(chunks), error="已取消")
                if delta.text:
                    chunks.append(delta.text)
                if delta.paused:
                    return AgentOutcome(
                        "".join(chunks).strip(),
                        paused_reason=delta.pause_reason or "paused")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - 转成失败结果
            logger.exception("成员 %s Agent 回合异常", self._member.name)
            return AgentOutcome("".join(chunks).strip(), error=str(e))
        return AgentOutcome("".join(chunks).strip())

    # —— 审批 ——
    async def _approval_flow(self, task, *, unread=None) -> str | None:
        """返回批准的计划正文；驳回/停止返回 None。

        `unread` 是本次任务边界消费到的消息，并入计划提示，避免再跑消息回合。
        """
        self._phase.value = MemberPhase.PLAN
        try:
            self._set_status(TeamMemberStatus.RUNNING, task_id=task.id)
            outcome = await self._run_agent(
                task_planning_prompt(task, unread=unread or []))
        finally:
            self._phase.value = MemberPhase.EXECUTE
        if outcome.error:
            self._fail(outcome.error, task.id)
            return None
        if outcome.paused_reason:
            self._set_status(
                TeamMemberStatus.BLOCKED, task_id=task.id,
                note=f"计划回合暂停: {outcome.paused_reason}")
            return None
        plan = outcome.text or "(成员未输出计划正文)"
        try:
            self._manager.send(
                sender=self._member.name,
                recipient=self._team.lead,
                type=TeamMessageType.PLAN_REQUEST,
                body=plan,
                task_id=task.id,
            )
        except Exception as e:  # noqa: BLE001
            self._fail(f"发送计划失败: {e}", task.id)
            return None
        self._set_status(
            TeamMemberStatus.BLOCKED, task_id=task.id, note="等待 Lead 审批")

        deadline = self._clock() + self._settings.approval_timeout_seconds
        timeout_notified = False
        while not self._stopped:
            decisions = self._mailbox.consume_matching(
                type=TeamMessageType.PLAN_DECISION, task_id=task.id)
            if decisions:
                decision = decisions[0]
                if decision.approved:
                    self._set_status(
                        TeamMemberStatus.RUNNING, task_id=task.id)
                    return plan
                try:
                    self._board.update(
                        task.id, status="pending", assignee=None,
                        allow_reopen=True)
                except Exception as e:  # noqa: BLE001
                    logger.warning("驳回后释放任务 %s 失败: %s", task.id, e)
                self._set_status(
                    TeamMemberStatus.IDLE, task_id=task.id,
                    note=decision.reason or "计划被驳回")
                return None

            if not timeout_notified and self._clock() >= deadline:
                timeout_notified = True
                self._set_status(
                    TeamMemberStatus.BLOCKED, task_id=task.id,
                    note="审批等待超时，仍未开始执行")
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self._settings.approval_poll_seconds)
                self._wake.clear()
            except asyncio.TimeoutError:
                pass
        return None

    # —— 任务执行 ——
    async def _execute_task(
        self,
        task,
        *,
        unread,
        approved_plan: str,
    ) -> bool:
        try:
            self._board.update(task.id, status="in_progress")
        except Exception as e:  # noqa: BLE001
            self._fail(f"任务 #{task.id} 无法开始: {e}", task.id)
            return False
        self._set_status(TeamMemberStatus.RUNNING, task_id=task.id)
        outcome = await self._run_agent(task_execution_prompt(
            task, unread=unread, approved_plan=approved_plan))
        if outcome.error:
            self._fail(outcome.error, task.id)
            return False
        if outcome.paused_reason:
            self._set_status(
                TeamMemberStatus.BLOCKED, task_id=task.id,
                note=f"任务执行暂停: {outcome.paused_reason}")
            return False
        result = outcome.text or "(成员未输出结论)"
        try:
            self._board.update(
                task.id, status="done", result=result)
        except Exception as e:  # noqa: BLE001
            self._fail(f"任务 #{task.id} 标记完成失败: {e}", task.id)
            return False
        self._set_status(
            TeamMemberStatus.IDLE, task_id=task.id,
            note=f"任务 #{task.id} 完成: {result[:200]}")
        return True
