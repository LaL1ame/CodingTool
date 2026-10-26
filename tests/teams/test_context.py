"""A2 成员上下文：协议原生 msgs ↔ 会话 JSONL 往返。"""

from pathlib import Path

from codepilot.teams.context import (
    load_member_msgs,
    member_context_path,
    save_member_msgs,
)
from codepilot.teams.storage import TeamStorage


def _anthropic_msgs():
    return [
        {"role": "user", "content": "开始任务"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "我先读文件"},
            {"type": "tool_use", "id": "call_1", "name": "read",
             "input": {"path": "a.py"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "内容"},
        ]},
        {"role": "assistant", "content": "任务完成"},
    ]


def _openai_msgs():
    return [
        {"role": "user", "content": "开始任务"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read",
                                      "arguments": '{"path": "a.py"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "内容"},
        {"role": "assistant", "content": "任务完成"},
    ]


def test_member_context_path(storage: TeamStorage, team: str):
    p = member_context_path(storage, team, "alice")
    assert p.name == "alice.jsonl"
    assert p.parent.name == "context"


def test_roundtrip_anthropic(storage: TeamStorage, team: str):
    msgs = _anthropic_msgs()
    save_member_msgs(storage, team, "alice", msgs, protocol="anthropic")
    restored = load_member_msgs(storage, team, "alice", protocol="anthropic")
    assert restored is not None
    assert len(restored) == len(msgs)
    # 逐条比对核心字段
    assert restored[0] == msgs[0]
    assert restored[1]["role"] == "assistant"
    assert restored[2]["role"] == "user"
    assert restored[3] == msgs[3]


def test_roundtrip_openai(storage: TeamStorage, team: str):
    msgs = _openai_msgs()
    save_member_msgs(storage, team, "bob", msgs, protocol="openai")
    restored = load_member_msgs(storage, team, "bob", protocol="openai")
    assert restored is not None
    assert len(restored) == len(msgs)
    assert restored[0] == msgs[0]
    assert restored[1]["tool_calls"][0]["function"]["name"] == "read"
    assert restored[2]["role"] == "tool"
    assert restored[3] == msgs[3]


def test_incremental_save_is_idempotent(storage: TeamStorage, team: str):
    msgs = _anthropic_msgs()
    # 全量保存
    n1 = save_member_msgs(storage, team, "alice", msgs, protocol="anthropic")
    assert n1 == len(msgs)
    # 用游标再次增量保存（不重复写）
    n2 = save_member_msgs(
        storage, team, "alice", msgs, persisted_len=n1, protocol="anthropic")
    assert n2 == len(msgs)
    restored = load_member_msgs(storage, team, "alice", protocol="anthropic")
    assert len(restored) == len(msgs), "增量保存不应重复写"


def test_append_more_then_restore(storage: TeamStorage, team: str):
    """先保存前两条，再追加后续，恢复得到完整序列。"""
    msgs = _anthropic_msgs()
    n = save_member_msgs(
        storage, team, "alice", msgs[:2], protocol="anthropic")
    save_member_msgs(
        storage, team, "alice", msgs, persisted_len=n, protocol="anthropic")
    restored = load_member_msgs(storage, team, "alice", protocol="anthropic")
    assert len(restored) == len(msgs)


def test_no_file_returns_none(storage: TeamStorage, team: str):
    assert load_member_msgs(storage, team, "alice", protocol="anthropic") is None


def test_corrupt_file_returns_none(storage: TeamStorage, team: str, tmp_path: Path):
    p = storage.member_context_path(team, "alice")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not-json\n", encoding="utf-8")
    assert load_member_msgs(storage, team, "alice", protocol="anthropic") is None
