"""SubAgent 角色加载测试：四级覆盖 + 校验全集 + 坏文件隔离。"""

from pathlib import Path

import pytest

from codepilot.subagents.loader import (
    discover_role_dirs,
    load_all,
    parse_role_file,
    parse_role_text,
)
from codepilot.subagents.model import AgentRole, RoleError

_FM = """---
name: {name}
description: {desc}
tools: [read, glob]
---

系统提示正文 {name}
"""


def _role_file(dir_: Path, name: str, extra: str = "",
               body_marker: str = "") -> Path:
    p = dir_ / f"{name}.md"
    fm = _FM.format(name=name, desc=name)
    if extra:
        fm = fm.replace("tools: [read, glob]", extra, 1)
    if body_marker:
        fm = fm.rstrip("\n") + f"\n\n[{body_marker}]\n"
    p.write_text(fm, encoding="utf-8")
    return p


def test_parse_role_basic(tmp_path):
    p = _role_file(tmp_path, "reviewer")
    role = parse_role_file(p)
    assert isinstance(role, AgentRole)
    assert role.name == "reviewer"
    assert role.tools == frozenset({"read", "glob"})
    assert role.blocked == frozenset()
    assert role.permission is None
    assert role.can_delegate is False
    assert "系统提示正文" in role.body


def test_parse_role_full_fields(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("""---
name: architect
description: 架构师
tools: [read, glob, grep]
blocked: [run]
model: claude-sonnet-4-6
max_rounds: 8
permission: strict
can_delegate: true
---

正文
""", encoding="utf-8")
    role = parse_role_file(p)
    assert role.blocked == frozenset({"run"})
    assert role.model == "claude-sonnet-4-6"
    assert role.max_rounds == 8
    assert role.permission == "strict"
    assert role.can_delegate is True


def test_parse_missing_frontmatter(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("没有 frontmatter 的正文\n", encoding="utf-8")
    with pytest.raises(RoleError):
        parse_role_file(p)


def test_parse_missing_name(tmp_path):
    with pytest.raises(RoleError):
        parse_role_text("---\ndescription: d\n---\nbody")


def test_parse_bad_permission(tmp_path):
    with pytest.raises(RoleError):
        parse_role_text("---\nname: x\ndescription: d\npermission: wild\n---\nbody")


def test_parse_tools_as_comma_string(tmp_path):
    role = parse_role_text(
        "---\nname: x\ndescription: d\ntools: read, glob\n---\nbody")
    assert role.tools == frozenset({"read", "glob"})


def test_discover_priority_order(tmp_path, monkeypatch):
    """四级目录序：插件 < 内置 < 用户 < 项目。"""
    plugin = tmp_path / "plugin" / ".agents"
    builtin = tmp_path / "builtin"
    user = tmp_path / "user" / ".agents"
    project = tmp_path / "ws" / ".agents"
    for d in (plugin, builtin, user, project):
        d.mkdir(parents=True)
    dirs = discover_role_dirs(
        tmp_path / "ws", plugin_root=plugin, builtin_root=builtin, user_root=user)
    assert dirs == [plugin, builtin, user, project]


def test_load_all_override_project_wins(tmp_path):
    """同名角色：项目覆盖用户、用户覆盖内置、内置覆盖插件。"""
    plugin = tmp_path / "plugin" / ".agents"
    builtin = tmp_path / "builtin"
    user = tmp_path / "user" / ".agents"
    project = tmp_path / "ws" / ".agents"
    for d in (plugin, builtin, user, project):
        d.mkdir(parents=True)
    _role_file(plugin, "r", extra="tools: [read]", body_marker="plugin")
    _role_file(builtin, "r", extra="tools: [glob]", body_marker="builtin")
    _role_file(user, "r", extra="tools: [grep]", body_marker="user")
    _role_file(project, "r", extra="tools: [run]", body_marker="project")

    known = load_all(tmp_path / "ws", plugin_root=plugin, builtin_root=builtin,
                     user_root=user, check_tools=lambda n: True)
    assert known["r"].tools == frozenset({"run"})   # 项目胜出
    assert "[project]" in known["r"].body           # 项目正文生效


def test_load_all_bad_file_skipped(tmp_path):
    """单个坏文件跳过不阻断整体。"""
    good = tmp_path / ".agents"
    good.mkdir()
    _role_file(good, "ok")
    bad = good / "bad.md"
    bad.write_text("没有 frontmatter\n", encoding="utf-8")
    known = load_all(tmp_path, check_tools=lambda n: True)
    assert "ok" in known
    assert "bad" not in known


def test_load_all_empty_dirs_ignored(tmp_path):
    """无任何角色目录 → 空 known（隔离内置，避免内置 architect 干扰）。"""
    ws = tmp_path / "empty"
    ws.mkdir()
    assert load_all(ws, builtin_root=tmp_path / "no_builtin",
                    user_root=tmp_path / "no_user") == {}


def test_load_all_unknown_tool_failfast(tmp_path):
    """白名单含不存在工具 → RoleError（启动 fail-fast）。"""
    ws = tmp_path / "ws"
    d = ws / ".agents"
    d.mkdir(parents=True)
    _role_file(d, "r", extra="tools: [read, not_a_tool]")
    with pytest.raises(RoleError):
        load_all(ws, check_tools=lambda n: n in {"read", "glob", "grep", "run"})


def test_load_all_no_check_tools(tmp_path):
    """不传 check_tools：白名单不校验，正常加载。"""
    ws = tmp_path / "ws"
    d = ws / ".agents"
    d.mkdir(parents=True)
    _role_file(d, "r", extra="tools: [read, not_a_tool]")
    known = load_all(ws)
    assert known["r"].tools == frozenset({"read", "not_a_tool"})
