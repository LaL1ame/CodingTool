"""Worktree 配置：内置安全默认 + 项目根 ``.worktree.yaml`` 覆盖。

配置文件结构::

    worktree:
      ttl_hours: 24
      cleanup_interval_minutes: 60
      copy: [config.yaml, .hooks.yaml]
      symlink: [.venv]
      include: [".env*"]

``copy`` / ``symlink`` / ``include`` 采用覆盖语义：项目写了该字段就整体替换
默认列表，没写才用默认。规则必须是主仓内的相对路径，不允许绝对路径或 ``..``。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_COPY: tuple[str, ...] = (
    "config.yaml",
    ".hooks.yaml",
    "codepilot-permissions.local.yaml",
)
DEFAULT_SYMLINK: tuple[str, ...] = (".venv", "venv", "node_modules")
DEFAULT_INCLUDE: tuple[str, ...] = ()

CONFIG_NAME = ".worktree.yaml"


class WorktreeSettingsError(Exception):
    """Worktree 配置非法（启动期 fail-fast）。"""


@dataclass(frozen=True)
class WorktreeSettings:
    ttl_hours: float = 24.0
    cleanup_interval_minutes: float = 60.0
    copy: tuple[str, ...] = DEFAULT_COPY
    symlink: tuple[str, ...] = DEFAULT_SYMLINK
    include: tuple[str, ...] = DEFAULT_INCLUDE


def _positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorktreeSettingsError(f"worktree.{field} 必须是数字: {value!r}")
    if value <= 0:
        raise WorktreeSettingsError(f"worktree.{field} 必须大于 0: {value!r}")
    return float(value)


def _validate_rule(rule: str, field: str) -> str:
    if not rule:
        raise WorktreeSettingsError(f"worktree.{field} 规则不能为空")
    if rule in (".", "./"):
        raise WorktreeSettingsError(f"worktree.{field} 规则不能指向主仓根: {rule!r}")
    if "\\" in rule:
        raise WorktreeSettingsError(f"worktree.{field} 规则请使用 '/' 分隔: {rule!r}")
    normalized = rule.removeprefix("./")
    path = Path(normalized)
    if path.is_absolute() or normalized.startswith("/"):
        raise WorktreeSettingsError(f"worktree.{field} 规则不能是绝对路径: {rule!r}")
    if ".." in path.parts:
        raise WorktreeSettingsError(f"worktree.{field} 规则不能含 '..': {rule!r}")
    return normalized


def _rules(raw: object, default: tuple[str, ...], field: str) -> tuple[str, ...]:
    if raw is None:
        return default
    if not isinstance(raw, (list, tuple)):
        raise WorktreeSettingsError(f"worktree.{field} 必须是字符串列表: {raw!r}")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise WorktreeSettingsError(f"worktree.{field} 必须是字符串列表: {raw!r}")
        out.append(_validate_rule(item.strip(), field))
    return tuple(out)


def load_settings(workspace: str | Path) -> WorktreeSettings:
    """读取 ``<workspace>/.worktree.yaml``；缺失返回全默认，非法抛错。"""
    path = Path(workspace) / CONFIG_NAME
    if not path.exists():
        return WorktreeSettings()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise WorktreeSettingsError(f"读取 {path} 失败: {e}") from e
    if raw is None:
        return WorktreeSettings()
    if not isinstance(raw, dict):
        raise WorktreeSettingsError(f"{path} 顶层必须是映射")
    section = raw.get("worktree", {})
    if section is None:
        return WorktreeSettings()
    if not isinstance(section, dict):
        raise WorktreeSettingsError(f"{path} 的 worktree 段必须是映射")

    ttl = section.get("ttl_hours", 24.0)
    interval = section.get("cleanup_interval_minutes", 60.0)
    return WorktreeSettings(
        ttl_hours=_positive_number(ttl, "ttl_hours"),
        cleanup_interval_minutes=_positive_number(
            interval, "cleanup_interval_minutes"),
        copy=_rules(section.get("copy"), DEFAULT_COPY, "copy"),
        symlink=_rules(section.get("symlink"), DEFAULT_SYMLINK, "symlink"),
        include=_rules(section.get("include"), DEFAULT_INCLUDE, "include"),
    )
