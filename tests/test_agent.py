"""测试 Agent 编排层 — ReAct 多轮循环。"""

import asyncio
import time
from collections.abc import AsyncIterator

from codepilot.protocols import Delta, ToolCall, ToolResult
from codepilot.providers import BaseProvider
from codepilot.config import ProviderConfig
from codepilot.agent import Agent
from codepilot.tools.registry import ToolRegistry
from codepilot.tools.base import Tool


class _MockProvider(BaseProvider):
    """脚本化 mock：每次 stream() 调用返回下一轮 deltas，用尽后返回空流。"""

    def __init__(self, protocol="anthropic", rounds=None):
        self.config = ProviderConfig(name="m", protocol=protocol, model="m", api_key="k", base_url="http://m")
        self._rounds = rounds or []
        self._calls = 0

    async def stream(self, messages, tools=None) -> AsyncIterator[Delta]:
        idx = self._calls
        self._calls += 1
        if idx >= len(self._rounds):
            return
        for d in self._rounds[idx]:
            yield d


class _SlowTool(Tool):
    def __init__(self, name, delay=0.0, side_effect=False):
        self._name = name
        self.delay = delay
        self._side_effect = side_effect

    @property
    def name(self): return self._name
    @property
    def description(self): return f"Mock: {self._name}"
    @property
    def parameters(self): return {"type": "object", "properties": {}}
    @property
    def side_effect(self): return self._side_effect

    async def execute(self, **kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)
        return ToolResult(call_id="", name=self._name, output=f"result from {self._name}")


class TestAgentPureText:
    async def test_pure_text_passthrough(self):
        provider = _MockProvider(rounds=[[Delta(text="Hello"), Delta(text=" world"), Delta(done=True)]])
        agent = Agent(provider, ToolRegistry(), workspace=None)
        deltas = [d async for d in agent.run([])]
        assert len([d for d in deltas if d.text]) == 2
        assert any(d.done for d in deltas)
        assert not any(d.tool_calls for d in deltas)


class TestAgentToolExecution:
    async def test_single_tool_call(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("read"))
        provider = _MockProvider(rounds=[
            [Delta(tool_calls=[ToolCall(id="1", name="read", arguments={})])],
            [Delta(text="Content"), Delta(done=True)],
        ])
        agent = Agent(provider, registry, workspace=None)
        deltas = [d async for d in agent.run([])]
        results = [d for d in deltas if d.tool_result]
        assert len(results) == 1 and results[0].tool_result.output == "result from read"

    async def test_tool_error_handling(self):
        class _ErrorTool(Tool):
            @property
            def name(self): return "bad"
            @property
            def description(self): return ""
            @property
            def parameters(self): return {"type": "object", "properties": {}}
            async def execute(self, **kwargs):
                return ToolResult(call_id="", name="bad", error="oops")

        registry = ToolRegistry()
        registry.register(_ErrorTool())
        provider = _MockProvider(rounds=[
            [Delta(tool_calls=[ToolCall(id="1", name="bad", arguments={})])],
            [Delta(text="Ok"), Delta(done=True)],
        ])
        agent = Agent(provider, registry, workspace=None)
        deltas = [d async for d in agent.run([])]
        assert any(d.tool_result and d.tool_result.error == "oops" for d in deltas)

    async def test_unknown_tool(self):
        provider = _MockProvider(rounds=[
            [Delta(tool_calls=[ToolCall(id="1", name="unknown", arguments={})])],
            [Delta(text="Sorry"), Delta(done=True)],
        ])
        agent = Agent(provider, ToolRegistry(), workspace=None)
        deltas = [d async for d in agent.run([])]
        assert any(d.tool_result and "未知工具" in d.tool_result.error for d in deltas)


class TestAgentMultiRound:
    async def test_two_tool_rounds_then_text(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("read"))
        registry.register(_SlowTool("grep"))
        provider = _MockProvider(rounds=[
            [Delta(tool_calls=[ToolCall(id="1", name="read", arguments={})])],
            [Delta(tool_calls=[ToolCall(id="2", name="grep", arguments={})])],
            [Delta(text="Final"), Delta(done=True)],
        ])
        agent = Agent(provider, registry, workspace=None)
        deltas = [d async for d in agent.run([])]
        results = [d for d in deltas if d.tool_result]
        assert len(results) == 2
        assert any(d.done for d in deltas)


class TestAgentParallel:
    async def test_parallel_execution_timing(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("read", delay=0.1))
        registry.register(_SlowTool("grep", delay=0.1))
        registry.register(_SlowTool("glob", delay=0.1))
        provider = _MockProvider(rounds=[
            [Delta(tool_calls=[
                ToolCall(id="1", name="read", arguments={}),
                ToolCall(id="2", name="grep", arguments={}),
                ToolCall(id="3", name="glob", arguments={}),
            ])],
            [Delta(text="Done"), Delta(done=True)],
        ])
        agent = Agent(provider, registry, workspace=None)
        start = time.perf_counter()
        deltas = [d async for d in agent.run([])]
        elapsed = time.perf_counter() - start
        assert len([d for d in deltas if d.tool_result]) == 3
        assert elapsed < 0.25


class TestAgentBatching:
    async def test_side_effect_tools_serial(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("write", delay=0.1, side_effect=True))
        registry.register(_SlowTool("edit", delay=0.1, side_effect=True))
        registry.register(_SlowTool("run", delay=0.1, side_effect=True))
        provider = _MockProvider(rounds=[
            [Delta(tool_calls=[
                ToolCall(id="1", name="write", arguments={}),
                ToolCall(id="2", name="edit", arguments={}),
                ToolCall(id="3", name="run", arguments={}),
            ])],
            [Delta(text="Done"), Delta(done=True)],
        ])
        agent = Agent(provider, registry, workspace=None)
        start = time.perf_counter()
        deltas = [d async for d in agent.run([])]
        elapsed = time.perf_counter() - start
        assert len([d for d in deltas if d.tool_result]) == 3
        assert elapsed >= 0.25  # 副作用串行：三者之和

    async def test_mixed_batching_preserves_order(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("read", delay=0.05))
        registry.register(_SlowTool("grep", delay=0.05))
        registry.register(_SlowTool("write", delay=0.05, side_effect=True))
        provider = _MockProvider(rounds=[
            [Delta(tool_calls=[
                ToolCall(id="1", name="read", arguments={}),
                ToolCall(id="2", name="grep", arguments={}),
                ToolCall(id="3", name="write", arguments={}),
                ToolCall(id="4", name="read", arguments={}),
            ])],
            [Delta(text="Done"), Delta(done=True)],
        ])
        agent = Agent(provider, registry, workspace=None)
        deltas = [d async for d in agent.run([])]
        results = [d for d in deltas if d.tool_result]
        assert [r.tool_result.call_id for r in results] == ["1", "2", "3", "4"]


class TestAgentStopConditions:
    async def test_iteration_limit(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("read"))
        rounds = [[Delta(tool_calls=[ToolCall(id=str(i), name="read", arguments={})])] for i in range(3)]
        provider = _MockProvider(rounds=rounds)
        agent = Agent(provider, registry, workspace=None, max_rounds=3)
        deltas = [d async for d in agent.run([])]
        assert any(d.error and "迭代上限" in d.error for d in deltas)
        assert len([d for d in deltas if d.tool_result]) == 3

    async def test_consecutive_unknown_tools(self):
        provider = _MockProvider(rounds=[
            [Delta(tool_calls=[ToolCall(id="1", name="unknown_a", arguments={})])],
            [Delta(tool_calls=[ToolCall(id="2", name="unknown_b", arguments={})])],
        ])
        agent = Agent(provider, ToolRegistry(), workspace=None, max_unknown=2)
        deltas = [d async for d in agent.run([])]
        assert any(d.error and "未知工具" in d.error for d in deltas)

    async def test_cancel_interrupts_tool(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("read", delay=5.0))
        provider = _MockProvider(rounds=[
            [Delta(tool_calls=[ToolCall(id="1", name="read", arguments={})])],
            [Delta(text="Done"), Delta(done=True)],
        ])
        agent = Agent(provider, registry, workspace=None)
        deltas = []
        start = time.perf_counter()

        async def consume():
            async for d in agent.run([]):
                deltas.append(d)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.2)
        agent.cancel()
        await task
        elapsed = time.perf_counter() - start
        assert any(d.cancelled for d in deltas)
        assert elapsed < 3.0  # 被中断，而非等满 5s


class TestAgentPlanMode:
    def test_system_prompt_plan_mode(self):
        agent = Agent(_MockProvider(), ToolRegistry(), workspace=None)
        normal = agent.system_prompt()
        agent.set_plan_mode(True)
        plan = agent.system_prompt()
        assert plan != normal
        assert "计划模式" in plan
        agent.set_plan_mode(False)
        assert agent.system_prompt() == normal

    def test_build_tools_read_only_in_plan_mode(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("read"))
        registry.register(_SlowTool("write", side_effect=True))
        agent = Agent(_MockProvider(protocol="anthropic"), registry, workspace=None)
        agent.set_plan_mode(True)
        assert [t["name"] for t in agent._build_tools()] == ["read"]
        agent.set_plan_mode(False)
        assert set(t["name"] for t in agent._build_tools()) == {"read", "write"}


class TestAgentHistoryInjection:
    async def test_anthropic_injection(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("read"))
        provider = _MockProvider(protocol="anthropic", rounds=[
            [Delta(tool_calls=[ToolCall(id="abc", name="read", arguments={"file_path": "x.py"})])],
            [Delta(text="Done"), Delta(done=True)],
        ])
        agent = Agent(provider, registry, workspace=None)
        messages = [{"role": "system", "content": "You are an agent."}, {"role": "user", "content": "Read"}]
        [d async for d in agent.run(messages)]
        assert messages[2]["role"] == "assistant"
        assert messages[2]["content"][0]["type"] == "tool_use"
        assert messages[3]["content"][0]["type"] == "tool_result"

    async def test_openai_injection(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("read"))
        provider = _MockProvider(protocol="openai", rounds=[
            [Delta(tool_calls=[ToolCall(id="call_x", name="read", arguments={"file_path": "x.py"})])],
            [Delta(text="Done"), Delta(done=True)],
        ])
        agent = Agent(provider, registry, workspace=None)
        messages = [{"role": "system", "content": "You are an agent."}, {"role": "user", "content": "Read"}]
        [d async for d in agent.run(messages)]
        assert messages[2]["tool_calls"][0]["function"]["name"] == "read"
        assert messages[3]["role"] == "tool"
