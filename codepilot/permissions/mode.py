"""权限模式：三档信任等级，只作「无规则命中」时的兜底判定。"""

from enum import Enum

from codepilot.permissions.rules import Verdict


class PermissionMode(Enum):
    """整体信任档位。不覆盖显式 deny 规则。"""
    STRICT = "strict"
    DEFAULT = "default"
    PERMISSIVE = "permissive"


_FALLBACK = {
    PermissionMode.STRICT: Verdict.DENY,
    PermissionMode.DEFAULT: Verdict.ASK,
    PermissionMode.PERMISSIVE: Verdict.ALLOW,
}


def fallback_for(mode: PermissionMode) -> Verdict:
    """无规则命中时按档位返回兜底判定。"""
    return _FALLBACK[mode]