"""会话持久化 store 单测。"""
import json

import pytest

from codepilot.chat import ChatManager, Message
from codepilot.session import store


def test_session_dir_is_stable_and_unique(tmp_path):
    a1 = store.session_dir(str(tmp_path / "proj"))
    a2 = store.session_dir(str(tmp_path / "proj"))
    b = store.session_dir(str(tmp_path / "other"))
    assert a1 == a2
    assert a1 != b
    # 稳定: 不同 cwd → 不同 sha1
    assert a1.parent == store.HOME / "projects"


def test_new_session_and_list_after_write(tmp_path):
    assert store.list_sessions(tmp_path) == []  # 未写入前无任何会话
    p = store.new_session_path(tmp_path, ts=123)
    assert p.parent.exists()  # 目录已建,文件待首个 append
    assert not p.exists()     # 空会话不落占位文件
    store.append_message(p, role="user", content="hi")
    assert p.exists()
    assert p in store.list_sessions(tmp_path)


def test_append_and_load_roundtrip(tmp_path):
    p = store.new_session_path(tmp_path, ts=100)
    store.append_meta(p, provider="D", model="m", cwd=str(tmp_path))
    store.append_message(p, role="user", content="你好")
    store.append_message(p, role="assistant", content="嗨", duration=1.2)
    store.append_mode(p, {"tool_axis": "plan", "release": "auto", "accept_edits": True})

    loaded = store.load_session(p)
    msgs = loaded["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "嗨" and msgs[1]["duration"] == 1.2
    assert loaded["mode"] == {"tool_axis": "plan", "release": "auto",
                              "accept_edits": True}
    assert loaded["meta"]["provider"] == "D"


def test_load_missing_returns_empty(tmp_path):
    loaded = store.load_session(tmp_path / "nope.jsonl")
    assert loaded["messages"] == [] and loaded["mode"] is None


def test_load_ignores_bad_lines(tmp_path):
    p = store.new_session_path(tmp_path, ts=1)
    with p.open("a", encoding="utf-8") as f:
        f.write("not json\n")
        f.write(json.dumps({"type": "message", "role": "user", "content": "x"}) + "\n")
    loaded = store.load_session(p)
    assert len(loaded["messages"]) == 1
    assert loaded["messages"][0]["content"] == "x"


class _StubAgent:
    def __init__(self):
        self.calls = []

    def set_plan_mode(self, on):
        self.calls.append(("plan", on))


def test_chatmanager_records_to_session_file(tmp_path):
    p = store.new_session_path(tmp_path, ts=1)
    cm = ChatManager(_StubAgent(), session_file=str(p))
    cm.history.append(None)  # 占位确认注入不影响
    cm = ChatManager(_StubAgent(), history=[Message("user", "prev")], session_file=str(p))
    assert len(cm.history) == 1
    cm._record("user", "rec")
    loaded = store.load_session(p)
    assert loaded["messages"][-1]["content"] == "rec"