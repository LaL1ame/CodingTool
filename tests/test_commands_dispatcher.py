"""T4 单测 —— 分发器(F3/N4):返回 str→转发 / None→本地 / 协程→awaitable。"""
import inspect

from codepilot.commands.dispatcher import dispatch_command
from codepilot.commands.registry import CommandKind, CommandSpec


class DummyCtx:
    pass


def _spec(handler):
    return CommandSpec(name="x", kind=CommandKind.UI, desc="d", handler=handler)


def test_sync_returns_string():
    s = _spec(lambda ctx, p: f"forward:{p}")
    out = dispatch_command(s, DummyCtx(), "hi")
    assert out == "forward:hi"
    assert isinstance(out, str)


def test_sync_returns_none():
    calls = []
    s = _spec(lambda ctx, p: calls.append(p) or None)
    out = dispatch_command(s, DummyCtx(), "p")
    assert out is None
    assert calls == ["p"]


def test_coroutine_returns_awaitable():
    async def handler(ctx, p):
        return "async-ok"

    s = _spec(handler)
    out = dispatch_command(s, DummyCtx(), "")
    assert inspect.isawaitable(out)

    async def _run():
        return await out

    import asyncio
    assert asyncio.run(_run()) == "async-ok"