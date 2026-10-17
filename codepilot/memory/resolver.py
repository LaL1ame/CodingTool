"""项目指令文件层(F1/F2) —— 三层发现 + @include 原地展开。

职责分层:
  · discover_instruction_roots  —— 三层根(用户级→项目根→递归子目录)按深度升序
  · expand_includes             —— 处理 @include,限深/防环/防逃逸
  · load_instructions           —— 逐根读 *.md + 展开,拼接成一段

纯函数、无 IO 副作用(spi 由调用方注入),便于单测。
指令目录名 = ".rules"(与机器数据 .codepilot/ 区分;后者存会话/笔记/上下文)。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("codepilot.memory")

INSTRUCTION_ROOT_NAME = ".rules"

# include 深度上限与防环的 visited 集由调用方传递。
_DEFAULT_DEPTH_LIMIT = 4
_INCLUDE_PREFIX = "@include "


def _reldepth(root: Path, base: Path) -> int:
    """base 相对 root 的层级差(根自身=0)。"""
    try:
        return len(base.relative_to(root).parts)
    except ValueError:
        return 0


def discover_instruction_roots(
    workspace: str | Path,
    *,
    user_root: str | Path | None = None,
) -> list[Path]:
    """返回三层指令根,最不具体在前。

    [用户级, 项目根, *递归子目录(深度升序)]。搜索 workspace 下所有
    `INSTRUCTION_ROOT_NAME/` 目录,按相对 workspace 的深度升序;剪枝 .git / node_modules 等。
    """
    ws = Path(workspace)
    roots: list[Path] = []
    if user_root is not None:
        u = Path(user_root)
        if u.is_dir():
            roots.append(u)
    else:
        u = Path.home() / INSTRUCTION_ROOT_NAME
        if u.is_dir():
            roots.append(u)

    # 递归收集 workspace 下指令根目录,按深度升序,根自身(深度0)最前
    found: list[Path] = []
    for dirpath, dirnames, _ in os.walk(ws):
        dirnames[:] = [
            d for d in dirnames
            if d not in (".git", "node_modules", "__pycache__",
                         ".tox", ".venv", "venv", ".codepilot")
        ]
        p = Path(dirpath)
        if p.name == INSTRUCTION_ROOT_NAME:
            found.append(p)
            dirnames[:] = []  # 不进入指令根内部再找嵌套根(顶层即可)
    found.sort(key=lambda p: _reldepth(ws, p))
    roots.extend(found)
    return roots


def _instruction_boundary(path: Path) -> Path | None:
    """找含 path 的最近指令根目录(含自身),作为 include 逃逸边界。"""
    for p in [path, *path.parents]:
        if p.name == INSTRUCTION_ROOT_NAME:
            return p
    return None


def expand_includes(
    text: str,
    base_dir: Path,
    *,
    depth: int = 0,
    depth_limit: int = _DEFAULT_DEPTH_LIMIT,
    visited: set[Path] | None = None,
) -> str:
    """处理 @include <相对路径>,原地展开引用文件内容。

    规则(F2):
      · 相对 base_dir 解析,目标必须落在同一指令根树内(防逃逸)
      · 嵌套过 depth_limit → 跳过该引用
      · visited 防环 — 已展开过的文件不再重复
      · 单处展开失败(找不到/超限/逃逸/读失败)只跳过该行,其余保留
    """
    if visited is None:
        visited = set()
    boundary = _instruction_boundary(base_dir)
    out_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(_INCLUDE_PREFIX):
            rel = line[len(_INCLUDE_PREFIX):].strip()
            target = (base_dir / rel).resolve()
            # 防逃逸:目标必须仍在同一指令根树(或其内部子目录)
            within = (
                boundary is not None
                and (target == boundary or boundary in target.parents)
            )
            if depth >= depth_limit or not within or target in visited:
                continue  # 只跳过该引用
            visited.add(target)
            try:
                included = target.read_text(encoding="utf-8")
                out_lines.append(expand_includes(
                    included, target.parent,
                    depth=depth + 1, depth_limit=depth_limit, visited=visited,
                ))
            except OSError:
                logger.warning("memory @include 读取失败: %s", target)
            continue
        out_lines.append(raw)
    return "\n".join(out_lines)


def load_instructions(
    workspace: str | Path,
    *,
    user_root: str | Path | None = None,
    depth_limit: int = _DEFAULT_DEPTH_LIMIT,
    _roots: list[Path] | None = None,
) -> str:
    """拼接全部指令文件内容为一段。单文件失败静默跳过并记日志。"""
    roots = _roots if _roots is not None \
        else discover_instruction_roots(workspace, user_root=user_root)
    chunks: list[str] = []
    for root in roots:
        try:
            files = sorted(p for p in root.iterdir() if p.suffix == ".md")
        except OSError:
            continue
        for f in files:
            try:
                content = f.read_text(encoding="utf-8")
            except OSError:
                logger.warning("memory 指令读取失败: %s", f)
                continue
            chunks.append(expand_includes(content, root, depth_limit=depth_limit))
    return "\n\n".join(c for c in chunks if c.strip())