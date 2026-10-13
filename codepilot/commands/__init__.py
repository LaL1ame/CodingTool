"""命令注册与分发机制(state14)。

- `CommandSpec`/`CommandKind`/`CommandRegistry`/`CommandConflictError`:注册中心
- `parse_command`/`ParseResult`:解析器(四态)
- `dispatch_command`:分发器(同步/协程处理器)
- `CommandContext`/`StatusSnapshot`:界面控制接口(协议)
- `build_registry`/`REVIEW_PROMPT`:内置命令与预设提示词
- `make_completer`:Tab 补全候选
"""
from codepilot.commands.builtins import REVIEW_PROMPT, build_registry
from codepilot.commands.completer import make_completer
from codepilot.commands.context import CommandContext, StatusSnapshot
from codepilot.commands.dispatcher import dispatch_command
from codepilot.commands.parser import ParseResult, parse_command
from codepilot.commands.registry import (
    CommandConflictError,
    CommandKind,
    CommandRegistry,
    CommandSpec,
)

__all__ = [
    "CommandSpec", "CommandKind", "CommandRegistry", "CommandConflictError",
    "ParseResult", "parse_command", "dispatch_command", "CommandContext",
    "StatusSnapshot", "build_registry", "REVIEW_PROMPT", "make_completer",
]