"""层2 摘要单测(F3/F4/F5)。"""

import asyncio

from codepilot.config import ProviderConfig
from codepilot.context.compactor import Compactor, format_recent_for_summary


class _FakeSummarizer:
    """记录 prompt,返回固定摘要。"""
    def __init__(self):
        self.calls = []
        self.config = ProviderConfig(name="s", protocol="anthropic", model="m",
                                     api_key="k", base_url="http://m")

    async def complete(self, system, messages):
        self.calls.append((system, messages))
        return "**正式摘要**\n## 背景与目标\n..." if "背景" in system else ""


# —— F4: prompt 约束 ——

def test_prompt_forbids_tools():
    p = Compactor.build_summary_prompt("ov", [{"role": "user", "content": "hi"}])
    assert "严禁调用任何工具" in p or "不得调用" in p or "禁" in p
    assert "分析草稿" in p and "正式摘要" in p
    # 固定部分组织
    for sec in ("背景与目标", "关键决策", "未完成", "待办"):
        assert sec in p


def test_prompt_embed_recent():
    recent = [{"role": "user", "content": "读下 a.py"}, {"role": "assistant", "content": "好的"}]
    p = Compactor.build_summary_prompt("", recent)
    assert "读下 a.py" in p
    assert "好的" in p


def test_format_recent_handles_content_blocks():
    recent = [{"role": "user", "content": [
        {"type": "tool_result", "content": "FILE_CONTENT"},
        {"type": "text", "text": "看到内容了"},
    ]}]
    s = format_recent_for_summary(recent)
    assert "FILE_CONTENT" in s
    assert "看到内容了" in s


# —— F4.2/F4.3: 草稿不入历史 / 独立调用 ——

def test_compact_calls_complete_with_empty_messages():
    fake = _FakeSummarizer()
    c = Compactor()
    res = asyncio.run(c.compact(
        provider=fake, model="m", overview="o", recent=[{"role": "user", "content": "x"}]))
    assert fake.calls
    system, messages = fake.calls[0]
    # 草稿埋在 prompt 里,独立调用不从主对话取 messages
    assert messages == []
    assert res.summary == "**正式摘要**\n## 背景与目标\n..."


# —— F5: 边界消息 ——

def test_boundary_message_prompts_reread():
    msg = Compactor.build_boundary_message()
    assert "重新读取" in msg
    assert "摘要" in msg and "脑补" in msg


# —— 失败传播 ——

def test_compact_propagates_failure():
    class _Boom:
        async def complete(self, system, messages):
            raise RuntimeError("provider down")

    c = Compactor()
    try:
        asyncio.run(c.compact(provider=_Boom(), model="m", overview="o", recent=[]))
        assert False, "should raise"
    except RuntimeError:
        pass