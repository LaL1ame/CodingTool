"""团队系统配置：默认值 + config.yaml 的 team: 段覆盖。

配置文件结构::

    team:
      root: ~/.config/codepilot/teams
      approval_timeout_seconds: 600
      approval_poll_seconds: 0.5
      lock_timeout_seconds: 5
      lock_stale_seconds: 30
      lock_retry_seconds: 0.05
      message_summary_chars: 120
      max_unread_per_wakeup: 20
      default_backend: auto

非法配置在启动期抛 TeamSettingsError（fail-fast）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from codepilot.teams.model import (
    TeamBackendError,
    TeamMemberBackend,
    TeamSettings,
    TeamSettingsError,
)

DEFAULT_ROOT = Path.home() / ".config" / "codepilot" / "teams"


def _number(raw: object, field: str, *, integer: bool = False) -> float | int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TeamSettingsError(f"team.{field} 必须是数字: {raw!r}")
    if raw <= 0:
        raise TeamSettingsError(f"team.{field} 必须大于 0: {raw!r}")
    return int(raw) if integer else float(raw)


def load_team_settings(config_path: str | Path) -> TeamSettings:
    """读取 team: 段；文件/段缺失时返回默认配置。"""
    path = Path(config_path)
    raw: object = None
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            raise TeamSettingsError(f"读取团队配置失败: {e}") from e
    section = None
    if isinstance(raw, dict):
        section = raw.get("team")
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise TeamSettingsError("team 段必须是映射")

    root_raw = section.get("root", DEFAULT_ROOT)
    if not isinstance(root_raw, (str, Path)) or not str(root_raw).strip():
        raise TeamSettingsError(f"team.root 必须是非空路径: {root_raw!r}")
    root = Path(str(root_raw)).expanduser().resolve()

    backend = section.get("default_backend", "auto")
    if not isinstance(backend, str):
        raise TeamSettingsError(f"team.default_backend 必须是字符串: {backend!r}")
    backend = backend.strip().lower()
    if backend not in {"auto", "inprocess", "terminal"}:
        raise TeamSettingsError(
            f"team.default_backend 只能是 auto/inprocess/terminal: {backend!r}")

    return TeamSettings(
        root=root,
        approval_timeout_seconds=_number(
            section.get("approval_timeout_seconds", 600.0),
            "approval_timeout_seconds"),
        approval_poll_seconds=_number(
            section.get("approval_poll_seconds", 0.5),
            "approval_poll_seconds"),
        lock_timeout_seconds=_number(
            section.get("lock_timeout_seconds", 5.0),
            "lock_timeout_seconds"),
        lock_stale_seconds=_number(
            section.get("lock_stale_seconds", 30.0),
            "lock_stale_seconds"),
        lock_retry_seconds=_number(
            section.get("lock_retry_seconds", 0.05),
            "lock_retry_seconds"),
        message_summary_chars=_number(
            section.get("message_summary_chars", 120),
            "message_summary_chars", integer=True),
        max_unread_per_wakeup=_number(
            section.get("max_unread_per_wakeup", 20),
            "max_unread_per_wakeup", integer=True),
        default_backend=backend,
    )


def select_backend(requested: str | None, settings: TeamSettings) -> TeamMemberBackend:
    """state19 后端选择：auto/inprocess 通过；terminal 未实现，明确失败。"""
    value = (requested or settings.default_backend or "auto").strip().lower()
    if value == "auto":
        # state19 唯一已实现的后端；state20 再按环境优先级选择终端后端。
        return TeamMemberBackend.INPROCESS
    if value == TeamMemberBackend.INPROCESS.value:
        return TeamMemberBackend.INPROCESS
    if value == TeamMemberBackend.TERMINAL.value:
        raise TeamBackendError(
            "terminal 后端尚未实现（state20 交付）；本阶段仅支持 inprocess")
    raise TeamBackendError(f"未知成员运行后端: {value!r}")
