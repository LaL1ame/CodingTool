"""state13 T5 接线测试 —— Agent 注入 memory_blocks + Chat done 触发 auto_memory。"""
import asyncio

import pytest

from codepilot.agent import Agent
from codepilot.chat import ChatManager
from codepilot.memory.inject import MemoryInjector
from codepilot.memory.store import NoteStore
from codepilot.permissions import PermissionManager
from codepilot.permissions.mode import PermissionMode
from codepilot.protocols import Delta
from codepilot.tools.registry import ToolRegistry


class _Cfg:
    protocol = "openai"


class _FakeProvider:
    def __init__(self, rounds=1):
        self._rounds = rounds
        self.captured: list[list[str]] = []
        self.config = _Cfg()

    async def stream(self, messages, tools=None, system=None, **kw):
        self.captured.append(list(system.volatile) if system else [])
        for _ in range(self._rounds):
            yield Delta(text="最终回复")
        yield Delta(done=True)


def _mk(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".mewcode").mkdir(exist_ok=True, parents=True)
    (ws / ".mewcode/general.md").write_text("回复务必附验收清单", encoding="utf-8")
    store = NoteStore(ws, user_dir=tmp_path / "user", proj_dir=tmp_path / "proj")
    store.append("preferences", ["喜欢中文回复"])
    inj = MemoryInjector(ws, store=store)
    provider = _FakeProvider()
    agent = Agent(provider=provider, registry=ToolRegistry(), workspace=ws,
                  memory_injector=inj.build)
    return agent, provider, store, ws


@pytest.mark.asyncio
async def test_agent_injects_memory_blocks_per_round(tmp_path):
    agent, provider, _, _ = _mk(tmp_path)
    async for _ in agent.run([{"role": "user", "content": "介绍一下"}]):
        pass
    assert len(provider.captured) >= 1
    first = provider.captured[0]
    # volatile 含指令块与索引块,且索引块在尾部
    joined = "\n".join(first)
    assert "验收清单" in joined
    assert "中文回复" in joined


@pytest.mark.asyncio
async def test_chat_done_triggers_auto_memory_snapshot(tmp_path):
    agent, _, _, ws = _mk(tmp_path)
    fired = []
    cm = ChatManager(agent, auto_memory=lambda snap: fired.append(list(snap)))
    async for _ in cm.send_message("hi"):
        pass
    assert len(fired) == 1
    assert fired[0][0]["role"] == "user"  # 快照自 user 消息起,不含后续增量


@pytest.mark.asyncio
async def test_agent_without_injector_behaves_same(tmp_path):
    # 缺省 memory_injector=None → volatile 无记忆块,行为等价既有
    ws = tmp_path / "ws"
    ws.mkdir()
    provider = _FakeProvider()
    agent = Agent(provider=provider, registry=ToolRegistry(), workspace=ws)
    async for _ in agent.run([{"role": "user", "content": "x"}]):
        pass
    joined = "\n".join(provider.captured[0])
    assert "验收清单" not in joined and "中文回复" not in joined