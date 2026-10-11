"""测试对话管理层 — 新事件透传与取消处理。"""

from codepilot.chat import ChatManager
from codepilot.protocols import Delta, Usage


class _FakeAgent:
    def __init__(self, deltas):
        self._deltas = deltas

    def system_prompt(self):
        return "You are a fake agent."

    async def run(self, messages):
        for d in self._deltas:
            yield d


class TestEventPassthrough:
    async def test_usage_and_round_passthrough(self):
        agent = _FakeAgent([
            Delta(round=1, max_rounds=10),
            Delta(text="Hi"),
            Delta(usage=Usage(input_tokens=5, output_tokens=2)),
            Delta(done=True),
        ])
        cm = ChatManager(agent)
        deltas = [d async for d in cm.send_message("hello")]
        assert any(d.usage and d.usage.input_tokens == 5 for d in deltas)
        assert any(d.round == 1 and d.max_rounds == 10 for d in deltas)
        assert any(d.text == "Hi" for d in deltas)


class TestToolResultPersistenceF0:
    """F0:工具结果跨 send_message 轮持久(进程内)。"""

    class _ToolAgent:
        async def run(self, messages):
            messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_1", "name": "read", "input": {}}]})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1",
                 "content": "LARGE FILE CONTENT 12345"}]})
            yield Delta(round=1, max_rounds=2)
            yield Delta(tool_calls=[ToolCall(id="call_1", name="read", arguments={})])
            yield Delta(tool_result=ToolResult(call_id="call_1", name="read",
                                              output="LARGE FILE CONTENT 12345"))
            yield Delta(done=True)

    async def test_tool_result_survives_across_messages(self):
        cm = ChatManager(self._ToolAgent())
        [d async for d in cm.send_message("look at file")]
        [d async for d in cm.send_message("continue")]
        # _msgs 是同一持久对象
        assert cm._msgs is cm.build_context()
        survived = any(
            isinstance(m.get("content"), list) and any(
                c["type"] == "tool_result" for c in m["content"])
            for m in cm._msgs
        )
        assert survived


class TestCancelHandling:
    async def test_cancel_does_not_write_history(self):
        agent = _FakeAgent([
            Delta(text="partial "),
            Delta(cancelled=True),
        ])
        cm = ChatManager(agent)
        deltas = [d async for d in cm.send_message("do something")]
        assert any(d.cancelled for d in deltas)
        assert len(cm.history) == 1
        assert cm.history[0].role == "user"

    async def test_normal_completion_writes_history(self):
        agent = _FakeAgent([
            Delta(text="full answer"),
            Delta(done=True),
        ])
        cm = ChatManager(agent)
        [d async for d in cm.send_message("hello")]
        assert len(cm.history) == 2
        assert cm.history[1].role == "assistant"
        assert cm.history[1].content == "full answer"
