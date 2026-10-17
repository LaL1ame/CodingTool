"""agent 工具测试：参数分流、常驻属性、永不抛错（F2）。"""

import pytest

from codepilot.protocols import ToolResult
from codepilot.subagents.tool import AgentTool


class _FakeManager:
    def __init__(self):
        self.calls = []
        self.known = {"reviewer": None, "architect": None}

    async def delegate(self, kind, prompt, *, role=None, background=False,
                       timeout=None, worktree=None):
        self.calls.append((kind, prompt, role, background, timeout, worktree))
        return ToolResult(call_id="", name="agent", output="ok")


@pytest.fixture
def mgr():
    return _FakeManager()


def test_agent_tool_is_system_exempt_and_no_side_effect(mgr):
    t = AgentTool(mgr)
    assert t.system_exempt is True
    assert t.side_effect is False


def test_agent_tool_name_and_schema(mgr):
    t = AgentTool(mgr)
    assert t.name == "agent"
    assert t.parameters["required"] == ["mode", "prompt"]
    assert t.parameters["properties"]["mode"]["enum"] == ["defined", "fork"]
    # state17 修正：角色名进 description + role 枚举，引导模型主动委派
    assert "architect" in t.description
    assert t.parameters["properties"]["role"]["enum"] == ["architect", "reviewer"]
    assert "worktree" in t.parameters["properties"]


async def test_defined_delegation(mgr):
    t = AgentTool(mgr)
    res = await t.execute(mode="defined", role="reviewer", prompt="审查 X")
    assert res.error is None
    assert mgr.calls[0] == ("defined", "审查 X", "reviewer", False, None, None)


async def test_worktree_param_passthrough(mgr):
    t = AgentTool(mgr)
    await t.execute(mode="defined", role="architect", prompt="改文件",
                    worktree="auth-fix")
    assert mgr.calls[0][5] == "auth-fix"


async def test_fork_delegation_background_passthrough(mgr):
    t = AgentTool(mgr)
    await t.execute(mode="fork", prompt="总结", background=True, timeout=30)
    assert mgr.calls[0][0] == "fork"
    assert mgr.calls[0][3] is True
    assert mgr.calls[0][4] == 30.0
    assert mgr.calls[0][5] is None


async def test_missing_mode(mgr):
    t = AgentTool(mgr)
    res = await t.execute(prompt="x")
    assert "mode" in res.error
    assert mgr.calls == []


async def test_missing_prompt(mgr):
    t = AgentTool(mgr)
    res = await t.execute(mode="defined", role="r")
    assert "prompt" in res.error


async def test_manager_error_returns_toolresult(mgr):
    async def _bad(*a, **k):
        return ToolResult(call_id="", name="agent", error="后台并发已满")
    mgr.delegate = _bad
    t = AgentTool(mgr)
    res = await t.execute(mode="defined", role="r", prompt="x")
    assert "并发已满" in res.error


async def test_never_raises(mgr):
    """manager 内部抛异常 → 工具返回 error ToolResult，不外抛（F2）。"""
    async def _boom(*a, **k):
        raise RuntimeError("内部炸了")
    mgr.delegate = _boom
    t = AgentTool(mgr)
    res = await t.execute(mode="defined", role="r", prompt="x")
    assert res.error is not None
    assert "内部炸了" in res.error
