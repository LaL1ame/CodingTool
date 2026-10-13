"""自动笔记存储层(F3/F5) —— 四类 + 目录分隔 + 索引。

职责:
  · 四类常量:preferences/corrections(→用户级)与 project_knowledge/references(→项目级)
  · 每类一个 .md 文件,一行一条 bullet;读/增(精确去重)/删(幂等)
  · to_index:拼四类分组索引,施加 max_lines/max_chars 硬上限(字符优先)

目录:
  ~/.codepilot/memory/                     用户级
  ~/.codepilot/projects/<sha1(workspace)>/memory/   项目级(复用 state12 会话根)
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("codepilot.memory")

HOME_CODEPILOT = Path.home() / ".codepilot"

# 四类笔记:前两类用户级,后两类项目级
CATEGORIES: tuple[str, ...] = (
    "preferences", "corrections", "project_knowledge", "references",
)
CATEGORY_LABELS: dict[str, str] = {
    "preferences": "用户偏好",
    "corrections": "纠正反馈",
    "project_knowledge": "项目知识",
    "references": "参考资料",
}
_USER_CATS = ("preferences", "corrections")
_PROJECT_CATS = ("project_knowledge", "references")

# 索引硬上限(N1: 注入开销可控)。字符优先,再按行数截。
DEFAULT_MAX_LINES = 200
DEFAULT_MAX_CHARS = 25_000


def _project_memory_dir(workspace: str | Path, proj_dir: Path | None = None) -> Path:
    """项目级记忆目录;proj_dir 可注入便于测试。"""
    if proj_dir is not None:
        return proj_dir
    key = hashlib.sha1(str(workspace).encode("utf-8")).hexdigest()
    return HOME_CODEPILOT / "projects" / key / "memory"


@dataclass
class NoteStore:
    """四类笔记的读写删 + 索引。目录可注入临时路径,便于单测。"""

    workspace: str | Path
    user_dir: Path = field(default_factory=lambda: HOME_CODEPILOT / "memory")
    proj_dir: Path | None = None  # None → 自动按 sha1 计算

    def __post_init__(self) -> None:
        self.proj_dir = _project_memory_dir(self.workspace, self.proj_dir)

    def _side_dir(self, cat: str) -> Path:
        return self.user_dir if cat in _USER_CATS else self.proj_dir  # type: ignore[arg-type]

    def _path(self, cat: str) -> Path:
        return self._side_dir(cat) / f"{cat}.md"

    @staticmethod
    def _read_bullets(path: Path) -> list[str]:
        try:
            out = []
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("- "):
                    out.append(line[2:])
            return out
        except OSError:
            return []

    def load_notes(self) -> dict[str, list[str]]:
        """读四类笔记,缺文件→空列表。"""
        return {c: self._read_bullets(self._path(c)) for c in CATEGORIES}

    def append(self, cat: str, notes: list[str]) -> tuple[str, int]:
        """向某类追加新笔记;与既有精确去重;返回 (落盘侧标识, 新增数)。

        落盘侧标识: "user" 或 "project",供调用方/日志区分。
        """
        if cat not in CATEGORIES:
            logger.warning("skip unknown memory category: %s", cat)
            return ("", 0)
        clean = [n.strip() for n in notes if n and n.strip()]
        if not clean:
            return ("", 0)
        path = self._path(cat)
        existing = set(self._read_bullets(path))
        fresh = [n for n in clean if n not in existing]
        if not fresh:
            return ("", 0)
        side = "user" if cat in _USER_CATS else "project"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for n in fresh:
                f.write(f"- {n}\n")
        return (side, len(fresh))

    def delete(self, keyword: str) -> int:
        """跨两侧四类删正文含关键词条目;幂等;返回删除总数。"""
        kw = keyword.strip()
        if not kw:
            return 0
        total = 0
        for cat in CATEGORIES:
            path = self._path(cat)
            items = self._read_bullets(path)
            kept = [n for n in items if kw not in n]
            removed = len(items) - len(kept)
            if removed:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8") as f:
                    for n in kept:
                        f.write(f"- {n}\n")
                total += removed
        return total

    def to_index(self, *, max_lines: int = DEFAULT_MAX_LINES,
                 max_chars: int = DEFAULT_MAX_CHARS) -> str:
        """拼四类分组索引;先按 max_chars 截断,再按行数截(字符优先）。"""
        notes = self.load_notes()
        blocks: list[str] = []
        for cat in CATEGORIES:
            items = notes.get(cat) or []
            if not items:
                continue
            label = CATEGORY_LABELS.get(cat, cat)
            body = "\n".join(f"- {n}" for n in items)
            # 分组仅在有内容时出现
            blocks.append(f"[{label}]\n{body}")
        combined = "\n\n".join(blocks)
        if not combined:
            return ""
        # 字符优先:先砍超长,再按行上限(避免先砍行导致单行还超长)
        if len(combined) > max_chars:
            combined = combined[:max_chars]
        lines = combined.splitlines()
        if len(lines) > max_lines:
            combined = "\n".join(lines[:max_lines])
        return combined