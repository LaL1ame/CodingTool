"""Skill 数据模型 — 可复用 AI 操作能力包的解析结果结构。

Skill 由 YAML frontmatter（元信息）+ Markdown 正文（SOP 指令）构成。
本模块只定义结构，解析逻辑在 loader.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


class SkillError(Exception):
    """Skill 解析/加载过程的可预期错误（缺字段、非法 mode、白名单工具不存在等）。"""

    pass


@dataclass(frozen=True)
class Skill:
    """一个 Skill 的解析结果。"""

    name: str                            # 唯一名（= frontmatter name，也是命令名默认来源）
    description: str                     # 一句话说明（阶段一注入）
    body: str                            # SOP 正文（含占位符）
    tools: frozenset[str] = frozenset()  # 工具白名单（空 = 不约束可选工具）
    mode: Literal["shared", "isolated"] = "shared"
    bring_history: int = 0               # 仅 isolated 有效
    model: str | None = None             # 可选指定模型（仅 isolated 生效）
    params_desc: str = ""                # 参数说明（供命令用法/提示）
    path: Path | None = None             # 来源文件（内置/用户/项目）
    resources: frozenset[Path] = field(default_factory=frozenset)  # 目录型 Skill 的辅助资源