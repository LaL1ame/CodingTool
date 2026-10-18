"""团队测试共用 fixture：临时根目录、已初始化的小组、假 Agent/Manager。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from codepilot.protocols import Delta
from codepilot.teams.model import TeamSettings
from codepilot.teams.storage import TeamStorage
from codepilot.teams.tasks import TaskBoard


class FakeAgent:
    """按脚本产出 Delta 的假 Agent；记录每次调用时的消息列表与阶段。"""

    def __init__(self, responses=None, *, phase=None):
        self._responses = list(responses or [])
        self._phase = phase
        self.calls: list[dict] = []

    async def run(self, messages):
        self.calls.append({
            "len": len(messages),
            "last": messages[-1] if messages else None,
            "phase": self._phase.value if self._phase is not None else None,
        })
        response = self._responses.pop(0) if self._responses else [Delta(text="ok")]
        for delta in response:
            yield delta


class FakeManager:
    """记录状态与消息的 TeamManager 替身（不写磁盘）。"""

    def __init__(self) -> None:
        self.statuses: list[tuple] = []
        self.sent: list[SimpleNamespace] = []

    def set_member_status(self, name, status, *, task_id=None, note=""):
        self.statuses.append((name, status, task_id, note))

    def send(self, *, sender, recipient, type, body, task_id=None, **kw):
        msg = SimpleNamespace(
            id=f"m{len(self.sent)}", sender=sender, recipient=recipient,
            type=type, body=body, task_id=task_id,
            approved=kw.get("approved"), reason=kw.get("reason", ""))
        self.sent.append(msg)
        return msg

    def broadcast(self, *, sender, body):
        msg = self.send(
            sender=sender, recipient="*", type="broadcast", body=body)
        return [msg], []

    def status_values(self) -> list:
        from codepilot.teams.model import TeamMemberStatus

        return [TeamMemberStatus(s[1]) if not isinstance(
            s[1], TeamMemberStatus) else s[1] for s in self.statuses]


async def wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("等待条件超时")


@pytest.fixture
def settings(tmp_path: Path) -> TeamSettings:
    return TeamSettings(root=tmp_path / "teams")


@pytest.fixture
def storage(settings: TeamSettings) -> TeamStorage:
    return TeamStorage(settings.root, settings)


@pytest.fixture
def team(storage: TeamStorage) -> str:
    """初始化一个空小组 alpha（team.json + tasks.json）。"""
    name = "alpha"
    storage.ensure_team_layout(name)
    storage.atomic_write_json(
        storage.team_json(name),
        {
            "name": name,
            "lead": "lead",
            "members": [],
            "created_at": 1.0,
            "next_member_seq": 1,
        },
    )
    storage.atomic_write_json(
        storage.tasks_json(name), {"next_id": 1, "tasks": {}})
    return name


@pytest.fixture
def board(storage: TeamStorage, team: str) -> TaskBoard:
    return TaskBoard(storage, team, is_member={"alice", "bob"}.__contains__)
