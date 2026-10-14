"""F1: Skill frontmatter+Markdown 解析与占位符渲染。"""

from pathlib import Path

import pytest

from codepilot.skills.loader import parse_skill_file, render_body
from codepilot.skills.model import SkillError

SAMPLE = """---
name: commit
description: 生成符合规范的提交信息
mode: shared
tools: [read, grep, run, edit, write]
---
# 提交

读取 git 状态后按 {input} 生成提交信息。
"""


def _write(tmp_path: Path, content: str, name: str = "s.md") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_parse_basic_fields(tmp_path):
    skill = parse_skill_file(_write(tmp_path, SAMPLE))
    assert skill.name == "commit"
    assert skill.description == "生成符合规范的提交信息"
    assert skill.mode == "shared"
    assert skill.tools == frozenset({"read", "grep", "run", "edit", "write"})
    assert "{input}" in skill.body


def test_parse_tools_comma_string(tmp_path):
    content = "---\nname: a\ndescription: b\nmode: isolated\ntools: read, grep, run\n---\nbody"
    skill = parse_skill_file(_write(tmp_path, content))
    assert skill.tools == frozenset({"read", "grep", "run"})
    assert skill.mode == "isolated"


def test_parse_optional_defaults(tmp_path):
    content = "---\nname: a\ndescription: b\n---\nbody"
    skill = parse_skill_file(_write(tmp_path, content))
    assert skill.bring_history == 0
    assert skill.model is None
    assert skill.tools == frozenset()
    assert skill.auto_prompt == ""   # 缺省空 → 裸命令不自动注入


def test_parse_auto_prompt(tmp_path):
    content = "---\nname: a\ndescription: b\nauto_prompt: 请审查改动\n---\nbody"
    skill = parse_skill_file(_write(tmp_path, content))
    assert skill.auto_prompt == "请审查改动"


def test_missing_name_raises(tmp_path):
    content = "---\ndescription: b\nmode: shared\n---\nbody"
    with pytest.raises(SkillError):
        parse_skill_file(_write(tmp_path, content))


def test_missing_description_raises(tmp_path):
    content = "---\nname: a\nmode: shared\n---\nbody"
    with pytest.raises(SkillError):
        parse_skill_file(_write(tmp_path, content))


def test_invalid_mode_raises(tmp_path):
    content = "---\nname: a\ndescription: b\nmode: parallel\n---\nbody"
    with pytest.raises(SkillError):
        parse_skill_file(_write(tmp_path, content))


def test_no_frontmatter_raises(tmp_path):
    content = "just body no frontmatter"
    with pytest.raises(SkillError):
        parse_skill_file(_write(tmp_path, content))


def test_render_body_replaces_input():
    assert render_body("按 {input} 处理", "  用户的话  ") == "按 用户的话 处理"


def test_render_body_no_placeholder_unchanged():
    assert render_body("固定指令", "x") == "固定指令"