"""会话恢复编排(state12) —— 把 JSONL 段落重建为可续跑的 LLM 消息序列。

职责分层:store.load_session 只做「解析原样」,本模块做「恢复策略」:
  1. 坏行已在 load_session 跳过
  2. dangling 截断 —— 某 assistant 的工具调用缺失对应结果 → 该行起截断
  3. 重建完整 LLM 序列(request 体,含 tool_calls / tool_result)
  4. 时间跨度提醒 —— 距上次活动超 gap_hours 时,最前插一条 role=user 提醒

token 超限「先压一次」不在此处:需 chat 挂载后的 ContextManager 执行(见
context/manager.compact_if_over),保持本模块纯、可独立单测。
"""
from __future__ import annotations

import time
from pathlib import Path

from codepilot.session import store

# 时间跨度提醒阈值默认(小时);为空/为 0 则关闭提醒(测试注入).
_DEFAULT_GAP_HOURS = 24


def _truncate_dangling(segments: list[dict]) -> list[dict]:
    """工具调用无对应结果 → 从该 assistant 行起截断(含本行),丢弃其后所有行。

    两遍扫描:先收齐全部结果 id,再看每个 assistant 的调用是否都有结果。
    """
    total_results = {r["call_id"] for r in segments if r["type"] == "tool_result"}
    for i, s in enumerate(segments):
        if s["type"] == "assistant" and s.get("tool_calls"):
            ids = {c["id"] for c in s["tool_calls"]}
            if not ids <= total_results:  # 有调用缺结果 → 悬空
                return segments[:i]
    return segments


def _last_activity(segments: list[dict]) -> float:
    """段落里最近的活动时间(文本行 ts=timestamp,工具行 ts)。无则 0。"""
    stamps = [s["ts"] for s in segments if s.get("ts")]
    return max(stamps) if stamps else 0.0


def _build_messages(segments: list[dict]) -> list[dict]:
    """把段落重建为 request 体消息序列(开方/openai 形态,可续跑)。
    user/assistant 文本 + assistant(带 tool_calls) + tool(结果)。
    """
    msgs: list[dict] = []
    for s in segments:
        if s["type"] == "message":
            msgs.append({"role": s.get("role"), "content": s.get("content", "")})
        elif s["type"] == "assistant":
            msg: dict = {"role": "assistant", "content": s.get("content") or None}
            if s.get("tool_calls"):
                msg["tool_calls"] = s["tool_calls"]
            msgs.append(msg)
        elif s["type"] == "tool_result":
            content = s.get("output") or s.get("error") or ""
            msgs.append({"role": "tool", "tool_call_id": s.get("call_id"),
                         "content": content})
    return msgs


def restore_chat(path: str | Path, *, gap_hours: float | None = None,
                 now: float | None = None) -> dict:
    """把会话文件恢复为可续跑的状态。

    返回 {messages, mode, meta}:messages 为含工具调用的完整 LLM 序列(可为
    ChatManager._msgs 的种子);mode/meta 直接透传。
    """
    path = Path(path)
    loaded = store.load_session(path)
    segments = loaded.get("segments", [])

    # 1. dangling 截断
    trimmed = _truncate_dangling(segments)

    # 2. 重建完整序列
    msgs = _build_messages(trimmed)

    # 3. 时间跨度提醒:超 gap_hours → 最前插 role=user 提醒(保持首末角色交替合法)
    gap = _DEFAULT_GAP_HOURS if gap_hours is None else gap_hours
    if gap > 0:
        last = _last_activity(trimmed)
        now = now if now is not None else time.time()
        if last and (now - last) > gap * 3600:
            hours = int((now - last) // 3600)
            remind = (f"（距上次会话已过去约 {hours} 小时。以下为历史记录,"
                      f"请结合上文继续。）")
            msgs.insert(0, {"role": "user", "content": remind})

    return {"messages": msgs, "mode": loaded.get("mode"), "meta": loaded.get("meta")}