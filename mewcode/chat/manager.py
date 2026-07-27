"""对话管理器：维护消息历史、组装上下文、委托 Provider 请求。"""

import time
from collections.abc import AsyncIterator

from mewcode.chat.models import Message
from mewcode.protocols.base import Delta
from mewcode.providers.base import BaseProvider

SYSTEM_PROMPT = """\
You are MewCode, a CLI AI coding assistant running in the terminal.

You help users with programming tasks, answer technical questions, \
and provide code examples. You are concise but thorough.

Current context: you are in a terminal-based chat interface. \
The user is a developer working on a coding project.
"""


class ChatManager:
    """维护单次会话的对话历史与上下文。"""

    def __init__(self, provider: BaseProvider) -> None:
        self.provider = provider
        self.history: list[Message] = []

    def add_user_message(self, content: str) -> Message:
        msg = Message(role="user", content=content)
        self.history.append(msg)
        return msg

    def add_assistant_message(self, content: str, duration: float) -> Message:
        msg = Message(role="assistant", content=content, duration=duration)
        self.history.append(msg)
        return msg

    def build_context(self) -> list[dict]:
        """组装完整上下文：system prompt + 历史消息。"""
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        for m in self.history:
            messages.append({"role": m.role, "content": m.content})
        return messages

    async def send_message(self, content: str) -> AsyncIterator[Delta]:
        """接收用户输入，委托 provider 发起流式请求。

        产出 Delta 流给 TUI 层消费：
        - text delta → 实时文本增量
        - done delta → 本轮结束（历史已自动追加）
        - error delta → 错误信息（不追加历史）

        thinking delta 在此层过滤，不向上传递。
        """
        self.add_user_message(content)
        context = self.build_context()

        full_text: list[str] = []
        started_at = time.time()
        received_done = False

        try:
            async for delta in self.provider.stream(context):
                if delta.error:
                    yield delta
                    return

                if delta.thinking:
                    continue  # 思考内容在协议层产出，此层丢弃

                if delta.text:
                    full_text.append(delta.text)
                    yield delta

                if delta.done:
                    elapsed = time.time() - started_at
                    self.add_assistant_message("".join(full_text), elapsed)
                    received_done = True
                    yield delta
                    return

        except Exception as e:
            yield Delta(error=str(e))
            return

        # 如果协议层没有显式 done 信号但流已耗尽
        if not received_done and full_text:
            elapsed = time.time() - started_at
            self.add_assistant_message("".join(full_text), elapsed)
            yield Delta(done=True)
