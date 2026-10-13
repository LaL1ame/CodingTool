"""state13 F4 单元测试 —— 自动记忆后台编排。"""
import asyncio

import pytest

from codepilot.memory.auto import _parse_notes, run_once, schedule
from codepilot.memory.store import NoteStore


class FakeProvider:
    def __init__(self, text: str = "", error: str | None = None) -> None:
        self._text = text
        self._error = error
        self.called = 0

    async def complete(self, system, messages) -> str:
        self.called += 1
        if self._error:
            raise RuntimeError(self._error)
        return self._text


def _store(tmp_path) -> NoteStore:
    return NoteStore(tmp_path / "ws", user_dir=tmp_path / "user",
                     proj_dir=tmp_path / "proj")


_TRANSCRIPT = [
    {"role": "user", "content": "帮我加个入口"},
    {"role": "assistant", "content": "好的,我已确认 CLI 入口在 main.py"},
    {"role": "assistant", "content": None,
     "tool_calls": [{"id": "t1", "name": "read", "arguments": {"path": "main.py"}}]},
    {"role": "tool", "tool_call_id": "t1", "content": "def main(): pass"},
    {"role": "assistant", "content": "入口已加好"},
]


# —— _parse_notes 规则切分 ——

def test_parse_notes_categories():
    text = "偏好: 喜欢中文回复\n纠正: 别用缩写\n项目知识: 入口在 main.py\n参考: https://x.dev\n杂行\n参考: https://y.dev"
    notes = _parse_notes(text)
    assert "喜欢中文回复" in notes["preferences"]
    assert "别用缩写" in notes["corrections"]
    assert "入口在 main.py" in notes["project_knowledge"]
    assert "https://x.dev" in notes["references"]
    assert "https://y.dev" in notes["references"]
    # 无法识别类别(杂行)丢弃,不污染任何类
    assert all("杂行" not in v for v in notes.values())


def test_parse_notes_empty_and_garbage():
    assert _parse_notes("") == {c: [] for c in
                                ("preferences", "corrections",
                                 "project_knowledge", "references")}
    assert _parse_notes("完全无法识别\n也识别不了") == {c: [] for c in
                                      ("preferences", "corrections",
                                       "project_knowledge", "references")}


# —— run_once 编排 ——

@pytest.mark.asyncio
async def test_run_once_parses_and_appends(tmp_path):
    store = _store(tmp_path)
    p = FakeProvider("偏好: 喜欢中文回复\n项目知识: 入口在 main.py\n参考: https://x.dev")
    n = await run_once(p, store, _TRANSCRIPT)
    assert n == 3
    assert p.called == 1
    assert "喜欢中文回复" in store.load_notes()["preferences"]
    assert "入口在 main.py" in store.load_notes()["project_knowledge"]


@pytest.mark.asyncio
async def test_run_once_empty_output_nothing_written(tmp_path):
    store = _store(tmp_path)
    n = await run_once(FakeProvider(""), store, _TRANSCRIPT)
    assert n == 0
    assert store.to_index() == ""


@pytest.mark.asyncio
async def test_run_once_provider_error_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(RuntimeError):
        await run_once(FakeProvider(error="boom"), store, _TRANSCRIPT)


# —— schedule fire-and-forget ——

@pytest.mark.asyncio
async def test_schedule_swallows_error(tmp_path):
    store = _store(tmp_path)
    p = FakeProvider(error="boom")
    task = schedule(p, store, _TRANSCRIPT)
    assert task is not None
    await task  # 等待后台跑完;异常应被 _safe_run 吞掉,不抛
    assert store.to_index() == ""
    # 即便失败也调用过一次
    assert p.called == 1


@pytest.mark.asyncio
async def test_schedule_writes_notes(tmp_path):
    store = _store(tmp_path)
    p = FakeProvider("偏好: 用 UTF-8")
    task = schedule(p, store, _TRANSCRIPT)
    assert task is not None
    await task
    assert "用 UTF-8" in store.load_notes()["preferences"]