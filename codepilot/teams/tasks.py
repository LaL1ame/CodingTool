"""共享任务板：CRUD、依赖门控、原子领取与状态机。

所有写操作在小组级 team.lock 内完成；依赖判断与状态迁移是纯函数，便于单测。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from codepilot.teams.locks import FileLock
from codepilot.teams.model import Task, TaskStatus, TeamTaskError
from codepilot.teams.naming import MEMBER_NAME_RESERVED, validate_member_name
from codepilot.teams.storage import TeamStorage

logger = logging.getLogger("codepilot.teams.tasks")

_ALLOWED_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.DONE, TaskStatus.CANCELLED},
    TaskStatus.DONE: {TaskStatus.PENDING},
    TaskStatus.CANCELLED: {TaskStatus.PENDING},
}


def dependency_gaps(task: Task, tasks: dict[int, Task]) -> tuple[int, ...]:
    """返回尚未 done 的依赖 ID（含不存在的依赖）。"""
    gaps: list[int] = []
    for dep in task.depends_on:
        other = tasks.get(dep)
        if other is None or other.status is not TaskStatus.DONE:
            gaps.append(dep)
    return tuple(gaps)


def deps_satisfied(task: Task, tasks: dict[int, Task]) -> tuple[bool, tuple[int, ...]]:
    gaps = dependency_gaps(task, tasks)
    return (not gaps, gaps)


def _coerce_status(value: TaskStatus | str) -> TaskStatus:
    try:
        return value if isinstance(value, TaskStatus) else TaskStatus(str(value))
    except ValueError as e:
        raise TeamTaskError(f"未知任务状态: {value!r}") from e


def _task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status.value,
        "assignee": task.assignee,
        "depends_on": list(task.depends_on),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "result": task.result,
    }


def _task_from_dict(raw: object) -> Task | None:
    if not isinstance(raw, dict):
        return None
    try:
        task_id = raw["id"]
        title = raw["title"]
        if isinstance(task_id, bool) or not isinstance(task_id, int):
            return None
        if not isinstance(title, str) or not title:
            return None
        status = TaskStatus(str(raw.get("status", TaskStatus.PENDING.value)))
        assignee = raw.get("assignee")
        if assignee is not None and not isinstance(assignee, str):
            return None
        deps_raw = raw.get("depends_on", [])
        if not isinstance(deps_raw, (list, tuple)):
            return None
        deps: list[int] = []
        for dep in deps_raw:
            if isinstance(dep, bool) or not isinstance(dep, int):
                return None
            deps.append(dep)
        created = float(raw.get("created_at", 0.0))
        updated = float(raw.get("updated_at", created))
        result = str(raw.get("result", ""))
        return Task(
            id=task_id,
            title=title,
            description=str(raw.get("description", "")),
            status=status,
            assignee=assignee,
            depends_on=tuple(dict.fromkeys(deps)),
            created_at=created,
            updated_at=updated,
            result=result,
        )
    except (KeyError, TypeError, ValueError):
        return None


class TaskBoard:
    def __init__(
        self,
        storage: TeamStorage,
        team: str,
        *,
        clock: Callable[[], float] = time.time,
        is_member: Callable[[str], bool] | None = None,
    ) -> None:
        self._storage = storage
        self._team = team
        self._clock = clock
        self._is_member = is_member

    def _lock(self) -> FileLock:
        settings = self._storage.settings
        return FileLock(
            self._storage.team_lock_path(self._team),
            timeout=settings.lock_timeout_seconds,
            stale_after=settings.lock_stale_seconds,
            retry=settings.lock_retry_seconds,
            clock=self._clock,
        )

    # —— 内部读写 ——
    def _load_unlocked(self) -> tuple[int, dict[int, Task]]:
        path = self._storage.tasks_json(self._team)
        raw = self._storage.read_json_safe(
            path, {"next_id": 1, "tasks": {}})
        if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), dict):
            self._storage.backup_corrupt(path)
            return 1, {}
        tasks: dict[int, Task] = {}
        for key, value in raw["tasks"].items():
            task = _task_from_dict(value)
            if task is None:
                logger.warning("跳过损坏任务记录: %s", key)
                continue
            tasks[task.id] = task
        next_id = raw.get("next_id", 1)
        if isinstance(next_id, bool) or not isinstance(next_id, int):
            next_id = 1
        next_id = max(next_id, max(tasks, default=0) + 1)
        return next_id, tasks

    def _save_unlocked(self, next_id: int, tasks: dict[int, Task]) -> None:
        self._storage.atomic_write_json(
            self._storage.tasks_json(self._team),
            {
                "next_id": next_id,
                "tasks": {str(t.id): _task_to_dict(t) for t in tasks.values()},
            },
        )

    def _validate_deps(
        self, deps: Any, *, task_id: int | None, tasks: dict[int, Task],
    ) -> tuple[int, ...]:
        if not isinstance(deps, (list, tuple)):
            raise TeamTaskError("depends_on 必须是任务 ID 列表")
        out: list[int] = []
        for dep in deps:
            if isinstance(dep, bool) or not isinstance(dep, int):
                raise TeamTaskError(f"依赖必须是整数任务 ID: {dep!r}")
            if task_id is not None and dep == task_id:
                raise TeamTaskError(f"任务 #{task_id} 不能依赖自身")
            if dep not in tasks:
                raise TeamTaskError(f"依赖任务不存在: #{dep}")
            if dep not in out:
                out.append(dep)
        return tuple(out)

    def _validate_assignee(self, assignee: str | None) -> None:
        if assignee is None:
            return
        if not isinstance(assignee, str) or not assignee:
            raise TeamTaskError(f"assignee 必须是非空成员名: {assignee!r}")
        if assignee == MEMBER_NAME_RESERVED:
            raise TeamTaskError("lead 不能作为任务执行者")
        try:
            validate_member_name(assignee)
        except Exception as e:
            raise TeamTaskError(str(e)) from e
        if self._is_member is not None and not self._is_member(assignee):
            raise TeamTaskError(f"成员不存在或不可指派: {assignee!r}")

    @staticmethod
    def _check_transition(
        old: TaskStatus, new: TaskStatus, *, allow_reopen: bool,
    ) -> None:
        if old is new:
            return
        if new in _ALLOWED_TRANSITIONS[old]:
            if old in (TaskStatus.DONE, TaskStatus.CANCELLED) and not allow_reopen:
                raise TeamTaskError(
                    f"任务 {old.value} → {new.value} 仅允许 Lead 重开")
            return
        raise TeamTaskError(f"非法任务状态迁移: {old.value} → {new.value}")

    # —— 公开 API ——
    def create(
        self,
        *,
        title: str,
        description: str = "",
        depends_on: list[int] | tuple[int, ...] = (),
        assignee: str | None = None,
    ) -> Task:
        if not isinstance(title, str) or not title.strip():
            raise TeamTaskError("任务标题不能为空")
        self._validate_assignee(assignee)
        with self._lock():
            next_id, tasks = self._load_unlocked()
            deps = self._validate_deps(depends_on, task_id=None, tasks=tasks)
            now = self._clock()
            task = Task(
                id=next_id,
                title=title.strip(),
                description=str(description or ""),
                status=TaskStatus.PENDING,
                assignee=assignee,
                depends_on=deps,
                created_at=now,
                updated_at=now,
            )
            tasks[task.id] = task
            self._save_unlocked(next_id + 1, tasks)
            return task

    def list(
        self,
        *,
        status: TaskStatus | str | None = None,
        assignee: str | None = None,
    ) -> list[Task]:
        with self._lock():
            _, tasks = self._load_unlocked()
        wanted = _coerce_status(status) if status is not None else None
        out = [
            t for t in tasks.values()
            if (wanted is None or t.status is wanted)
            and (assignee is None or t.assignee == assignee)
        ]
        return sorted(out, key=lambda t: t.id)

    def get(self, task_id: int) -> Task:
        with self._lock():
            _, tasks = self._load_unlocked()
        task = tasks.get(task_id)
        if task is None:
            raise TeamTaskError(f"任务不存在: #{task_id}")
        return task

    def update(
        self,
        task_id: int,
        *,
        allow_reopen: bool = False,
        **fields: Any,
    ) -> Task:
        allowed = {
            "title", "description", "depends_on", "assignee", "status", "result",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise TeamTaskError(f"不支持的任务字段: {', '.join(sorted(unknown))}")
        # assignee 校验会经 is_member 回调读取 MemberRegistry，而注册表使用同一把
        # 非重入 team.lock；必须在取锁前完成，否则锁内再次 acquire 会超时。
        if "assignee" in fields:
            self._validate_assignee(fields["assignee"])
        with self._lock():
            next_id, tasks = self._load_unlocked()
            task = tasks.get(task_id)
            if task is None:
                raise TeamTaskError(f"任务不存在: #{task_id}")
            changes: dict[str, Any] = {}
            if "title" in fields:
                title = fields["title"]
                if not isinstance(title, str) or not title.strip():
                    raise TeamTaskError("任务标题不能为空")
                changes["title"] = title.strip()
            if "description" in fields:
                changes["description"] = str(fields["description"] or "")
            if "depends_on" in fields:
                changes["depends_on"] = self._validate_deps(
                    fields["depends_on"], task_id=task_id, tasks=tasks)
            if "assignee" in fields:
                changes["assignee"] = fields["assignee"]
            if "status" in fields:
                status = _coerce_status(fields["status"])
                self._check_transition(
                    task.status, status, allow_reopen=allow_reopen)
                changes["status"] = status
            if "result" in fields:
                changes["result"] = str(fields["result"] or "")
            changes["updated_at"] = self._clock()
            updated = replace(task, **changes)
            tasks[task_id] = updated
            self._save_unlocked(next_id, tasks)
            return updated

    def delete(self, task_id: int) -> None:
        with self._lock():
            next_id, tasks = self._load_unlocked()
            task = tasks.get(task_id)
            if task is None:
                raise TeamTaskError(f"任务不存在: #{task_id}")
            dependents = [
                t.id for t in tasks.values()
                if task_id in t.depends_on and t.id != task_id
            ]
            if dependents:
                raise TeamTaskError(
                    f"任务 #{task_id} 仍被依赖: "
                    + ", ".join(f"#{i}" for i in dependents))
            if task.status is TaskStatus.IN_PROGRESS:
                raise TeamTaskError(f"任务 #{task_id} 执行中，不能删除")
            del tasks[task_id]
            self._save_unlocked(next_id, tasks)

    def claim(self, task_id: int, member: str) -> Task:
        self._validate_assignee(member)
        with self._lock():
            next_id, tasks = self._load_unlocked()
            task = tasks.get(task_id)
            if task is None:
                raise TeamTaskError(f"任务不存在: #{task_id}")
            if task.status is not TaskStatus.PENDING:
                raise TeamTaskError(
                    f"任务 #{task_id} 状态为 {task.status.value}，不可领取")
            if task.assignee is not None:
                raise TeamTaskError(
                    f"任务 #{task_id} 已被 {task.assignee} 指派/领取")
            gaps = dependency_gaps(task, tasks)
            if gaps:
                raise TeamTaskError(
                    f"任务 #{task_id} 依赖未完成: "
                    + ", ".join(f"#{i}" for i in gaps))
            updated = replace(
                task, assignee=member, updated_at=self._clock())
            tasks[task_id] = updated
            self._save_unlocked(next_id, tasks)
            return updated

    def deps_satisfied(self, task: Task) -> tuple[bool, tuple[int, ...]]:
        with self._lock():
            _, tasks = self._load_unlocked()
        return deps_satisfied(task, tasks)
