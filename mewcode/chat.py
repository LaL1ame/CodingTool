"""对话管理层：Message + ChatManager。"""

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from mewcode.agent import Agent
from mewcode.protocols import Delta

SYSTEM_PROMPT = (
    "You are MewCode, a CLI AI coding assistant. "
    "You help with programming tasks, answer technical questions, "
    "and provide code examples. Be concise but thorough."
)


@dataclass
class Message:
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    duration: float | None = None


class ChatManager:
    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.history: list[Message] = []

    def build_context(self) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in self.history:
            msgs.append({"role": m.role, "content": m.content})
        return msgs

    async def send_message(self, content: str) -> AsyncIterator[Delta]:
        self.history.append(Message(role="user", content=content))
        ctx = self.build_context()
        full: list[str] = []
        started = time.time()

        try:
            async for d in self.agent.run(ctx):
                if d.error:
                    yield d
                    return
                if d.thinking:
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
                    yield d
                    return
        except Exception as e:
            yield Delta(error=str(e))
            return

        if full:
            elapsed = time.time() - started
            self.history.append(Message(role="assistant", content="".join(full), duration=elapsed))
            yield Delta(done=True)
