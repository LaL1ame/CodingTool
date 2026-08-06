"""测试 Agent 编排层。"""

import asyncio
import time
from collections.abc import AsyncIterator

import pytest

from codepilot.protocols import Delta, ToolCall, ToolResult
from codepilot.providers import BaseProvider
from codepilot.config import ProviderConfig
from codepilot.agent import Agent
from codepilot.tools.registry import ToolRegistry
from codepilot.tools.base import Tool


class _MockProvider(BaseProvider):
    def __init__(self, protocol="anthropic", deltas=None):
        self.config = ProviderConfig(name="m", protocol=protocol, model="m", api_key="k", base_url="http://m")
        self._deltas = deltas or []

    async def stream(self, messages, tools=None) -> AsyncIterator[Delta]:
        for d in self._deltas:
            yield d


class _SlowTool(Tool):
    def __init__(self, name, delay=0.0):
        self._name = name
        self.delay = delay

    @property
    def name(self): return self._name
    @property
    def description(self): return f"Mock: {self._name}"
    @property
    def parameters(self): return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)
        return ToolResult(call_id="", name=self._name, output=f"result from {self._name}")


class TestAgentPureText:
    async def test_pure_text_passthrough(self):
        provider = _MockProvider(deltas=[Delta(text="Hello"), Delta(text=" world"), Delta(done=True)])
        agent = Agent(provider, ToolRegistry(), workspace=None)
        deltas = [d async for d in agent.run([])]
        assert len([d for d in deltas if d.text]) == 2
        assert any(d.done for d in deltas)
        assert not any(d.tool_calls for d in deltas)


class TestAgentToolExecution:
    async def test_single_tool_call(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("read"))
        provider = _MockProvider(deltas=[
            Delta(tool_calls=[ToolCall(id="1", name="read", arguments={})]),
            Delta(text="Content"), Delta(done=True),
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
        provider = _MockProvider(deltas=[
            Delta(tool_calls=[ToolCall(id="1", name="bad", arguments={})]),
            Delta(text="Ok"), Delta(done=True),
        ])
        agent = Agent(provider, registry, workspace=None)
        deltas = [d async for d in agent.run([])]
        assert any(d.tool_result and d.tool_result.error == "oops" for d in deltas)

    async def test_unknown_tool(self):
        provider = _MockProvider(deltas=[
            Delta(tool_calls=[ToolCall(id="1", name="unknown", arguments={})]),
            Delta(text="Sorry"), Delta(done=True),
        ])
        agent = Agent(provider, ToolRegistry(), workspace=None)
        deltas = [d async for d in agent.run([])]
        assert any(d.tool_result and "未知工具" in d.tool_result.error for d in deltas)


class TestAgentParallel:
    async def test_parallel_execution_timing(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("read", delay=0.1))
        registry.register(_SlowTool("grep", delay=0.1))
        registry.register(_SlowTool("glob", delay=0.1))
        provider = _MockProvider(deltas=[
            Delta(tool_calls=[
                ToolCall(id="1", name="read", arguments={}),
                ToolCall(id="2", name="grep", arguments={}),
                ToolCall(id="3", name="glob", arguments={}),
            ]),
            Delta(text="Done"), Delta(done=True),
        ])
        agent = Agent(provider, registry, workspace=None)
        start = time.perf_counter()
        deltas = [d async for d in agent.run([])]
        elapsed = time.perf_counter() - start
        assert len([d for d in deltas if d.tool_result]) == 3
        assert elapsed < 0.25


class TestAgentHistoryInjection:
    async def test_anthropic_injection(self):
        registry = ToolRegistry()
        registry.register(_SlowTool("read"))
        provider = _MockProvider(protocol="anthropic", deltas=[
            Delta(tool_calls=[ToolCall(id="abc", name="read", arguments={"file_path": "x.py"})]),
            Delta(text="Done"), Delta(done=True),
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
        provider = _MockProvider(protocol="openai", deltas=[
            Delta(tool_calls=[ToolCall(id="call_x", name="read", arguments={"file_path": "x.py"})]),
            Delta(text="Done"), Delta(done=True),
        ])
        agent = Agent(provider, registry, workspace=None)
        messages = [{"role": "system", "content": "You are an agent."}, {"role": "user", "content": "Read"}]
        [d async for d in agent.run(messages)]
        assert messages[2]["tool_calls"][0]["function"]["name"] == "read"
        assert messages[3]["role"] == "tool"
