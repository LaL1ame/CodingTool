"""层1 工具结果存盘(F1/F2)。

把超预算的大工具结果写到磁盘,对话历史中只留"预览 + 路径"替身,供模型按路径
通过既有 read 工具重读完整内容。

设计:
  offload(single)        : 单个结果预估 > N → 写盘,返回替身
  offload_oversized(batch): 同消息多结果合计 > M → 按 token 从大到小逐个存,
                           直至合计压回阈值;返回处理后的完整列表
  reopen_text(path)      : 按路径读回完整内容(模型 read 用)

磁盘写失败 → 降级(N5):返回仅预览、不丢原文,且不抛异常。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from codepilot.context.estimator import estimate_text
from codepilot.protocols import ToolResult

_PREVIEW_CHARS = 200


@dataclass(frozen=True)
class OffloadedResult:
    """存盘替身:供对话历史与调用方使用。"""

    path: str                 # 磁盘完整路径
    preview: str              # 预览片段
    saved_tokens: int         # 本次回收的 token 估算
    def display_text(self) -> str:
        return (f"[工具结果已存盘] {self.path}\n"
                f"[预览] {self.preview}\n"
                f"[如需完整内容请用 read 工具读取该路径]")


class ToolResultOffloader:
    """把大工具结果写到磁盘(层1)。"""

    def __init__(self, dir: Path) -> None:
        self._dir = Path(dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _write(self, result: ToolResult) -> OffloadedResult:
        """写盘并返回替身。失败→降级(返回仅预览,不抛)。"""
        content = result.error if result.error else (result.output or "")
        preview = (result.error or result.output or "")[:_PREVIEW_CHARS]
        size_tokens = estimate_text(content, kind="code")
        name = ((result.name or "tool").replace("/", "_").replace(" ", "_") or "tool")
        fname = f"{int(time.time())}-{uuid.uuid4().hex[:8]}-{name}.txt"
        path = self._dir / fname
        try:
            path.write_text(content, encoding="utf-8")
            return OffloadedResult(path=str(path), preview=preview, saved_tokens=size_tokens)
        except OSError:
            # N5 降级:不写盘,返回占位替身(路径为空 → 无法重读,但原文在 preview 尽力保留)
            return OffloadedResult(
                path="", preview=preview or "(空)",
                saved_tokens=min(size_tokens, len(preview)),
            )

    async def offload(self, result: ToolResult,
                      threshold_n: int = 1000) -> OffloadedResult | None:
        """单结果 > N → 写盘并返回替身;否则 None。"""
        size_tokens = estimate_text(result.error if result.error else (result.output or ""),
                                    kind="code")
        if size_tokens <= threshold_n:
            return None
        return self._write(result)

    async def offload_oversized(
        self, results: list[ToolResult],
        threshold_m: int = 4000, threshold_n: int = 1000,
    ) -> tuple[list[ToolResult | OffloadedResult], int]:
        """F2:同消息多结果合计 > M → 按 token 从大到小逐个存盘,直至压回阈值。

        返回 (处理后的结果列表, 总计回收 token 数)。被存盘的项为 OffloadedResult,
        其余原样保留。合计本身 ≤ M 时不变。
        """
        sizes = {
            i: estimate_text(r.error if r.error else (r.output or ""), kind="code")
            for i, r in enumerate(results)
        }
        if sum(sizes.values()) <= threshold_m:
            return list(results), 0

        # 从大到小排序索引,逐个存盘直至剩余非替身项合计 ≤ M
        order = sorted(sizes, key=sizes.__getitem__, reverse=True)
        saved_total = 0
        processed: list = list(results)
        for i in order:
            # 当前仍为 ToolResult 项的 token 合计;已达阈值 → 停止
            remaining = sum(
                sizes[k] for k in range(len(processed))
                if not isinstance(processed[k], OffloadedResult))
            if remaining <= threshold_m:
                break
            old = processed[i]
            if isinstance(old, OffloadedResult):
                continue
            off = self._write(old)
            processed[i] = off
            saved_total += off.saved_tokens
        return processed, saved_total

    async def reopen_text(self, path: str, max_chars: int = 65_536) -> str:
        """按路径读回完整内容(被存盘的,供模型 read)。"""
        try:
            return Path(path).read_text(encoding="utf-8")[:max_chars]
        except (OSError, ValueError):
            return ""