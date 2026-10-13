"""Skill 三级发现与优先级覆盖（F3）+ 单文件故障隔离（N2）+ 白名单校验。

目录序（低→高优先级）：[内置, 用户, *项目递归 .skills(深度升序)]。
同名 Skill 由 dict 赋值后者覆盖前者 → 项目胜出、用户次之、内置最低。
镜像 state13 memory/resolver.py 的遍历/剪枝约定，层级判定确定可测。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

from codepilot.skills.loader import parse_skill_file
from codepilot.skills.model import Skill, SkillError

logger = logging.getLogger("codepilot.skills")

SKILL_ROOT_NAME = ".skills"
ENTRY_FILE = "SKILL.md"

# 剪枝目录（镜像 memory/resolver.py）
_SKIP = {".git", "node_modules", "__pycache__", ".tox", ".venv", "venv"}


def _reldepth(root: Path, base: Path) -> int:
    try:
        return len(base.relative_to(root).parts)
    except ValueError:
        return 0


def discover_skill_dirs(
    workspace: str | Path,
    *,
    user_root: str | Path | None = None,
    builtin_root: str | Path | None = None,
) -> list[Path]:
    """返回三级 Skill 根目录，最不具体在前（低→高优先级）。

    [内置, 用户, *项目递归 .skills(深度升序)]；剪枝 _SKIP。
    """
    ws = Path(workspace)
    dirs: list[Path] = []
    if builtin_root is not None:
        b = Path(builtin_root)
        if b.is_dir():
            dirs.append(b)
    if user_root is not None:
        u = Path(user_root)
        if u.is_dir():
            dirs.append(u)
    else:
        u = Path.home() / SKILL_ROOT_NAME
        if u.is_dir():
            dirs.append(u)

    project: list[Path] = []
    for dirpath, dirnames, _ in os.walk(ws):
        dirnames[:] = [d for d in dirnames if d not in _SKIP]
        p = Path(dirpath)
        if p.name == SKILL_ROOT_NAME:
            project.append(p)
            dirnames[:] = []  # 不深入 .skills 内部再找嵌套根
    project.sort(key=lambda p: _reldepth(ws, p))
    dirs.extend(project)
    return dirs


def _iter_skills_in_dir(root: Path) -> list[Path]:
    """一个 Skill 根目录下的全部 Skill 入口：*.md 文件，或含 SKILL.md 的子目录。"""
    entry_points: list[Path] = []
    try:
        entries = sorted(root.iterdir())
    except OSError as e:
        logger.warning("skills 根读取失败 %s: %s", root, e)
        return entry_points
    for e in entries:
        if e.is_file() and e.suffix == ".md":
            entry_points.append(e)
        elif e.is_dir() and (e / ENTRY_FILE).is_file():
            entry_points.append(e / ENTRY_FILE)
    return entry_points


def _resources_of(entry: Path) -> frozenset[Path]:
    """目录型 Skill 的辅助资源：SKILL.md 所在目录里的其它文件。

    仅当 entry 是目录型入口（文件名 == SKILL.md）才收集同目录兄弟文件；
    单文件型（顶层 .md）不把兄弟 .md 当作资源。
    """
    if entry.name != ENTRY_FILE:
        return frozenset()
    try:
        return frozenset(f for f in entry.parent.iterdir() if f != entry)
    except OSError:
        return frozenset()


def load_all(
    workspace: str | Path,
    *,
    user_root: str | Path | None = None,
    builtin_root: str | Path | None = None,
    check_tools: Callable[[str], bool] | None = None,
    _roots: list[Path] | None = None,
) -> dict[str, Skill]:
    """加载三级全部 Skill，同名高优先级覆盖。单个坏文件跳过不阻断整体。

    返回 `name -> Skill`（生效版本）。可选 check_tools 校验白名单工具存在性，
    命中不存在工具抛 SkillError（启动 fail-fast）。
    """
    roots = _roots if _roots is not None else discover_skill_dirs(
        workspace, user_root=user_root, builtin_root=builtin_root)
    known: dict[str, Skill] = {}
    for root in roots:
        for entry in _iter_skills_in_dir(root):
            try:
                skill = parse_skill_file(entry)
                res = _resources_of(entry)
                if res:
                    skill = Skill(
                        **{**skill.__dict__, "resources": res,
                           "path": entry})
            except (SkillError, OSError) as e:
                logger.warning("skills 跳过 %s: %s", entry, e)
                continue
            known[skill.name] = skill  # 高优先级覆盖（同后备胜）

    if check_tools is not None:
        for name, skill in known.items():
            missing = [t for t in sorted(skill.tools) if not check_tools(t)]
            if missing:
                raise SkillError(
                    f"Skill {name!r} 白名单含不存在工具: {', '.join(missing)}")
    return known