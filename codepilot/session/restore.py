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

import json
import logging
import time
from pathlib import Path

from codepilot.session import store

logger = logging.getLogger("codepilot.session.restore")

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


def _openai_call(call: dict) -> dict:
    """把落盘的扁平 tool_calls 项重建为 OpenAI 请求形态(缺 function 包装/type)。"""
    args = call.get("arguments")
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False)
    return {"id": call.get("id"), "type": "function",
            "function": {"name": call.get("name"), "arguments": args}}


def _build_messages(segments: list[dict], protocol: str) -> list[dict]:
    """把段落重建为 request 体消息序列,按协议产出原生形态(可续跑)。

    state15 修:落盘 tool_calls 是【日志扁平形态】({id,name,arguments}),必须按
    协议重建请求形态,否则 resume 后原样回灌给 provider 会 400
    (openai 报 missing messages.tool_calls.function;anthropic 需 tool_use/content)。
    此前的恢复对两者都不合规,只在信手 live 兼容。
    """
    is_anthropic = protocol.lower() == "anthropic"
    msgs: list[dict] = []
    for s in segments:
        t = s["type"]
        if t == "message":
            msgs.append({"role": s.get("role"), "content": s.get("content", "")})
        elif t == "assistant":
            text = s.get("content") or ""
            calls = s.get("tool_calls") or []
            if is_anthropic:
                content: list[dict] = []
                if text:
                    content.append({"type": "text", "text": text})
                content.extend({"type": "tool_use", "id": c.get("id"),
                                "name": c.get("name"), "input": c.get("arguments")}
                               for c in calls)
                msgs.append({"role": "assistant", "content": content})
            else:
                msg: dict = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = [_openai_call(c) for c in calls]
                msgs.append(msg)
        elif t == "tool_result":
            content = s.get("output") or s.get("error") or ""
            if is_anthropic:
                msgs.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": s.get("call_id"),
                     "content": content}]})
            else:
                msgs.append({"role": "tool", "tool_call_id": s.get("call_id"),
                             "content": content})
    return msgs


def restore_chat(path: str | Path, *, protocol: str = "openai",
                 gap_hours: float | None = None,
                 now: float | None = None) -> dict:
    """把会话文件恢复为可续跑的状态。

    protocol: "anthropic"|"openai"(等 OpenAI 兼容)。决定工具调用/结果的重建形态。
    注:落盘是协议无关的扁平日志形态,必须按 protocol 重建,否则 resume 后回灌
    provider 会因 tool_calls 缺 function/tool_use 包装而 400。
    """
    path = Path(path)
    loaded = store.load_session(path)
    segments = loaded.get("segments", [])

    # 1. dangling 截断
    trimmed = _truncate_dangling(segments)
    n_cut = len(segments) - len(trimmed)

    # 2. 重建完整序列(按协议)
    msgs = _build_messages(trimmed, protocol)

    # 3. 时间跨度提醒:超 gap_hours → 最前插 role=user 提醒(保持首末角色交替合法)
    gap = _DEFAULT_GAP_HOURS if gap_hours is None else gap_hours
    gap_fired = False
    if gap > 0:
        last = _last_activity(trimmed)
        now = now if now is not None else time.time()
        if last and (now - last) > gap * 3600:
            hours = int((now - last) // 3600)
            remind = (f"（距上次会话已过去约 {hours} 小时。以下为历史记录,"
                      f"请结合上文继续。）")
            msgs.insert(0, {"role": "user", "content": remind})
            gap_fired = True

    logger.info("restore %s: %d msgs (dangling_cut=%d, gap_reminder=%s)",
                path.name, len(msgs), n_cut, gap_fired)
    return {"messages": msgs, "mode": loaded.get("mode"), "meta": loaded.get("meta")}