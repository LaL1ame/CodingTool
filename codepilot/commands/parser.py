"""命令解析器(state14) —— 斜杠前缀识别,返回四态 ParseResult(F2)。

纯函数、无副作用、可单测;只依赖 registry 做命中判定。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from codepilot.commands.registry import CommandRegistry, CommandSpec


@dataclass
class ParseResult:
    """一次输入的解析结果。

    kind:
      - "blank":   空/全空白,早返回
      - "message": 非斜杠普通输入,交给 AI
      - "command": 命中注册命令,携 spec + params
      - "unknown": 斜杠开头但未命中(或裸 `/`),给 /help 引导,不送 AI
    """

    kind: Literal["blank", "message", "command", "unknown"]
    spec: CommandSpec | None = None
    params: str = ""
    text: str = ""


def parse_command(text: str, registry: CommandRegistry) -> ParseResult:
    """解析输入。首空白前为命令名、后为参数;命令名小写不敏感。"""
    if not text or not text.strip():
        return ParseResult(kind="blank")
    stripped = text.strip()
    if not stripped.startswith("/"):
        return ParseResult(kind="message", text=stripped)

    token, _, rest = stripped.partition(" ")
    token = token.lower()
    params = rest.strip()
    if token in ("/", ""):
        return ParseResult(kind="unknown", text=stripped)
    spec = registry.resolve(token[1:])
    if spec is None:
        return ParseResult(kind="unknown", text=stripped)
    return ParseResult(kind="command", spec=spec, params=params, text=stripped)