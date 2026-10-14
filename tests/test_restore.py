"""state12 会话恢复(restore)+ store 扩展行为单测。

覆盖:dangling 截断、时间跨度提醒、完整 LLM 序列重建、旧格式兼容、
summarize 无副作用、cleanup_expired 只删过期、会话 ID 防同秒撞车。
"""
import os
import time

from codepilot.session import store, restore


# —— restore: dangling 截断 ——
def _write_tool_round(p, text, calls, results=()):
    store.append_assistant_toolcalls(p, content=text, tool_calls=calls)
    for r in results:
        store.append_tool_result(p, call_id=r["id"], name=r.get("name"),
                                 output=r.get("output"), error=None)


def test_restore_truncates_dangling_tool_round(tmp_path):
    p = store.new_session_path(tmp_path, ts=1700000000)
    store.append_message(p, role="user", content="开始")
    _write_tool_round(p, "读", [{"id": "c1", "name": "read", "arguments": {}}],
                      [{"id": "c1", "name": "read", "output": "A"}])
    _write_tool_round(p, "续", [{"id": "c2", "name": "read", "arguments": {}}])  # 悬空
    store.append_message(p, role="user", content="应被截断")

    r = restore.restore_chat(p, gap_hours=0)
    roles = [m["role"] for m in r["messages"]]
    assert roles == ["user", "assistant", "tool"]
    assert r["messages"][-1]["tool_call_id"] == "c1"
    assert all("c2" not in str(m) for m in r["messages"])


def test_restore_keeps_complete_round_when_all_results_present(tmp_path):
    p = store.new_session_path(tmp_path, ts=1700000000)
    _write_tool_round(p, "", [{"id": "c1", "name": "read", "arguments": {}}],
                      [{"id": "c1", "name": "read", "output": "X"}])
    r = restore.restore_chat(p, gap_hours=0)
    assert [m["role"] for m in r["messages"]] == ["assistant", "tool"]


# —— restore: 时间跨度提醒 ——
def test_restore_gap_reminder_inserted_when_over_threshold(tmp_path):
    p = store.new_session_path(tmp_path, ts=1700000000)
    store.append_message(p, role="user", content="你好")
    r = restore.restore_chat(p, gap_hours=1, now=time.time() + 7200)
    assert r["messages"][0]["role"] == "user"
    assert "距上次会话" in r["messages"][0]["content"]


def test_restore_no_reminder_recent_or_disabled(tmp_path):
    p = store.new_session_path(tmp_path, ts=1700000000)
    store.append_message(p, role="user", content="你好")
    r1 = restore.restore_chat(p, gap_hours=1, now=time.time())
    r2 = restore.restore_chat(p, gap_hours=0, now=time.time() + 7200)
    assert "距上次会话" not in r1["messages"][0]["content"]
    assert "距上次会话" not in r2["messages"][0]["content"]


# —— 旧格式兼容 ——
def test_restore_legacy_file_no_segments(tmp_path):
    """旧 JSONL(仅 meta/message/mode)→ 正常恢复为纯文本序列。"""
    p = store.new_session_path(tmp_path, ts=1690000000)
    store.append_meta(p, provider="D", model="m", cwd=str(tmp_path))
    store.append_message(p, role="user", content="hi")
    store.append_mode(p, {"tool_axis": "plan", "release": "auto", "accept_edits": True})
    r = restore.restore_chat(p, gap_hours=0)
    assert [m["role"] for m in r["messages"]] == ["user"]
    assert r["messages"][0]["content"] == "hi"
    assert r["mode"]["release"] == "auto"


def test_restore_missing_file_empty(tmp_path):
    r = restore.restore_chat(tmp_path / "nope.jsonl", gap_hours=0)
    assert r["messages"] == [] and r["mode"] is None


# —— state15: 按协议重建请求形态(落盘是扁平日志,resume 回灌必须合规) ——
def test_restore_openai_rebuilds_function_wrapper(tmp_path):
    """openai 兼容:扁平 {id,name,arguments} → {id,type:function,function:{…}}。"""
    p = store.new_session_path(tmp_path, ts=1700000000)
    store.append_message(p, role="user", content="查全局")
    store.append_assistant_toolcalls(
        p, content="", tool_calls=[{"id": "c1", "name": "glob",
                                    "arguments": {"pattern": "**/*.py"}}])
    store.append_tool_result(p, call_id="c1", name="glob", output="A.py")

    r = restore.restore_chat(p, protocol="openai", gap_hours=0)
    asst = r["messages"][1]
    assert asst["role"] == "assistant"
    call = asst["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "glob"          # function 包装补上
    assert call["function"]["arguments"] == '{"pattern": "**/*.py"}'  # arguments 成 JSON 串
    assert r["messages"][2]["role"] == "tool"           # openai 结果用 tool 角色
    assert r["messages"][2]["tool_call_id"] == "c1"


def test_restore_anthropic_rebuilds_tool_use_content(tmp_path):
    """anthropic:assistant content 为 text+tool_use 列表,结果用 user+tool_result。"""
    p = store.new_session_path(tmp_path, ts=1700000000)
    store.append_message(p, role="user", content="审")
    store.append_assistant_toolcalls(
        p, content="我先看", tool_calls=[{"id": "tc1", "name": "read",
                                         "arguments": {"file_path": "a.py"}}])
    store.append_tool_result(p, call_id="tc1", name="read", output="X")

    r = restore.restore_chat(p, protocol="anthropic", gap_hours=0)
    asst = r["messages"][1]
    kinds = [c["type"] for c in asst["content"]]
    assert kinds == ["text", "tool_use"]               # content 列表,非 tool_calls 键
    tu = asst["content"][1]
    assert tu["id"] == "tc1" and tu["name"] == "read" and tu["input"] == {"file_path": "a.py"}
    res = r["messages"][2]
    assert res["role"] == "user" and res["content"][0]["type"] == "tool_result"
    assert res["content"][0]["tool_use_id"] == "tc1"


# —— store: 会话 ID 格式 + 排序 ——
def test_new_session_id_format_and_unique_same_second(tmp_path):
    import re
    a = store.new_session_path(tmp_path, ts=1700000000.0)
    b = store.new_session_path(tmp_path, ts=1700000000.0)
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}\.jsonl", a.name)
    assert a.name != b.name  # 防同秒撞车
    assert store.new_session_path(tmp_path, ts=1700000000.0).parent.exists()


def test_list_sessions_sorts_by_name_desc(tmp_path):
    p = store.new_session_path(tmp_path, ts=1700000000)
    store.append_message(p, role="user", content="x")
    p2 = store.new_session_path(tmp_path, ts=1700000001)
    store.append_message(p2, role="user", content="y")
    names = [x.name for x in store.list_sessions(tmp_path)]
    assert names == sorted(names, reverse=True)


# —— store: summarize 无副作用 ——
def test_summarize_no_side_effect_and_fields(tmp_path):
    p = store.new_session_path(tmp_path, ts=1700000000)
    store.append_message(p, role="user", content="第一条消息内容足够长一些")
    _write_tool_round(p, "做", [{"id": "c1", "name": "read", "arguments": {}}])
    store.append_message(p, role="user", content="第二条")
    before = p.read_bytes()
    s = store.summarize(p)
    after = p.read_bytes()
    assert s["id"] == p.name
    assert s["title"] == "第一条消息内容足够长一些"
    assert s["message_count"] == 3  # 2 user + 1 assistant
    assert s["last_ts"] > 0
    assert before == after  # 只读,无副作用


# —— store: cleanup_expired 只删过期、幂等 ——
def test_cleanup_expired_only_removes_old_and_idempotent(tmp_path):
    root = store.session_dir(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    old = store.new_session_path(tmp_path, ts=1600000000)
    old.touch()
    new = store.new_session_path(tmp_path, ts=1700000000)
    new.touch()
    now = time.time()
    os.utime(old, (now - 40 * 86400, now - 40 * 86400))
    os.utime(new, (now - 100, now - 100))

    removed = store.cleanup_expired(root, max_age_days=30, now=now)
    assert {p.name for p in removed} == {old.name}
    assert new.exists()

    # 幂等:二次运行不再删
    assert store.cleanup_expired(root, max_age_days=30, now=now) == []