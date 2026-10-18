"""main 装配相关的 registry 分流与模块导入检查。"""

from pathlib import Path
from types import SimpleNamespace

from codepilot.mcp.manager import McpManager
from codepilot.teams import ALL_TEAM_TOOL_NAMES, filter_team_tools
from codepilot.teams.manager import TeamManager
from codepilot.teams.model import TeamSettings
from codepilot.teams.tools_lead import LEAD_TOOL_NAMES, build_lead_tools
from codepilot.tools.registry import ToolRegistry


def test_main_imports():
    import codepilot.main  # noqa: F401

    assert callable(codepilot.main.main)


def test_build_registry_is_clean_base(tmp_path: Path):
    from codepilot.main import _build_registry

    base = _build_registry(tmp_path)
    names = {t.name for t in base.list_all()}
    assert names == {"read", "write", "edit", "glob", "grep", "run"}
    assert names.isdisjoint(ALL_TEAM_TOOL_NAMES)


def test_lead_registry_is_base_plus_lead_tools(
    settings: TeamSettings, tmp_path: Path,
):
    base = ToolRegistry()
    from codepilot.tools.base import Tool
    from codepilot.protocols import ToolResult

    class _T(Tool):
        def __init__(self, name):
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

    for name in ("read", "write", "agent", "load_skill"):
        base.register(_T(name))
    manager = TeamManager(
        settings, workspace=tmp_path, base_registry=base, roles={},
        provider=SimpleNamespace(config=SimpleNamespace(model="m")))
    lead = base.clone()
    for tool in build_lead_tools(manager):
        lead.register(tool)
    names = {t.name for t in lead.list_all()}
    assert names == {"read", "write", "agent", "load_skill"} | LEAD_TOOL_NAMES


def test_mcp_extra_registries_receive_tools():
    base = ToolRegistry()
    lead = ToolRegistry()
    mgr = McpManager(base, extra_registries=[lead])
    assert mgr._registries == [base, lead]


def test_fork_parent_visible_filters_team_tools():
    names = {"read", "write", "team_spawn", "task_list"}
    assert filter_team_tools(names) == {"read", "write"}
