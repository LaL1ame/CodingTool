"""Skill 解析：frontmatter + Markdown 正文 → Skill；正文占位符渲染。

frontmatter 为文件首行起的 `---\n<YAML>\n---` 块；余下为 SOP 正文。
必填：name / description / mode。单个文件解析失败抛 SkillError，由调用方捕获隔离。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from codepilot.skills.model import Skill, SkillError

_MODES = ("shared", "isolated")


def parse_skill_file(path: Path) -> Skill:
    """解析一个 Skill 文件为 Skill。

    Raises:
        SkillError: frontmatter 缺失/非法、缺必填字段、mode 不合法。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SkillError(f"读取失败 {path}: {e}") from e
    return parse_skill_text(raw, path=path)


def parse_skill_text(raw: str, *, path: Path | None = None) -> Skill:
    """从文本解析 Skill（纯函数，便于单测）。

    Raises:
        SkillError: 结构/字段不合法。
    """
    fm, body = _split_frontmatter(raw)
    if fm is None:
        raise SkillError(f"缺少 YAML frontmatter: {path}")
    try:
        meta = yaml.safe_load(fm)
    except yaml.YAMLError as e:
        raise SkillError(f"frontmatter YAML 解析失败 {path}: {e}") from e
    if not isinstance(meta, dict):
        raise SkillError(f"frontmatter 应为映射: {path}")

    name = meta.get("name")
    description = meta.get("description")
    mode = meta.get("mode", "shared")
    if not name or not isinstance(name, str):
        raise SkillError(f"缺少 name: {path}")
    if not description or not isinstance(description, str):
        raise SkillError(f"缺少 description: {path}")
    if mode not in _MODES:
        raise SkillError(f"非法 mode={mode!r}（应为 {'/'.join(_MODES)}）: {path}")

    body = _strip_leading_blank(body)
    return Skill(
        name=name,
        description=description.strip(),
        body=body,
        tools=frozenset(_coerce_tools(meta.get("tools"))),
        mode=mode,
        bring_history=int(meta.get("bring_history", 0) or 0),
        model=meta.get("model") if isinstance(meta.get("model"), str) else None,
        params_desc=str(meta.get("params_desc", "") or ""),
        path=path,
        resources=frozenset(),
    )


def _split_frontmatter(raw: str) -> tuple[str | None, str]:
    """拆 frontmatter（首个 --- 块）与正文。无 frontmatter → (None, raw)。"""
    text = raw.lstrip("﻿")
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def _coerce_tools(value: object) -> list[str]:
    """tools 可写为 YAML 列表或逗号串，统一为小写去空白的名列表。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = value
    elif isinstance(value, str):
        items = [seg for seg in re.split(r"[,\s]+", value) if seg]
    else:
        return []
    out: list[str] = []
    for item in items:
        s = str(item).strip().lower()
        if s:
            out.append(s)
    return out


def _strip_leading_blank(text: str) -> str:
    return text.strip("\n")


def render_body(body: str, params: str = "") -> str:
    """把正文里的 {input} 占位替换为用户传入的 params（去首尾空白）。

    无 {input} 占位时正文原样返回；有占位但 params 为空则替换为空串。
    """
    if "{input}" not in body:
        return body
    return body.replace("{input}", params.strip())