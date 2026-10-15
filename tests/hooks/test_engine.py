"""state16 T6：HookEngine 匹配 / fire / once / async / timeout / 声明顺序。"""

import asyncio
import sys

import pytest

from codepilot.hooks.engine import HookEngine
from codepilot.hooks.injector import HookInjector
from codepilot.hooks.model import CondItem, Condition, HookAction, HookRule

PY = sys.executable


def rule(event="user_message", cond=None, atype="prompt", afields=None,
         once=False, is_async=False, timeout=None, source="t", ordinal=0):
    return HookRule(
        event=event, condition=cond,
        action=HookAction(type=atype, fields=afields or {"text": "hi"},
                          timeout=timeout, is_async=is_async, once=once),
        source=source, ordinal=ordinal)


def cond(field, pattern, mode="all"):
    return Condition(mode=mode, items=(CondItem(field, pattern),))


@pytest.fixture
def engine():
    return HookInjector()


class TestMatching:
    @pytest.mark.asyncio
    async def test_no_condition_always_fires(self, engine):
        e = HookEngine([rule()], engine)
        await e.fire("user_message", {"text": "anything"})
        assert engine.hook_blocks() == ["hi"]

    @pytest.mark.asyncio
    async def test_all_requires_every_item(self, engine):
        c = Condition(mode="all", items=(
            CondItem("text", "hello*"), CondItem("text", "*world")))
        e = HookEngine([rule(cond=c)], engine)
        await e.fire("user_message", {"text": "hello world"})
        await e.fire("user_message", {"text": "hello"})
        assert engine.hook_blocks() == ["hi"]  # 第二次不命中，块未变

    @pytest.mark.asyncio
    async def test_any_requires_one(self, engine):
        c = Condition(mode="any", items=(
            CondItem("text", "foo"), CondItem("text", "bar")))
        e = HookEngine([rule(cond=c, afields={"text": "hit"})], engine)
        await e.fire("user_message", {"text": "bar"})
        assert engine.hook_blocks() == ["hit"]

    @pytest.mark.asyncio
    async def test_event_filtering(self, engine):
        e = HookEngine([rule(event="compact")], engine)
        await e.fire("user_message", {"text": "x"})
        assert engine.hook_blocks() == []
        await e.fire("compact", {})
        assert engine.hook_blocks() == ["hi"]

    @pytest.mark.asyncio
    async def test_fields_str_coercion(self, engine):
        e = HookEngine([rule(event="round_start", cond=cond("round", "2"),
                             afields={"text": "r2"})], engine)
        await e.fire("round_start", {"round": 2})
        assert engine.hook_blocks() == ["r2"]

    @pytest.mark.asyncio
    async def test_zero_rules_shortcircuit(self, engine):
        e = HookEngine([], engine)
        await e.fire("user_message", {"text": "x"})  # 不炸即通过
        assert engine.hook_blocks() == []


class TestOrder:
    @pytest.mark.asyncio
    async def test_declaration_order(self, engine):
        rules = [rule(afields={"text": "first"}, source="u", ordinal=0),
                 rule(afields={"text": "second"}, source="p", ordinal=0)]
        e = HookEngine(rules, engine)
        await e.fire("user_message", {"text": "x"})
        assert engine.hook_blocks() == ["first", "second"]


class TestOnce:
    @pytest.mark.asyncio
    async def test_once_fires_single_time(self, engine):
        e = HookEngine([rule(atype="command",
                             afields={"cmd": f'"{PY}" -c "pass"'}, once=True)],
                       engine)
        await e.fire("user_message", {"text": "a"})
        await e.fire("user_message", {"text": "b"})  # 应跳过
        assert len(e._fired_once) == 1

    @pytest.mark.asyncio
    async def test_once_keyed_by_source_ordinal(self, engine):
        rules = [rule(once=True, source="a", ordinal=0, afields={"text": "A"}),
                 rule(once=True, source="a", ordinal=1, afields={"text": "B"})]
        e = HookEngine(rules, engine)
        await e.fire("user_message", {"text": "x"})
        assert engine.hook_blocks() == ["A", "B"]  # 不同 ordinal 各自触发


class TestAsyncAndTimeout:
    @pytest.mark.asyncio
    async def test_async_not_blocking_and_notifies(self, engine):
        notes = []
        e = HookEngine(
            [rule(atype="command",
                  afields={"cmd": f'"{PY}" -c "import time; time.sleep(0.2)"'},
                  is_async=True)],
            engine, notify=notes.append)
        await asyncio.wait_for(e.fire("user_message", {"text": "x"}), timeout=1)
        assert not notes  # fire 返回时后台任务未完成
        await e.aclose()
        assert notes == ["[hook] t#0 ok"]

    @pytest.mark.asyncio
    async def test_async_failure_notifies(self, engine):
        notes = []
        e = HookEngine(
            [rule(atype="command", afields={"cmd": f'"{PY}" -c "import sys; sys.exit(1)"'},
                  is_async=True)],
            engine, notify=notes.append)
        await e.fire("user_message", {"text": "x"})
        await e.aclose()
        assert notes and "failed" in notes[0]

    @pytest.mark.asyncio
    async def test_sync_command_timeout_is_failure(self, engine):
        notes = []
        e = HookEngine(
            [rule(atype="command",
                  afields={"cmd": f'"{PY}" -c "import time; time.sleep(5)"'},
                  timeout=0.3)],
            engine, notify=notes.append)
        await e.fire("user_message", {"text": "x"})  # 主流程不炸
        assert notes and "failed" in notes[0]

    @pytest.mark.asyncio
    async def test_aclose_cancels_pending(self, engine):
        e = HookEngine(
            [rule(atype="command",
                  afields={"cmd": f'"{PY}" -c "import time; time.sleep(60)"'},
                  is_async=True)],
            engine)
        await e.fire("user_message", {"text": "x"})
        await asyncio.wait_for(e.aclose(), timeout=10)
        assert not e._bg_tasks


class TestFaultIsolation:
    @pytest.mark.asyncio
    async def test_action_exception_does_not_propagate(self, engine):
        # 非法 url 的 http 动作会失败；fire 不抛、后续规则仍执行
        rules = [
            rule(atype="http", afields={"url": "http://127.0.0.1:1/x"}, ordinal=0),
            rule(afields={"text": "after"}, ordinal=1),
        ]
        e = HookEngine(rules, engine)
        await e.fire("user_message", {"text": "x"})
        assert engine.hook_blocks() == ["after"]
