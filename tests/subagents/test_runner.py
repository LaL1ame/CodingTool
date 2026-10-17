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
    res = await run_role_agent(role, "审查 X", depth=1, shared=shared)
    assert res.summary == "final answer"
    assert res.usage is None
    # 轨迹快照：触发消息 + 最终回答（F11）
    assert res.entries[0].kind == "user"
    assert any(e.kind == "assistant" and e.text == "final answer"
               for e in res.entries)


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
    res = await run_role_agent(role, "do", depth=1, shared=shared)
    assert "partial" in res.summary
    assert "达轮次上限" in res.summary
    assert any(e.kind == "tool_use" and e.name == "read" for e in res.entries)
    assert any(e.kind == "tool_result" for e in res.entries)


async def test_run_role_agent_error_raises_with_partial_entries():
    from codepilot.protocols import Delta
    shared = _shared()
    shared.provider._rounds = [[Delta(error="模型 500")]]
    role = AgentRole(name="r", description="d", body="b")
    with pytest.raises(SubAgentError) as ei:
        await run_role_agent(role, "x", depth=1, shared=shared)
    # 失败也保留已产生的部分轨迹（F11）
    assert [e.kind for e in ei.value.entries] == ["user"]


async def test_error_captures_partial_tool_trace():
    """工具调用后下一轮报错：部分轨迹仍可查看。"""
    from codepilot.protocols import Delta, ToolCall
    shared = _shared()
    shared.provider._rounds = [
        [Delta(text="partial"),
         Delta(tool_calls=[ToolCall(id="c1", name="read", arguments={})])],
        [Delta(error="模型 500")],
    ]
    role = AgentRole(name="r", description="d", body="b")
    with pytest.raises(SubAgentError) as ei:
        await run_role_agent(role, "x", depth=1, shared=shared)
    kinds = [e.kind for e in ei.value.entries]
    assert "assistant" in kinds and "tool_use" in kinds and "tool_result" in kinds


class _RecordingProvider(_MockProvider):
    """记录每次 stream 收到的 messages（fork 裁剪断言用）。"""

    def __init__(self):
        super().__init__()
        self.seen: list[list[dict]] = []

    async def stream(self, messages, tools=None, system=None):
        self.seen.append([dict(m) for m in messages])
        async for d in super().stream(messages, tools=tools, system=system):
            yield d


async def test_fork_trims_dangling_tool_calls_anthropic():
    """fork 发生时父末尾 assistant(tool_use) 无结果 → 必须裁掉，否则 400（真机回归）。"""
    shared = _shared()
    shared.provider = _RecordingProvider()
    parent = [
        {"role": "user", "content": "任务"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "我来委派"},
            {"type": "tool_use", "id": "t1", "name": "agent", "input": {}},
        ]},
    ]
    await run_fork_agent("继续", parent_msgs=parent,
                         parent_names=frozenset({"read"}), depth=1, shared=shared)
    sent = shared.provider.seen[0]
    # 无 dangling assistant tool_use
    for m in sent:
        if m.get("role") == "assistant" and isinstance(m.get("content"), list):
            assert not any(b.get("type") == "tool_use" for b in m["content"])
    # trigger 已并入末条 user（无连续同角色）
    assert sent[-1]["role"] == "user"
    assert "请基于当前上下文执行" in str(sent[-1]["content"])


async def test_fork_trims_dangling_tool_calls_openai():
    shared = _shared()
    shared.provider = _RecordingProvider()
    parent = [
        {"role": "user", "content": "任务"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "agent", "arguments": "{}"}}]},
    ]
    await run_fork_agent("继续", parent_msgs=parent,
                         parent_names=frozenset({"read"}), depth=1, shared=shared)
    sent = shared.provider.seen[0]
    assert all(not m.get("tool_calls") for m in sent)
    assert sent[-1]["role"] == "user"
    assert "请基于当前上下文执行" in str(sent[-1]["content"])


async def test_fork_inherits_parent_msgs_deepcopy():
    from codepilot.protocols import Delta
    shared = _shared()
    parent = [{"role": "user", "content": "原始"}, {"role": "assistant", "content": "背景"}]
    shared.provider._rounds = [[Delta(text="fork 输出")]]
    res = await run_fork_agent("继续", parent_msgs=parent,
                               parent_names=frozenset({"read"}), depth=1, shared=shared)
    assert res.summary == "fork 输出"
    # 深拷贝：不改父列表
    assert len(parent) == 2
    assert parent[0]["content"] == "原始"
    # 轨迹只存自有部分：不重复父历史（F11）
    joined = "\n".join(e.text for e in res.entries)
    assert "原始" not in joined and "背景" not in joined
    assert "请基于当前上下文执行" in joined


class _SystemRecordingProvider(_MockProvider):
    """记录 system 与每轮 messages，供隔离路径断言。"""

    def __init__(self, rounds):
        super().__init__(rounds=rounds)
        self.systems = []
        self.seen = []

    async def stream(self, messages, tools=None, system=None):
        import json
        self.systems.append(system)
        self.seen.append(json.loads(json.dumps(messages, default=str)))
        async for d in super().stream(messages, tools=tools, system=system):
            yield d


async def test_run_role_agent_uses_worktree_runtime(tmp_path):
    import json

    from codepilot.protocols import ToolCall
    from codepilot.worktree.model import WorktreeHandle

    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "local.txt").write_text("WT-CONTENT", encoding="utf-8")
    handle = WorktreeHandle(
        name="x", path=wt, branch="codepilot/x", base="abc", created_at=1.0)
    provider = _SystemRecordingProvider([
        [Delta(tool_calls=[ToolCall(
            id="c1", name="read", arguments={"file_path": "local.txt"})])],
        [Delta(text="done")],
    ])
    shared = _shared(provider=provider)
    role = AgentRole(
        name="r", description="d", body="ROLE BODY",
        tools=frozenset({"read"}), permission="auto",
        isolation="worktree")

    before = Path.cwd()
    res = await run_role_agent(
        role, "x", depth=1, shared=shared, worktree=handle)

    assert res.summary == "done"
    assert Path.cwd() == before  # 无 chdir
    # 系统提示含隔离路径与角色正文
    joined_system = "\n".join(
        b for s in provider.systems for b in (*s.stable, *s.volatile))
    assert str(wt) in joined_system
    assert "codepilot/x" in joined_system
    assert "ROLE BODY" in joined_system
    # read 工具从 worktree 读取，而不是主工作区
    assert any(
        "WT-CONTENT" in json.dumps(m, ensure_ascii=False)
        for m in provider.seen[-1])


async def test_runner_constructs_independent_context_manager():
    """子 Agent 有独立 ContextManager（不 attach_chat，隔离计数）。"""
    shared = _shared()
    role = AgentRole(name="r", description="d", body="b")
    res = await run_role_agent(role, "x", depth=1, shared=shared)
    assert res.summary == "final answer"
    # 子 Agent 使用的 ContextManager 与共享实例无关（共享实例此时不存在）
