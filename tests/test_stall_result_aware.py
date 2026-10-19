"""state19 设计补充 A：空转守卫「结果感知」。

- 同参数调用但结果在变（外部世界推进，如 Lead 轮询任务板/成员状态）
  → 视为合法轮询，不触发空转。
- 参数与结果都相同（真死循环）→ 保留 hint / 硬停（既有 TestStallDetection 覆盖）。
"""

from codepilot.agent import Agent
from codepilot.protocols import Delta, ToolCall, ToolResult
from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry
from tests.test_agent import _MockProvider


class _VarTool(Tool):
    """每次执行返回递增结果（模拟外部状态在变）。"""

    def __init__(self, name: str):
        self._name = name
        self._n = 0

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
        self._n += 1
        return ToolResult(call_id="", name=self._name,
                          output=f"state #{self._n}")


async def _collect(agent: Agent, msgs):
    out = []
    async for d in agent.run(msgs):
        out.append(d)
    return out


async def test_result_changing_is_not_stall():
    """Lead 轮询场景：连续同参数 team_task_list，但结果在变 → 不判空转。"""
    reg = ToolRegistry()
    reg.register(_VarTool("team_task_list"))
    rounds = [
        [Delta(tool_calls=[ToolCall(id=str(i), name="team_task_list", arguments={})])]
        for i in range(5)
    ]
    provider = _MockProvider(rounds=rounds)
    agent = Agent(provider, reg, workspace=None, max_rounds=20,
                  stall_repeat_k=2, stall_window=3)
    deltas = await _collect(agent, [])
    assert not any(d.pause_reason == "stall" for d in deltas), \
        "结果在变时不应判定空转"
    assert not any(d.stall_hint is not None for d in deltas)


async def test_result_same_still_stops():
    """真死循环：同参数且结果恒定 → 仍触发 hint 并硬停。"""
    reg = ToolRegistry()
    reg.register(_ConstTool("dead"))
    provider = _MockProvider(rounds=[
        [Delta(tool_calls=[ToolCall(id=str(i), name="dead", arguments={})])]
        for i in range(5)
    ])
    agent = Agent(provider, reg, workspace=None, max_rounds=20,
                  stall_repeat_k=2, stall_window=3)
    deltas = await _collect(agent, [])
    assert any(d.stall_hint is not None for d in deltas)
    assert any(d.pause_reason == "stall" for d in deltas)


class _ConstTool(Tool):
    """恒定结果（模拟真死循环：重复同一工具、结果不变）。"""

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
        return ToolResult(call_id="", name=self._name, output="same")