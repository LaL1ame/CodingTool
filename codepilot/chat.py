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
                 session_file: str | Path | None = None,
                 msgs: list[dict] | None = None) -> None:
        self.agent = agent
        self.history: list[Message] = list(history) if history else []
        self.session_file = Path(session_file) if session_file else None
        # F0:持久化的完整 LLM 消息序列（协议原生 dict）。
        # agent.run 原地向上追加助手消息与工具结果；跨 send_message 调用保持同一
        # 对象 → 工具结果跨轮存活,且 tool_use/tool_result 的 id 天然配对。
        # state12:msgs 非空时以之为序(restore 回灌完整序列含工具调用);否则从文本
        # history 重建纯 user/assistant 序列。
        if msgs is not None:
            self._msgs: list[dict] = list(msgs)
        else:
            self._msgs: list[dict] = [
                {"role": m.role, "content": m.content} for m in self.history
                if m.role in ("user", "assistant")
            ]

    def _record(self, role: str, content: str, duration: float | None = None) -> None:
        """若配置了会话文件,把一条消息追加为 JSONL 行(resume 用)。"""
        if self.session_file is None:
            return
        from codepilot.session import store
        store.append_message(self.session_file, role=role, content=content,
                             duration=duration)

    def _record_assistant_toolcalls(self, content: str, tool_calls: list) -> None:
        """若配置了会话文件,把带工具调用的一轮助手消息落盘(state12)。"""
        if self.session_file is None or not tool_calls:
            return
        plain = [{"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                 for tc in tool_calls]
        from codepilot.session import store
        store.append_assistant_toolcalls(self.session_file, content=content,
                                         tool_calls=plain)

    def _record_tool_result(self, result) -> None:
        """若配置了会话文件,把一条工具执行结果落盘(state12)。"""
        if self.session_file is None or result is None:
            return
        from codepilot.session import store
        store.append_tool_result(self.session_file, call_id=result.call_id,
                                 name=result.name, output=result.output,
                                 error=result.error)

    def build_context(self) -> list[dict]:
        """返回持久化的完整 LLM 消息序列（F0）。

        含 agent.run 跨轮原地追加的助手消息与工具结果。不含 system（system 由
        agent 每轮按协议串行化注入）。返回同一 _msgs 对象 → run 原地追加即持久。
        """
        return self._msgs

    async def send_message(self, content: str, *, resume: bool = False) -> AsyncIterator[Delta]:
        """发送一条 user 消息并驱动 agent 循环。

        resume=True(state10 F2):续接上一轮达上限而暂停的循环,沿用同一持久
        `_msgs`,不追加新 user 消息,直接续跑。用于 /continue。
        """
        if not resume:
            self.history.append(Message(role="user", content=content))
            self._record("user", content)
            self._msgs.append({"role": "user", "content": content})
        ctx = self.build_context()
        full: list[str] = []
        round_text: list[str] = []     # 当前轮已收文本(state12:用于补写 assistant 行)
        pending_calls = None           # 本轮待落盘的 tool_calls(首个 tool_result 时 flush)
        pending_assistant = ""         # 该批 tool_calls 对应的本轮助手文本
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
                if d.tool_calls is not None:
                    # 记录该批工具调用与其本轮助手文本,待首个结果落盘时 flush
                    pending_calls = d.tool_calls
                    pending_assistant = "".join(round_text)
                    yield d
                    continue
                if d.tool_result is not None:
                    if pending_calls is not None:
                        # 该批首个结果:先补写 assistant(带 tool_calls)行,再重置本轮文本
                        self._record_assistant_toolcalls(pending_assistant, pending_calls)
                        pending_calls = None
                        round_text = []
                    self._record_tool_result(d.tool_result)
                    yield d
                    continue
                if d.text:
                    full.append(d.text)
                    round_text.append(d.text)
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
