"""SubAgent 角色解析：frontmatter + Markdown 正文 → AgentRole；四级发现与覆盖。

frontmatter 为文件首行起的 `---\n<YAML>\n---` 块；余下为子 Agent 系统提示正文。
必填：name / description。单个文件解析失败抛 RoleError，由调用方捕获隔离。

目录序（优先级从低到高）：[插件, 内置, 用户, 项目] —— 后者覆盖前者
（state17 F1：项目级高于用户级，高于内置，高于插件）。参考 skills/resolver.py
的遍历/剪枝约定，层级判定确定可测。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml

from codepilot.subagents.model import AgentRole, RoleError

logger = logging.getLogger("codepilot.subagents")

# 内置角色数据根（随包分发，参照 skills/data）。
BUILTIN_DIR = Path(__file__).parent / "data"

ROLE_ROOT_NAME = ".agents"

# 权限模式合法值（与 permissions/mode.py 对齐的字符串形态）
_PERMISSIONS = {"manual", "auto", "strict", "permissive"}

# 隔离模式合法值（state18；当前仅 worktree）
_ISOLATIONS = {"worktree"}

# 剪枝目录（镜像 skills/resolver.py）
_SKIP = {".git", "node_modules", "__pycache__", ".tox", ".venv", "venv",
         ".codepilot"}


def _split_frontmatter(raw: str) -> tuple[str | None, str]:
    """拆 frontmatter（首个 --- 块）与正文。无 frontmatter → (None, raw)。"""
    text = raw.lstrip("﻿")
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def _coerce_tools(value: object) -> frozenset[str]:
    """tools/blocked 可写为 YAML 列表或逗号串，统一为小写去空白的名集合。"""
    if value is None:
        return frozenset()
    if isinstance(value, (list, tuple)):
        items = value
    elif isinstance(value, str):
        items = [seg for seg in re.split(r"[,\s]+", value) if seg]
    else:
        return frozenset()
    return frozenset(str(item).strip().lower() for item in items if str(item).strip())


def parse_role_file(path: Path) -> AgentRole:
    """解析一个角色文件为 AgentRole。

    Raises:
        RoleError: frontmatter 缺失/非法、缺必填字段、非法 permission。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RoleError(f"读取失败 {path}: {e}") from e
    return parse_role_text(raw, path=path)


def parse_role_text(raw: str, *, path: Path | None = None) -> AgentRole:
    """从文本解析角色（纯函数，便于单测）。

    Raises:
        RoleError: 结构/字段不合法。
    """
    fm, body = _split_frontmatter(raw)
    if fm is None:
        raise RoleError(f"缺少 YAML frontmatter: {path}")
    try:
        meta = yaml.safe_load(fm)
    except yaml.YAMLError as e:
        raise RoleError(f"frontmatter YAML 解析失败 {path}: {e}") from e
    if not isinstance(meta, dict):
        raise RoleError(f"frontmatter 应为映射: {path}")

    name = meta.get("name")
    description = meta.get("description")
    if not name or not isinstance(name, str):
        raise RoleError(f"缺少 name: {path}")
    if not description or not isinstance(description, str):
        raise RoleError(f"缺少 description: {path}")

    permission = meta.get("permission")
    if permission is not None:
        permission = str(permission).strip().lower()
        if permission not in _PERMISSIONS:
            raise RoleError(
                f"非法 permission={permission!r}（应为 {'/'.join(sorted(_PERMISSIONS))}）: {path}")

    isolation = meta.get("isolation")
    if isolation is not None:
        isolation = str(isolation).strip().lower()
        if isolation not in _ISOLATIONS:
            raise RoleError(
                f"非法 isolation={isolation!r}（应为 {'/'.join(sorted(_ISOLATIONS))}）: {path}")

    model = meta.get("model")
    if model is not None and not isinstance(model, str):
        raise RoleError(f"model 应为字符串: {path}")
    max_rounds = meta.get("max_rounds")
    if max_rounds is not None:
        try:
            max_rounds = int(max_rounds)
        except (TypeError, ValueError):
            raise RoleError(f"max_rounds 应为整数: {path}") from None
        if max_rounds <= 0:
            raise RoleError(f"max_rounds 应为正整数: {path}")

    return AgentRole(
        name=str(name).strip(),
        description=str(description).strip(),
        body=(body or "").strip("\n"),
        tools=_coerce_tools(meta.get("tools")),
        blocked=_coerce_tools(meta.get("blocked")),
        model=model,
        max_rounds=max_rounds,
        permission=permission,
        isolation=isolation,
        can_delegate=bool(meta.get("can_delegate", False)),
        path=path,
    )


def _reldepth(root: Path, base: Path) -> int:
    try:
        return len(base.relative_to(root).parts)
    except ValueError:
        return 0


def discover_role_dirs(
    workspace: str | Path,
    *,
    user_root: str | Path | None = None,
    plugin_root: str | Path | None = None,
    builtin_root: str | Path | None = None,
) -> list[Path]:
    """返回四级角色根目录，优先级从低到高：`[插件, 内置, 用户, 项目]`。

    插件层预留（D15）：plugin_root 为 None 时跳过；内置默认 `data/`；
    用户默认 `~/.agents/`；项目递归 `.agents/`（深度升序，同 skills）。
    """
    ws = Path(workspace)
    dirs: list[Path] = []
    # 插件（优先级最低）
    if plugin_root is not None:
        p = Path(plugin_root)
        if p.is_dir():
            dirs.append(p)
    # 内置
    b = Path(builtin_root) if builtin_root is not None else BUILTIN_DIR
    if b.is_dir():
        dirs.append(b)
    # 用户
    if user_root is not None:
        u = Path(user_root)
        if u.is_dir():
            dirs.append(u)
    else:
        u = Path.home() / ROLE_ROOT_NAME
        if u.is_dir():
            dirs.append(u)
    # 项目（递归，深度升序）
    project: list[Path] = []
    for dirpath, dirnames, _ in os.walk(ws):
        dirnames[:] = [d for d in dirnames if d not in _SKIP]
        p = Path(dirpath)
        if p.name == ROLE_ROOT_NAME:
            project.append(p)
            dirnames[:] = []  # 不深入 .agents 内部再找嵌套根
    project.sort(key=lambda p: _reldepth(ws, p))
    dirs.extend(project)
    return dirs


def _iter_roles_in_dir(root: Path) -> list[Path]:
    """一个角色根目录下的全部角色文件：顶层 `*.md`（单文件型，目录型留后续）。"""
    entry_points: list[Path] = []
    try:
        entries = sorted(root.iterdir())
    except OSError as e:
        logger.warning("roles 根读取失败 %s: %s", root, e)
        return entry_points
    for e in entries:
        if e.is_file() and e.suffix == ".md":
            entry_points.append(e)
    return entry_points


def load_all(
    workspace: str | Path,
    *,
    user_root: str | Path | None = None,
    plugin_root: str | Path | None = None,
    builtin_root: str | Path | None = None,
    check_tools=None,
    _roots: list[Path] | None = None,
) -> dict[str, AgentRole]:
    """加载四级全部角色，同名高优先级覆盖。单个坏文件跳过不阻断整体。

    返回 `name -> AgentRole`（生效版本）。可选 check_tools 校验白名单存在性，
    命中不存在工具抛 RoleError（启动 fail-fast）。
    """
    roots = _roots if _roots is not None else discover_role_dirs(
        workspace, user_root=user_root, plugin_root=plugin_root,
        builtin_root=builtin_root)
    known: dict[str, AgentRole] = {}
    for root in roots:
        for entry in _iter_roles_in_dir(root):
            try:
                role = parse_role_file(entry)
            except RoleError as e:
                logger.warning("roles 跳过 %s: %s", entry, e)
                continue
            known[role.name] = role  # 高优先级覆盖（后加载胜）

    if check_tools is not None:
        for name, role in known.items():
            missing = [t for t in sorted(role.tools) if not check_tools(t)]
            if missing:
                raise RoleError(
                    f"角色 {name!r} 白名单含不存在工具: {', '.join(missing)}")
    return known
