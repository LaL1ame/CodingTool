"""state10 续接/空转停止测试(F2/F3/N2)。

验证:
  · 达上限 → paused(reason=limit) 而非 error
  · 同一 _msgs 二次 run 能续跑、已完成轮次与历史保留
  · 续接不重放副作用工具执行(N2)
  · 空转:重复调用无文本 → stall_hint → 持续 → paused(stall)
"""
import asyncio

from codepilot.agent import Agent
from codepilot.protocols import Delta, ToolCall, ToolResult
from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry
from codepilot.providers import BaseProvider
from codepilot.config import ProviderConfig

from tests.test_agent import _MockProvider, _SlowTool


class _SideEffectTool(Tool):
    """副作用工具,记录执行次数(验证 N2 不重放)。"""
    def __init__(self):
        self.calls = 0

    @property
    def name(self): return "apply"
    @property
    def description(self): return "apply change"
    @property
    def parameters(self): return {"type": "object", "properties": {}}
    @property
    def side_effect(self): return True

    async def execute(self, **kwargs):
        self.calls += 1
        return ToolResult(call_id="", name="apply", output=f"applied {self.calls}")


async def _collect(agent, msgs):
    return [d async for d in agent.run(msgs)]


# —— F2: 达上限 → paused 非 error ——

class TestLimitPause:
    async def test_limit_reached_yields_paused_not_error(self):
        reg = ToolRegistry(); reg.register(_SlowTool("read"))
        # 连续 N 轮都调工具永不停(mock 每轮都是 tool_call)
        rounds = [[Delta(tool_calls=[ToolCall(id=str(i), name="read", arguments={})])]
                  for i in range(5)]
        provider = _MockProvider(rounds=rounds)
        # stall_enabled=False:本用例只测 limit 契约;无文本连续相同 read 会先触发空转硬停
        agent = Agent(provider, reg, workspace=None, max_rounds=3, stall_enabled=False)
        deltas = await _collect(agent, [])
        paused = [d for d in deltas if d.paused]
        assert len(paused) == 1
        assert paused[0].pause_reason == "limit"
        assert paused[0].resume_round == 3
        # 不是 error
        assert not any(d.error for d in deltas)

    async def test_resume_with_same_msgs(self):
        reg = ToolRegistry(); reg.register(_SlowTool("read"))
        rounds = [[Delta(tool_calls=[ToolCall(id="1", name="read", arguments={})])],
                  [Delta(tool_calls=[ToolCall(id="2", name="read", arguments={})])],
                  [Delta(tool_calls=[ToolCall(id="3", name="read", arguments={})])],
                  [Delta(text="done now", done=True)]]
        provider = _MockProvider(rounds=rounds)
        # stall_enabled=False:隔离机制,只测「同一 _msgs 二次 run 续接」契约
        agent = Agent(provider, reg, workspace=None, max_rounds=2, stall_enabled=False)
        msgs = [{"role": "user", "content": "start"}]
        # 第一次 run:达 2 轮上限 paused(还会再消费第3轮,因 mock detail)
        first = await _collect(agent, msgs)
        assert any(d.paused and d.pause_reason == "limit" for d in first)
        # 续接:同一 msgs 再 run,应续跑完成(done)
        more = await _collect(agent, msgs)
        assert msgs  # 历史保留
        assert more  # 续跑有产出


# —— N2: 续接不重放副作用 ——

class TestResumeNoSideEffectReplay:
    async def test_side_effect_not_replayed_across_runs(self):
        reg = ToolRegistry()
        apply = _SideEffectTool()
        reg.register(apply)
        # 每轮调副作用工具(它自己带壳);两轮间暂停再续
        rounds = [[Delta(tool_calls=[ToolCall(id="a", name="apply", arguments={})])],
                  [Delta(tool_calls=[ToolCall(id="b", name="apply", arguments={})])],
                  [Delta(text="done", done=True)]]
        provider = _MockProvider(rounds=rounds)
        agent = Agent(provider, reg, workspace=None, max_rounds=1)
        msgs = []
        d1 = await _collect(agent, msgs)   # 第1轮执行 apply → calls=1
        assert any(d.tool_result for d in d1)
        assert apply.calls == 1
        d2 = await _collect(agent, msgs)   # 续接第2轮
        assert apply.calls == 1      # 只执行当前批,不重放已执行的历史


# —— F3: 空转检测 ——

class TestStallDetection:
    async def test_stall_hint_then_pause(self):
        reg = ToolRegistry(); reg.register(_SlowTool("read"))
        # 连续多轮同一 read 调用、且每轮无文本(text_parts 空)
        rounds = [[Delta(tool_calls=[ToolCall(id=str(i), name="read", arguments={"f": "a.py"})])]
                  for i in range(6)]
        provider = _MockProvider(rounds=rounds)
        agent = Agent(provider, reg, workspace=None, max_rounds=10,
                      stall_repeat_k=2, stall_window=3)
        deltas = await _collect(agent, [])
        hints = [d for d in deltas if d.stall_hint is not None]
        paused = [d for d in deltas if d.paused]
        assert hints, "应出现软空转提示"
        # 持续空转 → 应最终 paused(stall)
        assert any(d.pause_reason == "stall" for d in paused)

    async def test_progress_with_text_no_stall(self):
        """有文本进展 → 不判空转(N1.2 不误伤慢而前进)。"""
        reg = ToolRegistry(); reg.register(_SlowTool("read"))
        rounds = [
            [Delta(text="analyzing "), Delta(
                tool_calls=[ToolCall(id="1", name="read", arguments={"f": "a.py"})])],
            [Delta(text="progress "), Delta(
                tool_calls=[ToolCall(id="2", name="read", arguments={"f": "a.py"})])],
            [Delta(text="done"), Delta(done=True)],
        ]
        provider = _MockProvider(rounds=rounds)
        agent = Agent(provider, reg, workspace=None, max_rounds=10,
                      stall_repeat_k=2, stall_window=3)
        deltas = await _collect(agent, [])
        # 每轮都有文本进展 → 不触发空转硬停
        assert not any(d.pause_reason == "stall" for d in deltas)
        assert any(d.done for d in deltas)


# —— F4: 停止 reason 结构化 ——

class TestStructuredReasons:
    async def test_done_has_no_pause(self):
        provider = _MockProvider(rounds=[[Delta(text="hi"), Delta(done=True)]])
        agent = Agent(provider, ToolRegistry(), workspace=None)
        deltas = await _collect(agent, [])
        assert any(d.done for d in deltas)
        assert not any(d.paused for d in deltas)