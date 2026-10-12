"""ContextManager 编排 + F0 跨轮持久回归。"""

import asyncio

import pytest

from codepilot.chat import ChatManager
from codepilot.context import ContextConfig, ContextManager
from codepilot.protocols import Delta, ToolCall, ToolResult


def _make_chat(msgs: list[dict]) -> ChatManager:
    cm = ChatManager(object())  # agent 用假对象(本例不真跑 run)
    cm._msgs = msgs
    return cm


class _FakeProvider:
    def __init__(self, summary="【摘要】结论"):
        self.summary = summary

    @property
    def config(self):
        from codepilot.config import ProviderConfig
        return ProviderConfig(name="p", protocol="anthropic", model="m",
                              api_key="k", base_url="http://m")

    async def complete(self, system, messages):
        return self.summary


def _fresh(msgs, config=None) -> tuple[ContextManager, ChatManager, _FakeProvider]:
    cm = _make_chat(msgs)
    pv = _FakeProvider()
    mgr = ContextManager(chat=cm, provider=pv, config=config or ContextConfig(
        window=10_000, auto_margin=3_000, manual_margin=1_000,
        tool_threshold_n=100,  # 便于层1触发
    ))
    return mgr, cm, pv


async def _run(coro):
    return await coro


async def _drain(ag):
    return [d async for d in ag]


# —— T8: 层2 手动压缩改写 _msgs ——

def _cfg_min_msgs(keep_min=2, window=10_000):
    return ContextConfig(window=window, auto_margin=3_000, manual_margin=1_000,
                         tool_threshold_n=100, compact_keep_min_msgs=keep_min,
                         compact_keep_tokens=10_000)


def test_manual_compact_rewrites_msgs():
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "go"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "doing work"}]},
        {"role": "user", "content": [{"type": "text", "text": "keep me recent"}]},
    ]
    mgr, cm, _ = _fresh(msgs, config=_cfg_min_msgs(keep_min=1))
    res = asyncio.run(mgr.compact_manual())
    assert res is not None
    # _msgs 被改写:早期被摘要替换,保留近期,且首条 content 含【历史摘要】+边界
    new_msgs = cm._msgs
    assert new_msgs  # 非空
    assert len(new_msgs) < len(msgs)
    first_content = new_msgs[0]["content"]
    flat = str(first_content)
    assert "【历史摘要】" in flat
    assert "重新读取" in flat


def test_manual_compact_respects_message_boundary():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "only one"}]}]
    mgr, cm, _ = _fresh(msgs)
    res = asyncio.run(mgr.compact_manual())
    # 单条消息无早期可压 → 不压
    assert res is None
    assert len(cm._msgs) == 1


# —— T8: 层1 prep_tool_result ——

def test_prep_tool_result_offloads_large():
    """非自分页工具(read 以外,如 grep)的大输出 → 存盘成替身。"""
    msgs = [{"role": "user", "content": "hi"}]
    mgr, cm, _ = _fresh(msgs)
    big = ToolResult(call_id="c", name="grep", output="X" * 10000)  # 2500 token>100
    out = asyncio.run(mgr.prep_tool_result(big))
    assert out is not None
    assert "grep" in out.name
    assert "已存盘" in (out.output or "")


def test_prep_tool_result_exempts_read():
    """read 豁免(state10 修正):自带分页,大输出必须完整回灌,不被存盘截断。"""
    msgs = [{"role": "user", "content": "hi"}]
    mgr, cm, _ = _fresh(msgs)
    big = ToolResult(call_id="c", name="read", output="Y" * 10000)  # 2500 token>100
    out = asyncio.run(mgr.prep_tool_result(big))
    assert out.output == "Y" * 10000
    assert "已存盘" not in (out.output or "")


def test_prep_tool_result_keeps_small():
    msgs = [{"role": "user", "content": "hi"}]
    mgr, cm, _ = _fresh(msgs)
    small = ToolResult(call_id="c", name="grep", output="tiny")
    out = asyncio.run(mgr.prep_tool_result(small))
    assert out.output == "tiny"


# —— T8/F6: 摘要失败 3 次熔断 ——

def test_compact_failure_trips_circuit():
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"}, {"role": "assistant", "content": "d"},
            {"role": "user", "content": "e"}, {"role": "assistant", "content": "f"}]
    mgr, cm, _ = _fresh(msgs, config=_cfg_min_msgs(keep_min=2))

    class _Fail:
        config = _FakeProvider().config
        async def complete(self, system, messages):
            raise RuntimeError("down")

    mgr.provider = _Fail()
    for _ in range(3):
        with pytest.raises(RuntimeError):
            asyncio.run(mgr.compact_manual())
    assert mgr.budget.is_openckety
    # 熔断后手动压缩被拒
    assert asyncio.run(mgr.compact_manual()) is None
    # 解除
    mgr.reset_circuit()
    assert not mgr.budget.is_openckety


# —— T8/F7: 使用量文本 ——

def test_current_usage_text():
    msgs = [{"role": "user", "content": "hi"}]
    mgr, cm, _ = _fresh(msgs)
    mgr.estimator.add("A" * 4000)  # 1000 token
    txt = mgr.current_usage_text()
    assert "ctx 1K/10K" in txt or "ctx 0K/10K" in txt  # 1000//1000=1K 或低估
    assert "%" in txt


# —— F0 回归(整合) ——

class _ToolAgent:
    async def run(self, messages):
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": "c1", "name": "read", "input": {}}]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": "DATA"}]})
        yield Delta(round=1, max_rounds=2)
        yield Delta(tool_calls=[ToolCall(id="c1", name="read", arguments={})])
        yield Delta(tool_result=ToolResult(call_id="c1", name="read", output="DATA"))
        yield Delta(done=True)


def test_f0_tool_result_survives_with_manager(workspace):
    cm = ChatManager(_ToolAgent())
    pv = _FakeProvider()
    mgr = ContextManager(chat=cm, provider=pv,
                         config=ContextConfig(window=10_000, tool_threshold_n=10_000),
                         offload_dir=workspace / "ctx")
    asyncio.run(_drain(cm.send_message("look")))
    asyncio.run(_drain(cm.send_message("again")))
    survived = any(isinstance(m.get("content"), list) and any(
        b == {"tool_call_id": "c1", "role": "tool"} or isinstance(b, dict) and
        b.get("tool_use_id") == "c1" for b in m["content"])
        for m in cm._msgs if isinstance(m.get("content"), list))
    assert cm._msgs is cm.build_context()
    assert survived or any("DATA" in str(m) for m in cm._msgs)