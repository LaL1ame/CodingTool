"""三套工具面隔离：base / lead / member，以及 Fork 工具集过滤。"""

from pathlib import Path
from types import SimpleNamespace

from codepilot.protocols import ToolResult
from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry
from codepilot.teams import (
    ALL_TEAM_TOOL_NAMES,
    LEAD_TOOL_NAMES,
    MEMBER_TOOL_NAMES,
    filter_team_tools,
)
from codepilot.teams.manager import TeamManager
from codepilot.teams.model import TeamSettings
from codepilot.teams.tools_lead import build_lead_tools
from codepilot.teams.tools_member import build_member_registry
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


def _base() -> ToolRegistry:
    registry = ToolRegistry()
    for name in ("read", "write", "edit", "glob", "grep", "run",
                 "agent", "load_skill", "mcp_tool"):
        registry.register(_FakeTool(name))
    return registry


def _manager(settings: TeamSettings, tmp_path: Path) -> TeamManager:
    return TeamManager(
        settings,
        workspace=tmp_path / "main",
        base_registry=ToolRegistry(),
        roles={},
        provider=SimpleNamespace(config=SimpleNamespace(model="m")),
    )


def test_tool_sets_are_disjoint():
    assert LEAD_TOOL_NAMES.isdisjoint(MEMBER_TOOL_NAMES)
    assert ALL_TEAM_TOOL_NAMES == LEAD_TOOL_NAMES | MEMBER_TOOL_NAMES


def test_base_has_no_team_tools():
    base = _base()
    names = {t.name for t in base.list_all()}
    assert names.isdisjoint(ALL_TEAM_TOOL_NAMES)


def test_lead_registry_only_adds_lead_tools(
    settings: TeamSettings, tmp_path: Path,
):
    base = _base()
    manager = _manager(settings, tmp_path)
    lead = base.clone()
    for tool in build_lead_tools(manager):
        lead.register(tool)
    names = {t.name for t in lead.list_all()}
    assert LEAD_TOOL_NAMES <= names
    assert names.isdisjoint(MEMBER_TOOL_NAMES)
    assert base.get("team_spawn") is None


def test_member_registry_only_adds_member_tools(
    settings: TeamSettings, tmp_path: Path, board,
):
    base = _base()
    member = build_member_registry(
        base, tmp_path / "wt", board, FakeManager(), "alice")
    names = {t.name for t in member.list_all()}
    assert MEMBER_TOOL_NAMES <= names
    assert names.isdisjoint(LEAD_TOOL_NAMES)
    assert member.get("agent") is None
    assert member.get("load_skill") is None
    assert member.get("read").workspace == tmp_path / "wt"


def test_filter_team_tools_for_fork():
    parent = frozenset({"read", "write", "team_spawn", "task_list",
                        "mcp_tool"})
    assert filter_team_tools(parent) == {"read", "write", "mcp_tool"}


def test_ordinary_subagent_base_is_clean():
    base = _base()
    names = {t.name for t in base.list_all()}
    assert "team_spawn" not in names
    assert "task_list" not in names
