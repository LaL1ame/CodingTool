"""小组名/成员名校验与 worktree 名映射。

名称规则与 state18 的 worktree 段规则保持一致的字符集，但额外限制为：
- 单个名称 1–24 字符、只能单段；
- 小写、首字符字母/数字、字符集 `[a-z0-9._-]`；
- 拒绝 `.`/`..`、段尾点、Windows 保留名；
- 成员名 `lead` 保留给负责人。

成员 worktree 使用三级嵌套名 `team/<team>/<member>`，避免用 `-` 拼接时
不同 team/member 组合产生歧义（如 team=x, member=y-z 与 team=x-y, member=z）。
"""

from __future__ import annotations

import re

from codepilot.worktree.naming import WorktreeNameError, validate_name

NAME_MAX = 24
MEMBER_NAME_RESERVED = "lead"
WORKTREE_PREFIX = "team"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _validate(name: str, kind: str) -> str:
    if not isinstance(name, str):
        raise ValueError(f"{kind}名必须是字符串: {name!r}")
    if not name:
        raise ValueError(f"{kind}名不能为空")
    if name != name.strip():
        raise ValueError(f"{kind}名不能包含前后空白: {name!r}")
    if len(name) > NAME_MAX:
        raise ValueError(f"{kind}名最长 {NAME_MAX} 字符: {name!r}")
    if name in (".", ".."):
        raise ValueError(f"{kind}名不能是 '.' 或 '..'")
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"{kind}名只能包含小写字母、数字、'.'、'_'、'-'，"
            f"且必须以字母或数字开头: {name!r}")
    if name.endswith("."):
        raise ValueError(f"{kind}名不能以点结尾: {name!r}")
    base = name.split(".", 1)[0]
    if base in _RESERVED:
        raise ValueError(f"{kind}名不能使用 Windows 保留名: {name!r}")
    return name


def validate_team_name(name: str) -> str:
    """校验小组名；返回原名（已保证安全）。"""
    try:
        return _validate(name, "小组")
    except ValueError as e:
        from codepilot.teams.model import TeamNameError

        raise TeamNameError(str(e)) from e


def validate_member_name(name: str) -> str:
    """校验成员名；`lead` 为保留名，不能用作成员。"""
    try:
        _validate(name, "成员")
    except ValueError as e:
        from codepilot.teams.model import TeamNameError

        raise TeamNameError(str(e)) from e
    if name == MEMBER_NAME_RESERVED:
        from codepilot.teams.model import TeamNameError

        raise TeamNameError(f"成员名 {MEMBER_NAME_RESERVED!r} 为 Lead 保留")
    return name


def member_worktree_name(team: str, member: str) -> str:
    """成员的 worktree 名：`team/<team>/<member>`（三级嵌套，无拼接歧义）。"""
    validate_team_name(team)
    validate_member_name(member)
    name = f"{WORKTREE_PREFIX}/{team}/{member}"
    try:
        validate_name(name)
    except WorktreeNameError as e:  # pragma: no cover - 规则同步后的兜底
        from codepilot.teams.model import TeamNameError

        raise TeamNameError(f"worktree 名非法: {e}") from e
    return name
