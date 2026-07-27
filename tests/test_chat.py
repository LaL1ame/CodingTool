"""对话管理层测试 —— 上下文组装 + 消息生命周期。"""

import pytest

from mewcode.chat.manager import SYSTEM_PROMPT, ChatManager
from mewcode.chat.models import Message
from mewcode.config import ProviderConfig
from mewcode.protocols.base import Delta
from mewcode.providers.base import BaseProvider


# ---- Mock Provider ---------------------------------------------------------

class _MockProvider(BaseProvider):
    """返回预设 Delta 序列的 mock provider。"""

    def __init__(self, deltas: list[Delta]):
        super().__init__(
            ProviderConfig("Mock", "mock", "mock", "sk", "http://x", False)
        )
        self._deltas = deltas

    async def stream(self, messages):
        for d in self._deltas:
            yield d


# ---- Message 数据类 --------------------------------------------------------

def test_message_creation() -> None:
    m = Message(role="user", content="hello")
    assert m.role == "user"
    assert m.content == "hello"
    assert m.timestamp > 0
    assert m.duration is None


def test_assistant_message_with_duration() -> None:
    m = Message(role="assistant", content="ok", duration=3.5)
    assert m.role == "assistant"
    assert m.duration == 3.5


# ---- ChatManager -----------------------------------------------------------

def test_add_user_message() -> None:
    cm = ChatManager(_MockProvider([]))
    assert len(cm.history) == 0
    cm.add_user_message("hello")
    assert len(cm.history) == 1
    assert cm.history[0].role == "user"
    assert cm.history[0].content == "hello"


def test_add_assistant_message() -> None:
    cm = ChatManager(_MockProvider([]))
    cm.add_assistant_message("reply", 2.0)
    assert len(cm.history) == 1
    assert cm.history[0].role == "assistant"
    assert cm.history[0].duration == 2.0


def test_build_context() -> None:
    cm = ChatManager(_MockProvider([]))
    cm.add_user_message("Q1")
    cm.add_assistant_message("A1", 2.0)
    cm.add_user_message("Q2")

    ctx = cm.build_context()
    assert len(ctx) == 4  # system + Q1 + A1 + Q2
    assert ctx[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert ctx[1] == {"role": "user", "content": "Q1"}
    assert ctx[2] == {"role": "assistant", "content": "A1"}
    assert ctx[3] == {"role": "user", "content": "Q2"}


@pytest.mark.asyncio
async def test_send_message_success() -> None:
    provider = _MockProvider(
        [
            Delta(text="Hello "),
            Delta(text="World"),
            Delta(done=True),
        ]
    )
    cm = ChatManager(provider)
    deltas = [d async for d in cm.send_message("hi")]

    # 应该有 text deltas + done
    texts = "".join(d.text for d in deltas if d.text)
    assert texts == "Hello World"
    assert deltas[-1].done

    # 历史应该包含 user + assistant
    assert len(cm.history) == 2
    assert cm.history[0].role == "user"
    assert cm.history[0].content == "hi"
    assert cm.history[1].role == "assistant"
    assert cm.history[1].content == "Hello World"
    assert cm.history[1].duration is not None


@pytest.mark.asyncio
async def test_send_message_error() -> None:
    provider = _MockProvider([Delta(error="API Error")])
    cm = ChatManager(provider)
    deltas = [d async for d in cm.send_message("hi")]

    assert len(deltas) == 1
    assert deltas[0].error == "API Error"
    # 错误时不应该追加 assistant 消息
    assert len(cm.history) == 1  # only user message


@pytest.mark.asyncio
async def test_send_message_filters_thinking() -> None:
    """thinking delta 不会传递到 TUI。"""
    provider = _MockProvider(
        [
            Delta(thinking="Hmm..."),
            Delta(text="Answer"),
            Delta(done=True),
        ]
    )
    cm = ChatManager(provider)
    deltas = [d async for d in cm.send_message("hi")]

    # thinking 应该被过滤
    thinking_deltas = [d for d in deltas if d.thinking]
    assert len(thinking_deltas) == 0

    text_deltas = [d for d in deltas if d.text]
    assert len(text_deltas) == 1
    assert text_deltas[0].text == "Answer"


@pytest.mark.asyncio
async def test_send_message_multiround_context() -> None:
    """多轮对话：上下文包含所有历史。"""
    provider = _MockProvider(
        [Delta(text="Second answer"), Delta(done=True)]
    )

    cm = ChatManager(provider)
    cm.add_user_message("Q1")
    cm.add_assistant_message("A1", 1.0)

    # 现在 send_message 应该包含 Q1, A1 在 context 中
    async for _ in cm.send_message("Q2"):
        pass

    assert len(cm.history) == 4  # Q1, A1, Q2, A2
