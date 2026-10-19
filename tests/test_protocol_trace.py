"""protocol-trace 回归：回灌不破坏 tool_result 配对；请求前清洗 dangling tool_use。"""

from collections.abc import AsyncIterator

from codepilot.agent import Agent
from codepilot.config import ProviderConfig
from codepilot.protocols import Delta
from codepilot.providers import BaseProvider
from codepilot.tools.registry import ToolRegistry
from codepilot.ui.app import _append_context_notice


class _Provider(BaseProvider):
    def __init__(self, protocol: str = "anthropic"):
        self.config = ProviderConfig(
            name="m", protocol=protocol, model="m",
            api_key="k", base_url="http://m")
        self.last_messages: list[dict] | None = None

    async def stream(self, messages, tools=None, system=None) -> AsyncIterator[Delta]:
        # 记录 sanitize 之后实际传给 provider 的历史（浅拷贝：旧条目 dict 不被 run 改动）。
        self.last_messages = list(messages)
        # 不产生任何工具调用，直接完成。
        yield Delta(done=True)

    async def complete(self, system, messages) -> str:
        return "summary"


def _pending_unpaired(messages) -> bool:
    """历史里是否有 tool_use/tool_call 没有任何配对 tool_result。

    两遍扫描：先收集全部结果 id，再检查每个 tool_use 是否被覆盖。
    """
    resolved: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            for b in (msg.get("content") or []):
                if isinstance(b, dict) and b.get("type") == "tool_result" \
                        and b.get("tool_use_id"):
                    resolved.add(b["tool_use_id"])
        elif role == "tool":
            if msg.get("tool_call_id"):
                resolved.add(msg["tool_call_id"])
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use" \
                        and b.get("id") not in resolved:
                    return True
        else:
            for c in (msg.get("tool_calls") or []):
                if isinstance(c, dict) and c.get("id") not in resolved:
                    return True
    return False


# —— R1：回灌不插进 tool_result ——

def test_append_notice_to_tool_result_message_creates_new_user():
    """末条 user 的 content 是含 tool_result 的列表时，通知必须另起新消息。"""
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_a", "name": "read",
             "input": {"path": "x"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_a", "content": "ok"}]},
    ]
    _append_context_notice(msgs, "【团队事件】...")
    # 不破坏原 tool_result 消息：末条是独立的新 user 文本
    assert msgs[-2] is msgs[2]
    assert msgs[-1] == {"role": "user", "content": "【团队事件】..."}
    assert msgs[2]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][0]["tool_use_id"] == "call_a"


def test_append_notice_to_tool_role_message_creates_new_user():
    """openai 协议末条 role=tool 时，通知必须另起新 user 消息。"""
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    _append_context_notice(msgs, "通知")
    assert msgs[-1] == {"role": "user", "content": "通知"}
    assert msgs[2]["role"] == "tool"


def test_append_notice_merges_into_plain_user():
    msgs = [{"role": "user", "content": "hello"}]
    _append_context_notice(msgs, "world")
    assert msgs[0]["content"] == "hello\n\nworld"
    assert len(msgs) == 1


def test_append_notice_to_tool_result_list_appends_text_not_front():
    """可合并的列表 user（纯文本块）append 到末尾，不打断任何块。"""
    msgs = [{"role": "user", "content": [{"type": "text", "text": "a"}]}]
    _append_context_notice(msgs, "b")
    assert msgs[0]["content"][-1] == {"type": "text", "text": "b"}


# —— R2/R3：sanitizer 补齐 dangling tool_use ——

async def _run_dangling(msgs, protocol) -> _Provider:
    provider = _Provider(protocol)
    agent = Agent(provider, ToolRegistry(), workspace=None, stall_enabled=False)
    async for _d in agent.run(msgs):
        pass
    return provider


async def test_sanitize_anthropic_dangling_tool_use():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_x", "name": "read",
             "input": {"path": "p"}}]},
    ]
    provider = await _run_dangling(msgs, "anthropic")
    assert not _pending_unpaired(provider.last_messages)
    direction = provider.last_messages[-1]
    assert direction["role"] == "user"
    block = direction["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "call_x"
    assert block["is_error"] is True
    assert "已取消" in block["content"]


async def test_sanitize_openai_dangling_tool_calls():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c9", "type": "function",
                         "function": {"name": "x", "arguments": "{}"}}]},
    ]
    provider = await _run_dangling(msgs, "openai")
    assert not _pending_unpaired(provider.last_messages)
    tail = provider.last_messages[-1]
    assert tail["role"] == "tool"
    assert tail["tool_call_id"] == "c9"
    assert "已取消" in tail["content"]


async def test_sanitize_does_not_touch_paired_history():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "ok", "name": "read",
             "input": {"path": "p"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "ok", "content": "r"}]},
    ]
    provider = await _run_dangling(msgs, "anthropic")
    assert not _pending_unpaired(provider.last_messages)
    # 已配对的历史不被改动、也不新增补齐
    assert len(provider.last_messages) == 3
    assert provider.last_messages[-1]["content"][0]["type"] == "tool_result"