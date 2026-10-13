"""Tab 补全候选(state14, F6) —— 候选唯一来自注册表,隐藏命令不参与。"""
from __future__ import annotations

from collections.abc import Callable

from codepilot.commands.registry import CommandRegistry


def make_completer(registry: CommandRegistry) -> Callable[[str], list[str]]:
    """返回一个补全器:输入行首 `/` 后的当前前缀,返回匹配候选命令名列表。

    对 `registry.candidates` 的薄封装,保持 candidate 语义(排除 hidden、去重、排序)。
    """

    def completer(input_text: str) -> list[str]:
        # 取行首 "/" 后至首个空白前的 token 作补全前缀;为空则列全部
        stripped = input_text.lstrip()
        if not stripped.startswith("/"):
            return []
        prefix = stripped[1:].split(" ", 1)[0]
        return registry.candidates(prefix)

    return completer