"""隔离子 Agent 运行时装配。"""

from pathlib import Path

from codepilot.config import ProviderConfig
from codepilot.permissions import PermissionManager
from codepilot.permissions.mode import PermissionMode
from codepilot.prompt import EnvContext
from codepilot.providers import BaseProvider
from codepilot.protocols import ToolResult
from codepilot.subagents.isolation import (
    build_isolated_registry,
    build_isolated_runtime,
    isolation_prompt_block,
)
from codepilot.subagents.model import AgentRole
from codepilot.subagents.runner import SharedContext
from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry
from codepilot.worktree.model import WorktreeHandle


class _MockProvider(BaseProvider):
    def __init__(self):
        self.config = ProviderConfig(
            name="m", protocol="openai", model="m", api_key="k",
            base_url="http://m")

    async def stream(self, messages, tools=None, system=None):
        return
        yield  # pragma: no cover

    async def complete(self, system, messages):
        return ""


class _T(Tool):
    def __init__(self, name):
        self._n = name

    @property
    def name(self): return self._n
    @property
    def description(self): return self._n
    @property
    def parameters(self): return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return ToolResult(call_id="", name=self._n, output="ok")


def _handle(tmp_path: Path) -> WorktreeHandle:
    path = tmp_path / ".codepilot" / "worktrees" / "x"
    path.mkdir(parents=True)
    return WorktreeHandle(
        name="x", path=path, branch="codepilot/x", base="abc",
        created_at=1.0)


def _shared(tmp_path: Path):
    reg = ToolRegistry()
    agent = _T("agent")
    skill = _T("load_skill")
    reg.register(agent)
    reg.register(skill)
    reg.register(_T("read"))
    shared = SharedContext(
        provider=_MockProvider(),
        registry=reg,
        workspace=tmp_path,
        env=EnvContext(cwd=str(tmp_path), date="2026-10-18"),
        parent_permission=PermissionManager(tmp_path),
    )
    return shared, agent, skill


def test_registry_rebinds_file_tools_and_shares_system_tools(tmp_path):
    shared, agent, skill = _shared(tmp_path)
    wt = _handle(tmp_path)
    rt = build_isolated_runtime(
        shared, AgentRole(name="r", description="d", body="b"), wt)

    assert rt.registry.get("read").workspace == wt.path
    assert rt.registry.get("write").workspace == wt.path
    assert rt.registry.get("edit").workspace == wt.path
    assert rt.registry.get("glob").workspace == wt.path
    assert rt.registry.get("grep").workspace == wt.path
    assert rt.registry.get("run").workspace == wt.path
    assert rt.registry.get("agent") is agent
    assert rt.registry.get("load_skill") is skill


def test_env_permission_and_offload_bound_to_worktree(tmp_path):
    shared, _, _ = _shared(tmp_path)
    wt = _handle(tmp_path)
    role = AgentRole(name="r", description="d", body="b", permission="strict")
    rt = build_isolated_runtime(shared, role, wt)

    assert rt.workspace == wt.path
    assert rt.env.cwd == str(wt.path)
    assert rt.env.date == shared.env.date
    assert rt.permission.workspace == wt.path
    assert rt.permission.mode is PermissionMode.STRICT
    assert rt.context_manager.offloader._dir == \
        wt.path / ".codepilot" / "context"
    assert rt.context_manager.offloader._dir.is_dir()


def test_prompt_block_and_memory_come_from_worktree(tmp_path):
    shared, _, _ = _shared(tmp_path)
    wt = _handle(tmp_path)
    rules = wt.path / ".rules"
    rules.mkdir()
    (rules / "local.md").write_text("LOCAL RULE", encoding="utf-8")
    role = AgentRole(name="r", description="d", body="ROLE BODY")
    rt = build_isolated_runtime(shared, role, wt)

    blocks = rt.skill_injector()
    joined = "\n".join(blocks)
    assert "隔离工作目录" in blocks[0]
    assert str(wt.path) in blocks[0]
    assert "codepilot/x" in blocks[0]
    assert "ROLE BODY" in joined
    assert "LOCAL RULE" in joined

    text = isolation_prompt_block(wt)
    assert str(wt.path) in text and "codepilot/x" in text


def test_role_model_override(tmp_path):
    shared, _, _ = _shared(tmp_path)
    wt = _handle(tmp_path)
    role = AgentRole(name="r", description="d", body="b", model="other")
    rt = build_isolated_runtime(shared, role, wt)
    assert rt.provider.config.model == "other"


def test_build_registry_without_template_extras(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    reg = build_isolated_registry(ToolRegistry(), wt)
    assert {t.name for t in reg.list_all()} == {
        "read", "write", "edit", "glob", "grep", "run"}
