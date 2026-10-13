"""自动记忆(F4) —— 每轮自然停后的后台 LLM 压缩写回。

职责分层:
  · run_once   —— 单次编排:构造提示 → 调用 provider → 解析 → store.append
  · schedule   —— fire-and-forget 包装,创建后台 task,异常仅记日志(不阻塞/不崩会话)

去重策略:交给 LLM 对比「既有笔记」判断语义重复,_parse_notes 只做规则性切分;
store.append 再以精确去重兜底。transcript 截尾避免超长。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from codepilot.memory.store import CATEGORIES, NoteStore

if TYPE_CHECKING:
    from codepilot.providers import BaseProvider

logger = logging.getLogger("codepilot.memory.auto")

# 单次压缩的 transcript 截尾上限(字符),防太长超窗。
_TRANSCRIPT_CHARS = 8_000

_SYSTEM_SUMMARY = (
    "你是 CodePilot 的记忆提炼器。给定一段对话记录与既有记忆,提炼值得"
    "长期记住的条目,并按四类归入:用户偏好 / 纠正反馈 / 项目知识 / 参考资料。"
    "-- 每条一句话,具体、可复用,不含冗长解释。"
    "-- 只输出要新增的、且与既有记忆不重复的条目(语义去重)。"
    "-- 没有值得记的就什么都不输出。"
    "-- 格式:每行一个条目,行首用「偏好:」「纠正:」「项目知识:」「参考:」冒号前缀。"
)

_CAT_DIRECTIVE = {
    "preferences": "偏好:",
    "corrections": "纠正:",
    "project_knowledge": "项目知识:",
    "references": "参考:",
}


def _transcript_text(transcript: list[dict]) -> str:
    """把 LLM 消息序列压缩为纯文本段(截尾),供记忆提炼。"""
    parts: list[str] = []
    for m in transcript:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            text = " ".join(
                str(b.get("text", b.get("content", "")))
                for b in content if isinstance(b, dict)
            )
        else:
            text = str(content or "")
        if role == "tool":
            parts.append(f"[工具] {text}")
        elif role == "assistant":
            parts.append(f"[助手] {text}")
        else:
            parts.append(f"[用户] {text}")
    joined = "\n".join(p for p in parts if p)
    if len(joined) > _TRANSCRIPT_CHARS:
        joined = joined[: _TRANSCRIPT_CHARS]
    return joined


def _parse_notes(text: str) -> dict[str, list[str]]:
    """从模型输出分行,按冒号前缀归入四类;无法识别类别的行丢弃。"""
    out: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    label_to_cat = {v: k for k, v in _CAT_DIRECTIVE.items()}
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for line in lines:
        cat = None
        for label, c in label_to_cat.items():
            if line.startswith(label):
                cat = c
                rest = line[len(label):].strip()
                break
        if cat is None:
            continue  # 无法识别,丢弃
        if rest:
            out[cat].append(rest)
    return out


async def run_once(provider: "BaseProvider", store: NoteStore,
                   transcript: list[dict]) -> int:
    """单次自动记忆:压缩本轮 → 解析 → 落盘;返回新增条数。失败抛给调度方吞掉。"""
    spoken = (
        "【本轮对话】\n" + _transcript_text(transcript) + "\n\n"
        + "【既有记忆(供去重对比,勿重复)】\n" + (store.to_index() or "(无)")
    )
    # 复用 provider.complete(状态9 compactor 同款):单次非流式,禁工具,独立调用。
    try:
        text = await provider.complete(
            _SYSTEM_SUMMARY, [{"role": "user", "content": spoken}])
    except Exception as e:
        raise RuntimeError(f"记忆提炼失败: {e}") from e
    if not text or not text.strip():
        logger.info("memory auto: 本轮无记忆产出")
        return 0
    notes = _parse_notes(text)
    added = 0
    for cat, items in notes.items():
        if not items:
            continue
        side, n = store.append(cat, items)
        added += n
        logger.info("memory auto: +%d %s(%s)", n, cat, side)
    return added


async def _safe_run(provider: "BaseProvider", store: NoteStore,
                    transcript: list[dict]) -> None:
    try:
        await run_once(provider, store, transcript)
    except Exception as e:  # 后台写绝不外泄/崩会话
        logger.warning("memory auto 失败(已忽略): %s", e)


def schedule(provider: "BaseProvider", store: NoteStore,
             transcript: list[dict]) -> asyncio.Task | None:
    """fire-and-forget 创建后台任务;返回 Task。调用方不 await。"""
    try:
        return asyncio.create_task(_safe_run(provider, store, list(transcript)))
    except RuntimeError:  # 无运行中的事件循环(如已 close)
        logger.warning("memory auto schedule 跳过:无事件循环")
        return None