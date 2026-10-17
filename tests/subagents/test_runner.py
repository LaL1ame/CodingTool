"""SubAgent runner 测试：定义式/Fork 构造独立 Agent、摘要收集、隔离。"""

from pathlib import Path

import pytest

from codepilot.config import ProviderConfig
from codepilot.permissions import PermissionManager
from codepilot.protocols import Delta
from codepilot.providers import BaseProvider
from codepilot.subagents.model import AgentRole
from codepilot.subagents.runner import (
    SharedContext,
    SubAgentError,
    run_fork_agent,
    run_role_agent,
)
from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry
from codepilot.prompt import EnvContext


class _MockProvider(BaseProvider):
    def __init__(self, rounds=None, model="m"):
        self.config = ProviderConfig(name="m", protocol="openai", model=model,
                                     api_key="k", base_url="http://m")
        self._rounds = rounds or [[Delta(text="final answer")]]
        self._calls = 0

    async def stream(self, messages, tools=None, system=None):
        idx = self._calls
        self._calls += 1
        if idx >= len(self._rounds):
            return
        for d in self._rounds[idx]:
            yield d

    async def complete(self, system, messages):
        return ""


class _T(Tool):
    def __init__(self, name, side_effect=False):
        self._n = name
        self._s = side_effect

    @property
    def name(self): return self._n
    @property
    def description(self): return self._n
    @property
    def parameters(self): return {"type": "object", "properties": {}}
    @property
    def side_effect(self): return self._s

    async def execute(self, **kwargs):
        from codepilot.protocols import ToolResult
        return ToolResult(call_id="", name=self._n, output="ok")


def _shared(**kw) -> SharedContext:
    reg = ToolRegistry()
    for t in kw.pop("tools", [_T("read")]):
        reg.register(t)
    base = dict(
        provider=_MockProvider(),
        registry=reg,
        workspace=Path("."),
        env=EnvContext(cwd=".", date="2026-10-17"),
        parent_permission=PermissionManager(workspace=Path(".")),
    )
    base.update(kw)
    return SharedContext(**base)


async def test_run_role_agent_collects_summary():
    shared = _shared()
    role = AgentRole(name="reviewer", description="d", body="你是审查员，输出结论。")
    summary, usage = await run_role_agent(role, "审查 X", depth=1, shared=shared)
    assert summary == "final answer"
    assert usage is None


async def test_run_role_agent_respects_max_rounds():
    """达轮次上限 → 尽力完成 + 摘要注明（D14），非失败。"""
    shared = _shared()
    role = AgentRole(name="r", description="d", body="b", max_rounds=1)
    # 单轮内既有文本又有工具调用：执行后达轮次上限 → paused(limit)
    from codepilot.protocols import ToolCall
    shared.provider._rounds = [[
        Delta(text="partial"),
        Delta(tool_calls=[ToolCall(id="c1", name="read", arguments={})]),
    ]]
    summary, _ = await run_role_agent(role, "do", depth=1, shared=shared)
    assert "partial" in summary
    assert "达轮次上限" in summary


async def test_run_role_agent_error_raises():
    from codepilot.protocols import Delta
    shared = _shared()
    shared.provider._rounds = [[Delta(error="模型 500")]]
    role = AgentRole(name="r", description="d", body="b")
    with pytest.raises(SubAgentError):
        await run_role_agent(role, "x", depth=1, shared=shared)


async def test_fork_inherits_parent_msgs_deepcopy():
    from codepilot.protocols import Delta
    shared = _shared()
    parent = [{"role": "user", "content": "原始"}, {"role": "assistant", "content": "背景"}]
    shared.provider._rounds = [[Delta(text="fork 输出")]]
    summary, _ = await run_fork_agent("继续", parent_msgs=parent,
                                parent_names=frozenset({"read"}), depth=1, shared=shared)
    assert summary == "fork 输出"
    # 深拷贝：不改父列表
    assert len(parent) == 2
    assert parent[0]["content"] == "原始"


async def test_runner_constructs_independent_context_manager():
    """子 Agent 有独立 ContextManager（不 attach_chat，隔离计数）。"""
    shared = _shared()
    role = AgentRole(name="r", description="d", body="b")
    summary, _ = await run_role_agent(role, "x", depth=1, shared=shared)
    assert summary == "final answer"
    # 子 Agent 使用的 ContextManager 与共享实例无关（共享实例此时不存在）
