"""A2 成员上下文跨进程恢复：协议原生 `_msgs` ↔ 会话 JSONL 扁平行。

复用 state12 的 `session/store.py`（append_* 扁平行）与 `session/restore.py`
（`restore_chat` 按协议重建消息序列）。成员 `_msgs` 是 Agent.run 原地追加的
协议原生 dict（assistant 含 tool_use 块、user 含 tool_result 块），落盘时转成
扁平行，恢复时对称转回 —— 保证「保存 → 恢复 == 原 msgs」。
"""

from __future__ import annotations

import logging

from codepilot.session import store
from codepilot.session.restore import restore_chat
from codepilot.teams.naming import MEMBER_NAME_RESERVED
from codepilot.teams.storage import TeamStorage

logger = logging.getLogger("codepilot.teams.context")


def _is_anthropic(msg: dict, protocol: str) -> bool:
    """按协议与消息形态判断该条消息用 anthropic 块结构。"""
    if protocol.lower() == "anthropic":
        return True
    # openai 协议下若 content 是 list（异常形态）也按块处理
    return isinstance(msg.get("content"), list)


def _text_of(msg: dict) -> str:
    """取 assistant/user 消息的纯文本部分（content 可能是 str 或块列表）。"""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        return "".join(parts)
    return ""


def _flatten_tool_calls(msg: dict) -> list[dict]:
    """协议原生 tool_calls / tool_use 块 → 扁平 {id, name, arguments}。"""
    out: list[dict] = []
    if msg.get("tool_calls"):
        for c in msg["tool_calls"]:
            fn = c.get("function", {}) if isinstance(c.get("function"), dict) else {}
            out.append({
                "id": c.get("id", ""),
                "name": fn.get("name") or c.get("name", ""),
                "arguments": fn.get("arguments") or c.get("arguments", {}),
            })
    content = msg.get("content")
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                out.append({
                    "id": b.get("id", ""),
                    "name": b.get("name", ""),
                    "arguments": b.get("input", {}),
                })
    return out


def _tool_results(msg: dict, protocol: str) -> list[dict]:
    """取 user/tool 消息中的 tool_result 块 → 扁平 {call_id, name, output, error}。"""
    out: list[dict] = []
    content = msg.get("content")
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                out.append({
                    "call_id": b.get("tool_use_id", ""),
                    "name": b.get("name"),
                    "output": b.get("content"),
                    "error": "error" if b.get("is_error") else None,
                })
    if protocol.lower() != "anthropic" and msg.get("role") == "tool":
        out.append({
            "call_id": msg.get("tool_call_id", ""),
            "name": msg.get("name"),
            "output": msg.get("content"),
            "error": None,
        })
    return out


def _append_one(msg: dict, path, protocol: str) -> None:
    """把一条协议原生消息追加为会话 JSONL 扁平行。"""
    role = msg.get("role")
    if role == "assistant":
        tool_calls = _flatten_tool_calls(msg)
        if tool_calls:
            store.append_assistant_toolcalls(
                path, content=_text_of(msg), tool_calls=tool_calls)
            return
        store.append_message(path, role="assistant", content=_text_of(msg))
        return
    results = _tool_results(msg, protocol)
    if results:
        for r in results:
            store.append_tool_result(
                path, call_id=r["call_id"], name=r["name"],
                output=r["output"], error=r["error"])
        return
    if role in ("user", "assistant"):
        store.append_message(path, role=role, content=_text_of(msg))


def member_context_path(storage: TeamStorage, team: str, member: str):
    """成员上下文 JSONL 路径（透传 storage）。"""
    if member == MEMBER_NAME_RESERVED:
        raise ValueError("lead 没有成员上下文")
    return storage.member_context_path(team, member)


def save_member_msgs(
    storage: TeamStorage, team: str, member: str,
    msgs: list[dict], *, persisted_len: int = 0, protocol: str = "anthropic",
) -> int:
    """把成员 `_msgs` 增量追加落盘；返回新的已持久化长度。

    `persisted_len` 是上次已落盘的消息条数游标；只转 `msgs[persisted_len:]`，
    幂等、可重复调用。
    """
    if member == MEMBER_NAME_RESERVED:
        return persisted_len
    path = storage.member_context_path(team, member)
    for msg in msgs[persisted_len:]:
        _append_one(msg, path, protocol)
    return len(msgs)


def load_member_msgs(
    storage: TeamStorage, team: str, member: str, *, protocol: str,
) -> list[dict] | None:
    """恢复成员完整消息序列；无文件/损坏/恢复异常 → None（降级全新上下文）。"""
    if member == MEMBER_NAME_RESERVED:
        return None
    path = storage.member_context_path(team, member)
    if not path.exists():
        return None
    try:
        restored = restore_chat(path, protocol=protocol)
        return restored.get("messages") or None
    except Exception as e:  # noqa: BLE001 - 损坏上下文不阻塞派生
        logger.warning("成员 %s 上下文恢复失败，降级全新: %s", member, e)
        return None
