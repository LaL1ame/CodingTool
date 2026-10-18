"""成员协作工具与成员 registry 构造。"""

from pathlib import Path

import pytest

from codepilot.protocols import ToolResult
from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry
from codepilot.teams.tasks import TaskBoard
from codepilot.teams.tools_member import (
    MEMBER_TOOL_NAMES,
    build_member_registry,
    build_member_tools,
)
from tests.teams.conftest import FakeManager


class _FakeTool(Tool):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return self._name

    @property
    def parameters(self):
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return ToolResult(call_id="", name=self._name, output="ok")


@pytest.fixture
def manager() -> FakeManager:
    return FakeManager()


@pytest.fixture
def tools(board: TaskBoard, manager: FakeManager):
    return {t.name: t for t in build_member_tools(board, manager, "alice")}


async def test_tool_names(tools):
    assert set(tools) == MEMBER_TOOL_NAMES


async def test_task_create_list_get(tools):
    created = await tools["task_create"].execute(title="A", description="d")
    assert created.error is None and "#1" in created.output
    listed = await tools["task_list"].execute()
    assert "A" in listed.output
    got = await tools["task_get"].execute(task_id=1)
    assert "A" in got.output and "pending" in got.output
    missing = await tools["task_get"].execute(task_id=99)
    assert missing.error


async def test_task_update_done(tools, board: TaskBoard):
    board.create(title="A")
    started = await tools["task_update"].execute(
        task_id=1, status="in_progress")
    assert started.error is None
    done = await tools["task_update"].execute(
        task_id=1, status="done", result="finished")
    assert done.error is None
    assert board.get(1).result == "finished"


async def test_task_update_cannot_change_assignee(tools):
    result = await tools["task_update"].execute(
        task_id=1, assignee="alice")
    assert result.error


async def test_task_claim(tools, board: TaskBoard):
    board.create(title="A")
    result = await tools["task_claim"].execute(task_id=1)
    assert result.error is None
    assert board.get(1).assignee == "alice"


async def test_task_delete_and_dependency_error(tools, board: TaskBoard):
    board.create(title="A")
    board.create(title="B", depends_on=[1])
    blocked = await tools["task_delete"].execute(task_id=1)
    assert blocked.error
    deleted = await tools["task_delete"].execute(task_id=2)
    assert deleted.error is None


async def test_send_and_broadcast(tools, manager: FakeManager):
    sent = await tools["send_message"].execute(to="lead", body="hi")
    assert sent.error is None
    assert manager.sent[-1].recipient == "lead"
    broadcast = await tools["broadcast"].execute(body="all")
    assert broadcast.error is None
    assert "1" in broadcast.output


async def test_build_member_registry(
    board: TaskBoard, manager: FakeManager, tmp_path: Path,
):
    base = ToolRegistry()
    base.register(_FakeTool("read"))  # 会被重新绑定覆盖
    for name in ("agent", "load_skill", "mcp_tool"):
        base.register(_FakeTool(name))
    base.register(_FakeTool("write"))

    workspace = tmp_path / "wt"
    registry = build_member_registry(base, workspace, board, manager, "alice")
    assert registry.get("read").workspace == workspace
    assert registry.get("agent") is None
    assert registry.get("load_skill") is None
    assert registry.get("mcp_tool") is base.get("mcp_tool")
    for name in MEMBER_TOOL_NAMES:
        assert registry.get(name) is not None
