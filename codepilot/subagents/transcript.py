"""子 Agent 轨迹扁平化 —— 协议原生消息 → 可渲染条目（F11）。

把 Anthropic 内容块（text/tool_use/tool_result）与 OpenAI（content/tool_calls/
role=tool）统一为 `TranscriptEntry`，供 TUI 直接渲染。本模块不依赖 Textual，
也不修改入参消息列表（N3）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

_DEFAULT_TOOL_RESULT_MAX = 1000
_DEFAULT_TOOL_INPUT_MAX = 300


@dataclass(frozen=True)
class TranscriptEntry:
    """一条可渲染的轨迹记录。"""

    kind: str                    # user / assistant / tool_use / tool_result
    text: str
    name: str = ""               # 工具名（tool_use / tool_result）
    is_error: bool = False       # 工具结果是否失败


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… [截断 {len(text) - limit} 字符]"


def _stringify(value) -> str:
    """块/参数转文本；dict/list 用 JSON，其余 str()。"""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _block_text(content) -> str:
    """工具结果 content：可能是 str，也可能是 Anthropic 块列表。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_result":
                    parts.append(_block_text(block.get("content")))
                else:
                    parts.append(_stringify(block))
            elif str(block):
                parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return _stringify(content)


def _assistant_text(content) -> str:
    """助手消息只取文本块；tool_use 块由调用方单独展开。"""
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
            and block.get("text"))
    return _stringify(content)


def flatten(
    msgs: list[dict],
    *,
    start: int = 0,
    tool_result_max: int = _DEFAULT_TOOL_RESULT_MAX,
    tool_input_max: int = _DEFAULT_TOOL_INPUT_MAX,
) -> list[TranscriptEntry]:
    """协议原生消息 → 轨迹条目（只读，不修改 msgs）。

    `start` 用于 Fork：只保留触发消息及其后的自有部分，避免把继承的父历史
    再次持有/展示。工具结果与参数按上限截断。
    """
    entries: list[TranscriptEntry] = []
    tool_names: dict[str, str] = {}          # tool_use id → name（补齐 Anthropic 结果名）
    for msg in list(msgs)[max(0, start):]:
        role = str(msg.get("role", ""))
        content = msg.get("content")

        # OpenAI 工具结果
        if role == "tool":
            call_id = str(msg.get("tool_call_id", ""))
            name = str(msg.get("name") or tool_names.get(call_id, ""))
            text = _clip(_block_text(content), tool_result_max)
            if text:
                entries.append(TranscriptEntry("tool_result", text, name))
            continue

        if role == "assistant":
            text = _assistant_text(content) if content is not None else ""
            if text:
                entries.append(TranscriptEntry("assistant", text))
            # Anthropic tool_use 块
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = str(block.get("name", ""))
                        call_id = str(block.get("id", ""))
                        if call_id:
                            tool_names[call_id] = name
                        args = _clip(
                            _stringify(block.get("input", {})), tool_input_max)
                        entries.append(TranscriptEntry("tool_use", args, name))
            # OpenAI tool_calls
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                name = str(fn.get("name", ""))
                call_id = str(tc.get("id", ""))
                if call_id:
                    tool_names[call_id] = name
                args = _clip(_stringify(fn.get("arguments")), tool_input_max)
                entries.append(TranscriptEntry("tool_use", args, name))
            continue

        # user（含 Anthropic tool_result 块）/ system
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    if str(block):
                        entries.append(TranscriptEntry("user", str(block)))
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = str(block.get("text", ""))
                    if text:
                        entries.append(TranscriptEntry("user", text))
                elif btype == "tool_use":
                    name = str(block.get("name", ""))
                    call_id = str(block.get("id", ""))
                    if call_id:
                        tool_names[call_id] = name
                    args = _clip(_stringify(block.get("input", {})), tool_input_max)
                    entries.append(TranscriptEntry("tool_use", args, name))
                elif btype == "tool_result":
                    call_id = str(block.get("tool_use_id", ""))
                    text = _clip(_block_text(block.get("content")), tool_result_max)
                    if text:
                        entries.append(TranscriptEntry(
                            "tool_result", text, tool_names.get(call_id, ""),
                            bool(block.get("is_error"))))
                else:
                    text = _block_text(block)
                    if text:
                        entries.append(TranscriptEntry("user", text))
        else:
            text = _stringify(content)
            if text:
                kind = "assistant" if role == "assistant" else "user"
                entries.append(TranscriptEntry(kind, text))
    return entries
