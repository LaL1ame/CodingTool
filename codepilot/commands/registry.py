"""命令注册中心(state14) —— 元数据 + 启动期冲突检测。

`CommandSpec` 集中登记每条命令的名称/别名/描述/用法/类型/参数提示/处理函数。
`CommandRegistry` 在 register 时即时查重,并提供 `assert_no_conflicts()` 启动兜底
(全量两两校验,覆盖别名互撞等非直接注册撞口),保证「撞名直接失败退出」而非运行时才炸。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注
    from codepilot.commands.context import CommandContext


class CommandKind(Enum):
    """命令执行模式三类:纯本地 / 影响界面 / 预设提示词送 AI。"""

    LOCAL = "local"    # 纯本地:不进对话、不改 UI 状态机
    UI = "ui"          # 影响界面/应用状态机
    PROMPT = "prompt"  # 把预设提示词送对话交给 AI


class CommandConflictError(Exception):
    """命令注册冲突:两个不同命令共用同一名称或别名。"""


@dataclass(frozen=True)
class CommandSpec:
    """一条命令的完整注册定义。name 为规范名(无前导斜杠、小写)。"""

    name: str
    kind: CommandKind
    desc: str
    handler: Callable[["CommandContext", str], "str | None"]
    aliases: tuple[str, ...] = ()
    usage: str = ""
    arg_hint: str | None = None
    hidden: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", self.name.lower())
        object.__setattr__(self, "aliases", tuple(a.lower() for a in self.aliases))


class CommandRegistry:
    """名称/别名 → CommandSpec 的集合,负责登记、解析、列全与补全候选。"""

    def __init__(self) -> None:
        self._specs: list[CommandSpec] = []
        self._by_token: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> None:
        """登记一条命令;name/任一 alias 与已注册者撞名即抛 `CommandConflictError`。"""
        for token in (spec.name, *spec.aliases):
            existing = self._by_token.get(token)
            if existing is not None:
                raise CommandConflictError(
                    f"命令大小写不敏感名 '{token}' 已被 {existing.name} 占用"
                    f"(新命令 {spec.name} 无法登记)"
                )
        self._specs.append(spec)
        for token in (spec.name, *spec.aliases):
            self._by_token[token] = spec

    def resolve(self, token: str) -> CommandSpec | None:
        """小写后精确匹配名称或别名,返回 spec 或 None。"""
        return self._by_token.get(token.lower())

    def all(self, *, include_hidden: bool = True) -> list[CommandSpec]:
        """按名称排序返回全部命令(可排除隐藏)。"""
        specs = [s for s in self._specs if include_hidden or not s.hidden]
        return sorted(specs, key=lambda s: s.name)

    def candidates(self, prefix: str) -> list[str]:
        """返回匹配前缀的名称+别名,排除隐藏、去重、排序(补全候选)。"""
        p = prefix.lower()
        out: set[str] = set()
        for s in self._specs:
            if s.hidden:
                continue
            for token in (s.name, *s.aliases):
                if token.startswith(p):
                    out.add(token)
        return sorted(out)

    def assert_no_conflicts(self) -> None:
        """全量两两校验(覆盖别名互撞);冲突抛 `CommandConflictError`,启动期调用。"""
        seen: dict[str, CommandSpec] = {}
        for s in self._specs:
            for token in (s.name, *s.aliases):
                prev = seen.get(token)
                if prev is not None and prev is not s:
                    raise CommandConflictError(
                        f"启动期发现命令冲突: token '{token}' 同时被 "
                        f"{prev.name} 与 {s.name} 占用"
                    )
                seen[token] = s