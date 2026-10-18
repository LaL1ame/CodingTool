"""TeamManager：小组/成员生命周期、任务与消息门面、Lead 事件分发。

只依赖团队领域层与现有 Agent/Worktree 装配；不 import UI。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codepilot.agent import Agent
from codepilot.context.manager import ContextManager
from codepilot.memory.inject import MemoryInjector
from codepilot.memory.store import NoteStore
from codepilot.prompt import EnvContext
from codepilot.subagents.isolation import role_permission, role_provider
from codepilot.subagents.runner import SharedContext
from codepilot.teams.mailbox import Mailbox
from codepilot.teams.model import (
    MemberRef,
    Task,
    TeamError,
    TeamEvent,
    TeamMember,
    TeamMemberError,
    TeamMemberStatus,
    TeamMessage,
    TeamMessageType,
    TeamMeta,
    TeamSettings,
    TeamTaskError,
)
from codepilot.teams.naming import (
    MEMBER_NAME_RESERVED,
    member_worktree_name,
    validate_member_name,
    validate_team_name,
)
from codepilot.teams.prompts import lead_context_block, member_context_block
from codepilot.teams.registry import MemberRegistry
from codepilot.teams.runtime import (
    MemberPhase,
    MemberRuntime,
    build_member_tool_filter,
)
from codepilot.teams.settings import select_backend
from codepilot.teams.storage import TeamStorage
from codepilot.teams.tasks import TaskBoard
from codepilot.teams.tools_member import build_member_registry

if TYPE_CHECKING:
    from codepilot.permissions import PermissionManager
    from codepilot.providers import BaseProvider
    from codepilot.subagents.model import AgentRole
    from codepilot.tools.registry import ToolRegistry
    from codepilot.worktree.manager import WorktreeManager
    from codepilot.worktree.model import WorktreeHandle

logger = logging.getLogger("codepilot.teams.manager")

_ACTIVE_STATUSES = {
    TeamMemberStatus.SPAWNING,
    TeamMemberStatus.RUNNING,
    TeamMemberStatus.IDLE,
    TeamMemberStatus.BLOCKED,
}
_REVIVABLE_STATUSES = {
    TeamMemberStatus.OFFLINE,
    TeamMemberStatus.FAILED,
}


class TeamManager:
    def __init__(
        self,
        settings: TeamSettings,
        *,
        workspace: Path,
        base_registry: "ToolRegistry",
        roles: dict[str, "AgentRole"],
        provider: "BaseProvider",
        env: EnvContext | None = None,
        ctx_config=None,
        hooks=None,
        parent_permission: "PermissionManager | None" = None,
        worktree: "WorktreeManager | None" = None,
        agent_factory: Callable[..., object] | None = None,
        clock: Callable[[], float] = time.time,
        max_rounds: int = 18,
    ) -> None:
        self._settings = settings
        self._workspace = Path(workspace).resolve()
        self._base_registry = base_registry
        self._roles = roles
        self._worktree = worktree
        self._agent_factory = agent_factory or Agent
        self._clock = clock
        self._max_rounds = max_rounds
        self._storage = TeamStorage(settings.root, settings)
        if env is None:
            env = EnvContext(
                cwd=str(self._workspace),
                date=datetime.now().strftime("%Y-%m-%d"),
            )
        self._shared = SharedContext(
            provider=provider,
            registry=base_registry,
            workspace=self._workspace,
            env=env,
            shared_hooks=hooks,
            ctx_config=ctx_config,
            parent_permission=parent_permission,
            max_rounds=max_rounds,
            max_depth=0,
        )
        self._team_name: str | None = None
        self._team_meta: TeamMeta | None = None
        self._registry: MemberRegistry | None = None
        self._board: TaskBoard | None = None
        self._runtimes: dict[str, MemberRuntime] = {}
        self._handles: dict[str, "WorktreeHandle"] = {}
        self._pending_approvals: dict[int, str] = {}
        self._on_event: Callable[[TeamEvent], None] | None = None
        self._permission_lock = asyncio.Lock()
        self._last_status: dict[str, tuple] = {}
        self._map_lock = threading.RLock()

    # —— 基础访问 ——
    @property
    def storage(self) -> TeamStorage:
        return self._storage

    @property
    def settings(self) -> TeamSettings:
        return self._settings

    @property
    def team_name(self) -> str | None:
        return self._team_name

    def current_team(self) -> TeamMeta | None:
        return self._team_meta

    def _require_team(self) -> tuple[MemberRegistry, TaskBoard, TeamMeta]:
        if (self._registry is None or self._board is None
                or self._team_meta is None):
            raise TeamMemberError("当前没有小组；先用 team_create 或 /team use")
        return self._registry, self._board, self._team_meta

    def registry(self) -> MemberRegistry:
        return self._require_team()[0]

    def tasks(self) -> TaskBoard:
        return self._require_team()[1]

    # —— 小组生命周期 ——
    def list_teams(self) -> list[str]:
        root = self._storage.root
        if not root.exists():
            return []
        names: list[str] = []
        for p in sorted(root.iterdir()):
            if not p.is_dir():
                continue
            try:
                validate_team_name(p.name)
            except Exception:
                continue
            if self._storage.team_json(p.name).exists():
                names.append(p.name)
        return names

    def create_team(self, name: str) -> TeamMeta:
        validate_team_name(name)
        if self._storage.team_dir(name).exists():
            raise TeamMemberError(f"小组目录已存在: {name!r}")
        if self._team_name is not None:
            self._deactivate_current_sync()
        self._storage.ensure_team_layout(name)
        meta = TeamMeta(
            name=name, lead=MEMBER_NAME_RESERVED, members=(),
            created_at=self._clock(), next_member_seq=1)
        self._storage.atomic_write_json(
            self._storage.team_json(name), {
                "name": meta.name, "lead": meta.lead,
                "members": [], "created_at": meta.created_at,
                "next_member_seq": meta.next_member_seq,
            })
        self._storage.atomic_write_json(
            self._storage.tasks_json(name), {"next_id": 1, "tasks": {}})
        self._storage.atomic_write_json(
            self._storage.registry_json(name), {
                "team": name, "lead": MEMBER_NAME_RESERVED, "members": {},
            })
        lead_mail = self._storage.mailbox_path(name, MEMBER_NAME_RESERVED)
        lead_mail.parent.mkdir(parents=True, exist_ok=True)
        lead_mail.touch(exist_ok=True)
        self._storage.write_current(name)
        self._activate(name, mark_offline=False)
        logger.info("团队创建 %s", name)
        return meta

    def use_team(self, name: str) -> TeamMeta:
        validate_team_name(name)
        if self._team_name == name:
            return self._require_team()[2]
        if not self._storage.team_json(name).exists():
            raise TeamMemberError(f"小组不存在: {name!r}")
        if self._team_name is not None:
            self._deactivate_current_sync()
        self._activate(name, mark_offline=True)
        self._storage.write_current(name)
        logger.info("团队切换 %s", name)
        return self._require_team()[2]

    def load_current(self) -> TeamMeta | None:
        name = self._storage.read_current()
        if not name or not self._storage.team_json(name).exists():
            return None
        try:
            self._activate(name, mark_offline=True)
        except Exception as e:  # noqa: BLE001 - 启动不应因坏组崩溃
            logger.warning("加载当前小组失败 %s: %s", name, e)
            self._team_name = None
            self._team_meta = None
            self._registry = None
            self._board = None
            return None
        logger.info("团队加载 %s（成员置 offline）", name)
        return self._team_meta

    def _activate(self, name: str, *, mark_offline: bool) -> None:
        if not self._storage.team_json(name).exists():
            raise TeamMemberError(f"小组不存在: {name!r}")
        registry = MemberRegistry(
            self._storage, name, workspace=self._workspace, clock=self._clock)
        board = TaskBoard(
            self._storage, name, clock=self._clock,
            is_member=registry.is_assignable)
        self._team_name = name
        self._registry = registry
        self._board = board
        self._last_status.clear()
        if mark_offline:
            registry.mark_all_offline()
        self._team_meta = registry.team_meta()

    def _deactivate_current_sync(self) -> None:
        if self._team_name is None:
            return
        registry = self._registry
        with self._map_lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            runtime.stop_now(reason="team switched")
        for name in list(self._handles):
            self._release_handle(name)
        if registry is not None:
            try:
                for ref in registry.list():
                    if ref.status not in (
                            TeamMemberStatus.TERMINATED,
                            TeamMemberStatus.OFFLINE):
                        registry.update(
                            ref.name, status=TeamMemberStatus.OFFLINE,
                            note="小组切换")
            except Exception:  # noqa: BLE001
                logger.exception("切换小组时置 offline 失败")
        self._team_name = None
        self._team_meta = None
        self._registry = None
        self._board = None
        self._pending_approvals.clear()

    async def shutdown(self) -> None:
        with self._map_lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            await runtime.stop(reason="shutdown")
        for name in list(self._handles):
            self._release_handle(name)
        registry = self._registry
        if registry is not None:
            try:
                for ref in registry.list():
                    if ref.status not in (
                            TeamMemberStatus.TERMINATED,
                            TeamMemberStatus.OFFLINE):
                        registry.update(
                            ref.name, status=TeamMemberStatus.OFFLINE,
                            note="进程退出")
            except Exception:  # noqa: BLE001
                logger.exception("shutdown 置 offline 失败")
        self._pending_approvals.clear()

    # —— 成员生命周期 ——
    async def spawn_member(
        self,
        *,
        name: str,
        role: str,
        backend: str = "auto",
        requires_approval: bool = False,
    ) -> TeamMember:
        registry, board, team = self._require_team()
        validate_member_name(name)
        role_obj = self._roles.get(role)
        if role_obj is None:
            raise TeamMemberError(f"未知角色: {role!r}")
        chosen = select_backend(backend, self._settings)
        if self._worktree is None:
            raise TeamMemberError("Worktree 系统未启用，无法派生团队成员")
        existing: MemberRef | None = None
        try:
            existing = registry.resolve(name)
        except TeamMemberError:
            existing = None
        if existing is not None:
            if existing.status is TeamMemberStatus.TERMINATED:
                raise TeamMemberError(f"成员已终止，不能重新派生: {name!r}")
            if existing.status not in _REVIVABLE_STATUSES:
                raise TeamMemberError(
                    f"成员 {name!r} 已存在且状态为 {existing.status.value}，"
                    "不能重复派生")
        with self._map_lock:
            if name in self._runtimes:
                raise TeamMemberError(f"成员 {name!r} 正在运行，不能重复派生")

        seq = team.next_member_seq
        wt_name = member_worktree_name(team.name, name)
        handle = await asyncio.to_thread(
            self._worktree.enter, wt_name, role=role_obj.name, task_id=seq)
        created = existing is None
        try:
            now = self._clock()
            if existing is None:
                member = TeamMember(
                    name=name, role=role_obj.name,
                    worktree_path=handle.path, branch=handle.branch,
                    backend=chosen, requires_approval=requires_approval,
                    status=TeamMemberStatus.SPAWNING,
                    created_at=now, updated_at=now)
                member = registry.add_member(member)
            else:
                member = registry.update(
                    name, role=role_obj.name,
                    worktree_path=handle.path, branch=handle.branch,
                    backend=chosen, requires_approval=requires_approval,
                    status=TeamMemberStatus.SPAWNING, note="")
            self._team_meta = registry.team_meta()
            runtime = self._build_runtime(member, role_obj, handle)
            with self._map_lock:
                self._runtimes[name] = runtime
                self._handles[name] = handle
            await runtime.start()
            member = registry.update(
                name, status=TeamMemberStatus.IDLE, note="")
            self._emit(TeamEvent(
                kind="spawned", member=name, task_id=None,
                summary=f"成员 {name} 已派生（{chosen.value}）"))
            logger.info(
                "成员派生 %s role=%s backend=%s worktree=%s",
                name, role_obj.name, chosen.value, handle.path)
            return member
        except Exception as e:  # noqa: BLE001 - 回滚半成品
            logger.exception("成员派生失败 %s", name)
            with self._map_lock:
                self._runtimes.pop(name, None)
                self._handles.pop(name, None)
            if created:
                try:
                    registry.remove_member(name)
                except Exception:  # noqa: BLE001
                    logger.exception("回滚成员元数据失败 %s", name)
            else:
                try:
                    registry.update(
                        name, status=TeamMemberStatus.FAILED,
                        note=f"重新派生失败: {e}")
                except Exception:  # noqa: BLE001
                    pass
            try:
                if created and not handle.reused:
                    await asyncio.to_thread(self._worktree.leave, handle)
                else:
                    await asyncio.to_thread(self._worktree.release, handle)
            except Exception:  # noqa: BLE001
                logger.exception("回滚 worktree 失败 %s", handle.name)
            if isinstance(e, TeamError):
                raise
            raise TeamMemberError(f"派生成员失败: {e}") from e

    async def terminate_member(
        self, name: str, *, reason: str = "Lead 终止",
    ) -> TeamMember:
        registry, _, _ = self._require_team()
        ref = registry.resolve(name)
        if ref.status is TeamMemberStatus.TERMINATED:
            raise TeamMemberError(f"成员已终止: {name!r}")
        with self._map_lock:
            runtime = self._runtimes.pop(name, None)
        if runtime is not None:
            await runtime.stop(reason=reason)
        self._release_handle(name)
        member = registry.update(
            name, status=TeamMemberStatus.TERMINATED, note=reason)
        self._emit(TeamEvent(
            kind="terminated", member=name, task_id=None,
            summary=f"成员 {name} 已终止：{reason}"))
        logger.info("成员终止 %s: %s", name, reason)
        return member

    def _release_handle(self, name: str) -> None:
        with self._map_lock:
            handle = self._handles.pop(name, None)
        if handle is None or self._worktree is None:
            return
        try:
            self._worktree.release(handle)
        except Exception:  # noqa: BLE001
            logger.exception("释放 worktree 占用失败 %s", name)

    def _build_runtime(
        self, member: TeamMember, role: "AgentRole", handle: "WorktreeHandle",
    ) -> MemberRuntime:
        registry, board, team = self._require_team()
        phase = MemberPhase()
        tools = build_member_registry(
            self._base_registry, handle.path, board, self, member.name)
        provider = role_provider(self._shared, role.model)
        permission = role_permission(
            self._shared, role, workspace=handle.path)
        parent_cb = (
            self._shared.parent_permission.human_callback
            if self._shared.parent_permission is not None else None)
        if parent_cb is not None:
            async def serialized(tool: str, subject: str):
                async with self._permission_lock:
                    return await parent_cb(tool, subject)
            permission.human_callback = serialized
        context_manager = ContextManager(
            provider=provider, config=self._shared.ctx_config,
            offload_dir=handle.path / ".codepilot" / "context")
        memory = MemoryInjector(handle.path, store=NoteStore(handle.path))

        def injector() -> list[str]:
            blocks = [member_context_block(
                team, member, registry.list())]
            if role.body:
                blocks.append(role.body)
            blocks.extend(memory.build())
            return blocks

        agent = self._agent_factory(
            provider=provider,
            registry=tools,
            workspace=handle.path,
            env=EnvContext(cwd=str(handle.path), date=self._shared.env.date),
            permission=permission,
            context_manager=context_manager,
            skill_injector=injector,
            tool_filter=build_member_tool_filter(role, phase),
            max_rounds=role.max_rounds or self._shared.max_rounds,
            hooks=self._shared.shared_hooks,
            stall_enabled=self._shared.stall_enabled,
        )
        return MemberRuntime(
            member=member,
            team=team,
            agent=agent,
            mailbox=self._mailbox(member.name),
            board=board,
            manager=self,
            settings=self._settings,
            phase=phase,
            clock=self._clock,
        )

    # —— 状态与事件 ——
    def set_on_event(self, cb: Callable[[TeamEvent], None] | None) -> None:
        self._on_event = cb

    def _emit(self, event: TeamEvent) -> None:
        cb = self._on_event
        if cb is None:
            return
        try:
            cb(event)
        except Exception:  # noqa: BLE001 - 通知失败不影响团队
            logger.exception("TeamEvent 回调失败: %s", event)

    def set_member_status(
        self,
        name: str,
        status: TeamMemberStatus,
        *,
        task_id: int | None = None,
        note: str = "",
    ) -> TeamMember | None:
        if self._team_name is None or self._registry is None:
            return None
        key = (status, task_id, note)
        if self._last_status.get(name) == key:
            return None
        self._last_status[name] = key
        try:
            member = self._registry.update(
                name, status=status, note=note)
        except Exception as e:  # noqa: BLE001
            logger.warning("更新成员状态失败 %s: %s", name, e)
            return None
        try:
            self.send(
                sender=name, recipient=MEMBER_NAME_RESERVED,
                type=TeamMessageType.STATUS,
                body=note, task_id=task_id, status=status.value)
        except Exception:  # noqa: BLE001 - 状态通知失败不影响成员
            logger.exception("发送成员状态失败 %s", name)
        return member

    # —— 消息 ——
    def _mailbox(self, owner: str) -> Mailbox:
        registry, _, _ = self._require_team()
        # 邮箱路径只依赖当前组；owner 由调用方保证合法。
        return Mailbox(
            self._storage.mailbox_path(registry.team, owner),
            settings=self._settings, clock=self._clock)

    def send(
        self,
        *,
        sender: str,
        recipient: str,
        type: str | TeamMessageType,
        body: str = "",
        **payload: Any,
    ) -> TeamMessage:
        registry, _, _ = self._require_team()
        if sender != MEMBER_NAME_RESERVED:
            registry.resolve(sender)  # 发件人存在即可（含 failed 的最终通知）
        target = registry.resolve_active(recipient)
        mailbox = Mailbox(
            target.mailbox_path, settings=self._settings, clock=self._clock)
        message = mailbox.make(
            sender=sender, recipient=recipient, type=type, body=body,
            task_id=payload.get("task_id"),
            approved=payload.get("approved"),
            reason=payload.get("reason", ""),
            status=payload.get("status", ""),
            summary=payload.get("summary"),
        )
        mailbox.append(message)
        msg_type = message.type
        if msg_type is TeamMessageType.PLAN_REQUEST and message.task_id is not None:
            self._pending_approvals[message.task_id] = sender
        if recipient == MEMBER_NAME_RESERVED:
            self._emit(TeamEvent(
                kind=("status" if msg_type is TeamMessageType.STATUS
                      else "message"),
                member=sender, task_id=message.task_id,
                summary=message.summary,
                mailbox_path=target.mailbox_path))
        else:
            with self._map_lock:
                runtime = self._runtimes.get(recipient)
            if runtime is not None:
                runtime.wake()
        logger.info(
            "团队消息 %s -> %s type=%s task=%s summary=%s",
            sender, recipient, msg_type.value, message.task_id,
            message.summary[:60])
        return message

    def broadcast(
        self, *, sender: str, body: str,
    ) -> tuple[list[TeamMessage], list[tuple[str, str]]]:
        registry, _, _ = self._require_team()
        if sender != MEMBER_NAME_RESERVED:
            registry.resolve(sender)
        delivered: list[TeamMessage] = []
        failures: list[tuple[str, str]] = []
        targets: list[MemberRef] = [
            r for r in registry.list() if r.status in _ACTIVE_STATUSES]
        if sender != MEMBER_NAME_RESERVED:
            targets.append(registry.resolve(MEMBER_NAME_RESERVED))
        for ref in targets:
            if ref.name == sender:
                continue
            try:
                msg = self.send(
                    sender=sender, recipient=ref.name,
                    type=TeamMessageType.BROADCAST, body=body)
                delivered.append(msg)
            except Exception as e:  # noqa: BLE001 - 逐个报告
                failures.append((ref.name, str(e)))
        return delivered, failures

    def decide(
        self, *, task_id: int, approved: bool, reason: str = "",
    ) -> TeamMessage:
        self._require_team()
        member = self._pending_approvals.get(task_id)
        if not member:
            raise TeamTaskError(
                f"任务 #{task_id} 没有等待审批的 plan_request")
        message = self.send(
            sender=MEMBER_NAME_RESERVED, recipient=member,
            type=TeamMessageType.PLAN_DECISION,
            body=reason, task_id=task_id,
            approved=approved, reason=reason)
        self._pending_approvals.pop(task_id, None)
        return message

    def lead_inbox(self, limit: int = 20) -> list[TeamMessage]:
        self._require_team()
        return self._mailbox(MEMBER_NAME_RESERVED).consume_unread(limit=limit)

    # —— 任务门面 ——
    def create_task(
        self, *, title: str, description: str = "",
        depends_on: list[int] | tuple[int, ...] = (),
        assignee: str | None = None,
    ) -> Task:
        _, board, _ = self._require_team()
        task = board.create(
            title=title, description=description,
            depends_on=depends_on, assignee=assignee)
        if assignee:
            self._wake_member(assignee)
        return task

    def update_task(self, task_id: int, **fields: Any) -> Task:
        _, board, _ = self._require_team()
        task = board.update(task_id, allow_reopen=True, **fields)
        if fields.get("assignee"):
            self._wake_member(fields["assignee"])
        return task

    def list_tasks(self, **filters: Any) -> list[Task]:
        _, board, _ = self._require_team()
        return board.list(**filters)

    def task_counts(self) -> dict[str, int]:
        _, board, _ = self._require_team()
        return dict(Counter(t.status.value for t in board.list()))

    def _wake_member(self, name: str) -> None:
        with self._map_lock:
            runtime = self._runtimes.get(name)
        if runtime is not None:
            runtime.wake()

    # —— 展示 ——
    def lead_context(self) -> str:
        if self._team_name is None or self._registry is None:
            return lead_context_block(None, [], {})
        try:
            members = self._registry.list()
            counts = self.task_counts()
        except Exception:  # noqa: BLE001
            members, counts = [], {}
        return lead_context_block(self._team_meta, members, counts)

    def team_summary(self) -> str:
        if self._team_name is None or self._registry is None:
            return "当前没有小组。用 /team create <name> 创建。"
        lines = [f"小组 {self._team_name}（当前）"]
        try:
            for ref in self._registry.list():
                approval = "需审批" if ref.requires_approval else "免审批"
                lines.append(
                    f"- {ref.name} | {ref.role} | {ref.status.value} | "
                    f"{approval} | {ref.worktree_path} | {ref.branch}")
            counts = self.task_counts()
            lines.append(
                "任务: " + " | ".join(
                    f"{k}={counts.get(k, 0)}"
                    for k in ("pending", "in_progress", "done", "cancelled")))
        except Exception as e:  # noqa: BLE001
            lines.append(f"（读取小组状态失败: {e}）")
        return "\n".join(lines)
