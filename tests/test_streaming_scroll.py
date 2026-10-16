"""流式回答可滚动回归(state16 方案A) —— 生成期间文本已进可滚动 #chat。

回归守护:流式 d.text 按整行刷入 RichLog(而非结束后一次性写入),因此
生成中即可上滚(RichLog 手动上滚会暂停 auto-follow)。#stream 只作占位。
"""
import asyncio
from collections.abc import AsyncIterator

from textual.widgets import RichLog

from codepilot.agent import Agent
from codepilot.config import ProviderConfig
from codepilot.protocols import Delta, ToolCall, ToolResult
from codepilot.providers import BaseProvider
from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry
from codepilot.ui.app import CodePilotApp
from codepilot.ui.widgets import ChatLog


class _ReadTool(Tool):
    @property
    def name(self): return "read"
    @property
    def description(self): return "r"
    @property
    def parameters(self): return {"type": "object", "properties": {}}
    @property
    def side_effect(self): return False
    async def execute(self, **kwargs):
        return ToolResult(call_id="", name="read", output="文件内容")


class _StreamProvider(BaseProvider):
    """逐片流式输出(每片一行,带小间隔),最后 done。"""

    def __init__(self, parts: list[str]):
        self.config = ProviderConfig(name="m", protocol="anthropic", model="m",
                                     api_key="k", base_url="http://m")
        self._parts = parts

    async def stream(self, messages, tools=None, system=None) -> AsyncIterator[Delta]:
        for p in self._parts:
            await asyncio.sleep(0.02)
            yield Delta(text=p)
        yield Delta(done=True)

    async def complete(self, system, messages) -> str:
        return "summary"


async def test_streaming_text_in_chat_mid_generation():
    parts = [f"第{i}行 流式内容\n" for i in range(120)]
    agent = Agent(_StreamProvider(parts), ToolRegistry(), workspace=None,
                  stall_enabled=False)
    app = CodePilotApp(agent)
    async with app.run_test(size=(120, 40)) as pilot:
        inp = app.query_one("#prompt")
        inp.text = "写一段长回答"
        await pilot.press("enter")

        chat = app.query_one("#chat", RichLog)
        # 轮询:直到「仍在生成」且行数已溢出视口(32行)→ 此时必然可滚动
        for _ in range(200):
            await pilot.pause(0.02)
            if app._streaming and len(chat.lines) >= 45:
                break
        assert app._streaming, "应在生成中观察到流式写入"
        assert len(chat.lines) >= 45, "生成期间文本应已进入 #chat 且溢出视口"
        assert chat.max_scroll_y > 0, "内容溢出 → 可滚动"

        # 生成期间可上滚
        chat.scroll_up(animate=False)
        await pilot.pause(0.03)
        assert 0 < chat.scroll_y < chat.max_scroll_y, "生成期间应能向上滚动"

        # 等生成结束,全部行都应在 chat(而非瞬时面板)
        for _ in range(200):
            await pilot.pause(0.05)
            if not app._streaming:
                break
        assert not app._streaming
        assert len(chat.lines) >= 120

        # #stream 只是占位(内容行数远小于回答)
        stream = app.query_one("#stream")
        assert stream.size.height <= 2


class _ToolProvider(BaseProvider):
    """按轮脚本:轮1 文本+调用 read;轮2 文本(无工具 → agent done)。"""

    def __init__(self):
        self.config = ProviderConfig(name="m", protocol="anthropic", model="m",
                                     api_key="k", base_url="http://m")
        self._rounds = [
            [Delta(text="先读一下文件。\n"),
             Delta(tool_calls=[ToolCall(id="1", name="read",
                                        arguments={"file_path": "a.py"})])],
            [Delta(text="继续写。\n")],
        ]

    async def stream(self, messages, tools=None, system=None) -> AsyncIterator[Delta]:
        for d in self._rounds.pop(0):
            await asyncio.sleep(0.01)
            yield d

    async def complete(self, system, messages) -> str:
        return "summary"


async def test_tool_call_renders_one_line_in_chat():
    """方案A:每工具一行(结果内嵌 ✅)直接进可滚动 #chat,顺序在文本之间。"""
    reg = ToolRegistry()
    reg.register(_ReadTool())
    app = CodePilotApp(Agent(_ToolProvider(), reg, workspace=None,
                             stall_enabled=False))
    async with app.run_test(size=(120, 40)) as pilot:
        inp = app.query_one("#prompt")
        inp.text = "读文件然后继续"
        await pilot.press("enter")
        for _ in range(120):
            await pilot.pause(0.05)
            if not app._streaming:
                break
        assert not app._streaming
        texts = [getattr(s, "text", "") for s in app.query_one("#chat", RichLog).lines]
        joined = "\n".join(texts)
        # 工具一行含 ✅ 结果,位于「先读」与「继续写」之间
        assert "先读一下文件" in joined
        assert "继续写" in joined
        assert joined.index("先读一下文件") < joined.index("read") < joined.index("继续写")
        assert "✅" in joined and "文件内容" in joined
        tool_lines = [t for t in texts if "read" in t]
        assert len(tool_lines) == 1, "每工具只占一行"


async def test_scroll_position_survives_writes():
    """核心修复:上滚后继续流式写入,视图不被拽回底部(ChatLog)。"""
    parts = [f"第{i}行 流式内容\n" for i in range(120)]
    agent = Agent(_StreamProvider(parts), ToolRegistry(), workspace=None,
                  stall_enabled=False)
    app = CodePilotApp(agent)
    async with app.run_test(size=(120, 40)) as pilot:
        inp = app.query_one("#prompt")
        inp.text = "写长回答"
        await pilot.press("enter")
        chat = app.query_one("#chat", ChatLog)
        for _ in range(200):
            await pilot.pause(0.02)
            if app._streaming and len(chat.lines) >= 50:
                break
        assert app._streaming
        chat.scroll_up(animate=False)
        await pilot.pause(0.03)
        pos = chat.scroll_y
        assert pos < chat.max_scroll_y, "上滚后应离开底部"
        # 继续流式写入 → 位置必须保持(不因新行 write 被拽回底部)
        for _ in range(30):
            await pilot.pause(0.02)
        assert chat.scroll_y == pos, (
            f"上滚后写入不应拽回底部 (before={pos}, now={chat.scroll_y}, max={chat.max_scroll_y})")
        # 回到底部后,重新跟随
        chat.scroll_end(animate=False)
        await pilot.pause(0.03)
        assert chat.scroll_y >= chat.max_scroll_y - 1
        # 等生成完全结束再退出(避免 worker 取消的 CancelledError 噪音)
        for _ in range(120):
            await pilot.pause(0.05)
            if not app._streaming:
                break


async def test_streaming_chunk_without_newline_buffered():
    """无换行符的残片不刷行,结束时收尾为一行(避免碎成多行)。"""
    parts = ["你好", "世界", "。", "\n", "第二行\n"]  # 前 4 片拼成一行
    agent = Agent(_StreamProvider(parts), ToolRegistry(), workspace=None,
                  stall_enabled=False)
    app = CodePilotApp(agent)
    async with app.run_test(size=(120, 40)) as pilot:
        inp = app.query_one("#prompt")
        inp.text = "hi"
        await pilot.press("enter")
        chat = app.query_one("#chat", RichLog)
        for _ in range(120):
            await pilot.pause(0.05)
            if not app._streaming:
                break
        assert not app._streaming
        texts = [getattr(s, "text", "") for s in chat.lines]
        joined = "\n".join(texts)
        assert "你好世界。" in joined  # 残片正确拼接为一行
