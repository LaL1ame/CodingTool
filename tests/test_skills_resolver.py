"""F2/F3: 三级发现、优先级覆盖、目录型、单文件故障隔离、白名单 fail-fast。"""

from pathlib import Path

import pytest

from codepilot.skills.model import SkillError
from codepilot.skills.resolver import discover_skill_dirs, load_all


def _md(path: Path, name: str, desc: str, body: str = "body", **extra):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"name: {name}", f"description: {desc}", f"mode: {extra.get('mode', 'shared')}"]
    if "tools" in extra:
        lines.append(f"tools: [{extra['tools']}]")
    path.write_text("---\n" + "\n".join(lines) + "\n---\n" + body, encoding="utf-8")
    return path


# builtin_root 参数本身就是装 .md 的 Skill 根目录，直接放文件。
def test_three_tier_priority_project_wins(tmp_path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    ws = tmp_path / "ws"
    _md(builtin / "base.md", "base", "builtin-ver")
    _md(user / "base.md", "base", "user-ver")
    _md(ws / ".skills" / "base.md", "base", "project-ver")

    known = load_all(ws, user_root=user, builtin_root=builtin)
    assert known["base"].description == "project-ver"


def test_user_overrides_builtin(tmp_path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    ws = tmp_path / "ws"
    _md(builtin / "base.md", "base", "builtin-ver")
    _md(user / "base.md", "base", "user-ver")
    ws.mkdir(exist_ok=True)
    known = load_all(ws, user_root=user, builtin_root=builtin)
    assert known["base"].description == "user-ver"


def test_builtin_only(tmp_path):
    builtin = tmp_path / "builtin"
    ws = tmp_path / "ws"
    _md(builtin / "b.md", "b", "only")
    ws.mkdir(exist_ok=True)
    known = load_all(ws, builtin_root=builtin)
    assert known["b"].description == "only"


def test_directory_type_skill(tmp_path):
    builtin = tmp_path / "builtin"
    ws = tmp_path / "ws"
    review_dir = builtin / "review"
    _md(review_dir / "SKILL.md", "review", "dir-based", mode="isolated")
    (review_dir / "template.md").write_text("# t", encoding="utf-8")
    ws.mkdir(exist_ok=True)

    known = load_all(ws, builtin_root=builtin)
    assert known["review"].mode == "isolated"
    assert any(p.name == "template.md" for p in known["review"].resources)


def test_broken_file_skipped_still_returns_others(tmp_path):
    builtin = tmp_path / "builtin"
    ws = tmp_path / "ws"
    _md(builtin / "good.md", "good", "ok")
    (builtin / "bad.md").write_text("no frontmatter", encoding="utf-8")
    ws.mkdir(exist_ok=True)
    known = load_all(ws, builtin_root=builtin)
    assert "good" in known
    assert "bad" not in known


def test_check_tools_fail_fast_on_missing(tmp_path):
    builtin = tmp_path / "builtin"
    ws = tmp_path / "ws"
    _md(builtin / "s.md", "s", "d", tools="read, nosuchtool")
    ws.mkdir(exist_ok=True)
    with pytest.raises(SkillError):
        load_all(ws, builtin_root=builtin,
                 check_tools=lambda n: n in {"read", "grep", "run"})


def test_discover_orders_builtin_user_project(tmp_path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    ws = tmp_path / "ws"
    builtin.mkdir(parents=True, exist_ok=True)
    _md(user / "a.md", "a", "d")
    _md(ws / ".skills" / "b.md", "b", "d")

    dirs = discover_skill_dirs(ws, user_root=user, builtin_root=builtin)
    assert dirs[0] == builtin
    assert dirs[1] == user
    assert dirs[2] == (ws / ".skills")