"""Worktree 名称安全校验与自动命名（纯函数，无 IO）。

目录名允许斜杠嵌套，但必须是小写分段制，防止 LLM 输入触发路径遍历：
- 总长 ≤64，最多 3 段，每段 1–32；
- 段字符集 [a-z0-9._-]，段首必须是字母或数字；
- 拒绝 . / .. / 空段 / 反斜杠 / 绝对路径 / 前后空白 / 段尾点或空格；
- 拒绝 Windows 保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9，含扩展名形式）。
"""

from __future__ import annotations

import re

_MAX_TOTAL = 64
_MAX_SEGMENT = 32
_MAX_SEGMENTS = 3

_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class WorktreeNameError(ValueError):
    """Worktree 名称不合法。"""


def _fail(name: str, reason: str) -> WorktreeNameError:
    return WorktreeNameError(f"非法 worktree 名称 {name!r}: {reason}")


def validate_name(name: str) -> str:
    """校验并返回名称；不合法抛 `WorktreeNameError`。

    不做静默规范化：前后空白、大小写、多余斜杠都直接拒绝，避免调用方
    误以为传入值被接受。
    """
    if not isinstance(name, str):
        raise WorktreeNameError("worktree 名称必须是字符串")
    if not name:
        raise _fail(name, "不能为空")
    if name != name.strip():
        raise _fail(name, "不能含前后空白")
    if len(name) > _MAX_TOTAL:
        raise _fail(name, f"总长不能超过 {_MAX_TOTAL}")
    if name.startswith("/") or _DRIVE_RE.match(name):
        raise _fail(name, "不能是绝对路径")
    if "\\" in name:
        raise _fail(name, "不能含反斜杠")

    segments = name.split("/")
    if len(segments) > _MAX_SEGMENTS:
        raise _fail(name, f"最多 {_MAX_SEGMENTS} 段")
    for seg in segments:
        if not seg:
            raise _fail(name, "不能含空段")
        if seg in (".", ".."):
            raise _fail(name, "不能含 . 或 .. 段")
        if len(seg) > _MAX_SEGMENT:
            raise _fail(name, f"每段不能超过 {_MAX_SEGMENT} 字符")
        if seg.endswith((".", " ")):
            raise _fail(name, "段尾不能是点或空格")
        if not _SEGMENT_RE.fullmatch(seg):
            raise _fail(name, "字符集仅允许 [a-z0-9._-] 且段首须为字母或数字")
        if seg.split(".", 1)[0].lower() in _RESERVED:
            raise _fail(name, "不能使用 Windows 保留设备名")
    return name


def auto_name(role: str, task_id: int) -> str:
    """生成缺省目录名 ``<role-slug>-<task_id>`` 并通过同一套校验。"""
    slug = re.sub(r"[^a-z0-9]+", "-", str(role).lower()).strip("-") or "subagent"
    suffix = f"-{int(task_id)}"
    max_slug = max(1, _MAX_SEGMENT - len(suffix))
    slug = slug[:max_slug].strip("-") or "s"
    return validate_name(f"{slug}{suffix}")
