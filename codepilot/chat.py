"""对话管理层：Message + ChatManager。"""

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from codepilot.agent import Agent
from codepilot.protocols import Delta


@dataclass
class Message:
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    duration: float | None = None


class ChatManager:
    def __init__(self, agent: Agent, history: list[Message] | None = None,
                 session_file: str | Path | None = None) -> None:
        self.agent = agent
        self.history: list[Message] = list(history) if history else []
        self.session_file = Path(session_file) if session_file else None

    def _record(self, role: str, content: str, duration: float | None = None) -> None:
        """若配置了会话文件,把一条消息追加为 JSONL 行(resume 用)。"""
        if self.session_file is None:
            return
        from codepilot.session import store
        store.append_message(self.session_file, role=role, content=content,
                             duration=duration)

    def build_context(self) -> list[dict]:
        """返回历史消息（不含 system —— system 由 agent 每轮按协议串行化注入）。"""
        return [{"role": m.role, "content": m.content} for m in self.history]

    async def send_message(self, content: str) -> AsyncIterator[Delta]:
        self.history.append(Message(role="user", content=content))
        self._record("user", content)
        ctx = self.build_context()
        full: list[str] = []
        started = time.time()

        try:
            async for d in self.agent.run(ctx):
                if d.error:
                    yield d
                    return
                if d.cancelled:
                    yield d
                    return
                if d.thinking:
                    continue
                if d.usage is not None:
                    yield d
                    continue
                if d.round is not None:
                    yield d
                    continue
                if d.tool_result is not None:
                    yield d
                    continue
                if d.tool_calls is not None:
                    yield d
                    continue
                if d.text:
                    full.append(d.text)
                    yield d
                if d.done:
                    elapsed = time.time() - started
                    self.history.append(Message(role="assistant", content="".join(full), duration=elapsed))
                    self._record("assistant", "".join(full), duration=elapsed)
                    yield d
                    return
        except Exception as e:
            yield Delta(error=str(e))
            return

        if full:
            elapsed = time.time() - started
            self.history.append(Message(role="assistant", content="".join(full), duration=elapsed))
            self._record("assistant", "".join(full), duration=elapsed)
            yield Delta(done=True)
