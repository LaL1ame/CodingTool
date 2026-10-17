"""轨迹扁平化测试（F11）：两种协议、工具名映射、截断、start 切片、只读。"""

import copy
import json

from codepilot.subagents.transcript import TranscriptEntry, flatten


def test_openai_text_only():
    msgs = [
        {"role": "user", "content": "做 X"},
        {"role": "assistant", "content": "结论 Y"},
    ]
    entries = flatten(msgs)
    assert [(e.kind, e.text) for e in entries] == [
        ("user", "做 X"), ("assistant", "结论 Y")]


def test_openai_tool_calls_and_result():
    msgs = [
        {"role": "user", "content": "查文件"},
        {"role": "assistant", "content": "我来读", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read", "arguments": json.dumps({"path": "a.py"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "文件内容"},
    ]
    entries = flatten(msgs)
    assert [e.kind for e in entries] == [
        "user", "assistant", "tool_use", "tool_result"]
    assert entries[2].name == "read"
    assert entries[2].text == '{"path": "a.py"}'
    assert entries[3].name == "read"
    assert entries[3].text == "文件内容"


def test_anthropic_blocks_and_name_mapping():
    msgs = [
        {"role": "user", "content": "开始"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "调用工具"},
            {"type": "tool_use", "id": "t1", "name": "grep", "input": {"q": "x"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "命中",
             "is_error": True},
        ]},
    ]
    entries = flatten(msgs)
    assert [e.kind for e in entries] == [
        "user", "assistant", "tool_use", "tool_result"]
    assert entries[1].text == "调用工具"          # tool_use 不混入助手文本
    assert entries[2].name == "grep"
    assert entries[3].name == "grep"              # 由 id 映射补齐
    assert entries[3].is_error is True
    assert entries[3].text == "命中"


def test_truncation_marks_cut_length():
    long = "x" * 5000
    entries = flatten([{"role": "tool", "content": long}], tool_result_max=100)
    assert len(entries) == 1
    assert entries[0].text.startswith("x" * 100)
    assert "[截断 4900 字符]" in entries[0].text


def test_start_slices_inherited_prefix():
    msgs = [
        {"role": "user", "content": "父历史"},
        {"role": "assistant", "content": "父回答"},
        {"role": "user", "content": "自有触发"},
        {"role": "assistant", "content": "自有结论"},
    ]
    entries = flatten(msgs, start=2)
    assert [e.text for e in entries] == ["自有触发", "自有结论"]


def test_does_not_mutate_input():
    msgs = [
        {"role": "assistant", "content": "a", "tool_calls": [
            {"id": "c1", "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r"},
    ]
    snapshot = copy.deepcopy(msgs)
    flatten(msgs)
    assert msgs == snapshot


def test_skips_empty_text_and_unknown_role_defaults_user():
    entries = flatten([
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "system", "content": "约束"},
    ])
    assert entries == [TranscriptEntry("user", "约束")]
