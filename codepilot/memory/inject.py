"""记忆注入(F5) —— MemoryInjector:把「指令文件 + 记忆索引」组装为 volatile 块。

每轮由 agent.run 调用,产出 volatile 尾块交给 build_system_prompt。
指令与索引各自施加大小截断,避免挤占上下文预算(N1)。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from codepilot.memory.resolver import load_instructions
from codepilot.memory.store import DEFAULT_MAX_CHARS, DEFAULT_MAX_LINES, NoteStore

logger = logging.getLogger("codepilot.memory")


class MemoryInjector:
    """把指令文件与自动笔记组装为 volatile 块的入口(每轮调用)。

    兼容 `Callable[[], list[str]]`:实现 __call__,agent 可直接当 resolver 注入。
    """

    def __init__(self, workspace: str | Path, store: NoteStore | None = None) -> None:
        self.workspace = Path(workspace)
        self.store = store if store is not None else NoteStore(self.workspace)

    def instruction_block(self, *, max_lines: int = DEFAULT_MAX_LINES,
                          max_chars: int = DEFAULT_MAX_CHARS) -> str:
        """指令文件拼接结果,施加行/字上限;空则返回空串。"""
        text = load_instructions(self.workspace)
        if not text:
            return ""
        text = f"【项目指令】\n{text}"
        if len(text) > max_chars:
            text = text[:max_chars]
        lines = text.splitlines()
        if len(lines) > max_lines:
            text = "\n".join(lines[:max_lines])
        return text

    def index_block(self, *, max_lines: int = DEFAULT_MAX_LINES,
                    max_chars: int = DEFAULT_MAX_CHARS) -> str:
        """记忆索引块;空则返回空串。前缀占用 1 行,故在此对包裹后文本再截断,
        保证注入块总行/字不超过硬上限(N1,字符优先)。"""
        idx = self.store.to_index(max_lines=max_lines, max_chars=max_chars)
        if not idx:
            return ""
        block = f"【记忆】\n{idx}"
        if len(block) > max_chars:
            block = block[:max_chars]
        lines = block.splitlines()
        if len(lines) > max_lines:
            block = "\n".join(lines[:max_lines])
        return block

    def build(self) -> list[str]:
        """组装为 volatile 块列表;指令在前,索引在后。任一为空则省略。"""
        blocks: list[str] = []
        inst = self.instruction_block()
        if inst:
            blocks.append(inst)
        idx = self.index_block()
        if idx:
            blocks.append(idx)
        return blocks

    def __call__(self) -> list[str]:
        return self.build()

    # 复用类型标注:mapper 供 agent 明确识别为无参回调
    @staticmethod
    def as_resolver(injector: "MemoryInjector") -> Callable[[], list[str]]:
        return injector.build