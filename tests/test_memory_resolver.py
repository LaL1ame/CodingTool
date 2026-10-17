"""state13 F1/F2 单元测试 —— 指令三层发现 + @include 展开。"""
from pathlib import Path

from codepilot.memory.resolver import (
    discover_instruction_roots,
    expand_includes,
    load_instructions,
)


def _write(tmp: Path, rel: str, content: str) -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ── F1 三层发现 ──────────────────────────────────────────────

def test_discover_three_levels_ordered(tmp_path: Path):
    ws = tmp_path / "ws"
    _write(ws, ".rules/a.md", "A")
    _write(ws, "lib/.rules/b.md", "B")         # 深度 2
    _write(ws, "lib/sub/.rules/c.md", "C")     # 深度 3
    user = tmp_path / "user" / ".rules"
    _write(user, "u.md", "U")

    roots = discover_instruction_roots(ws, user_root=user)
    names = [r.name for r in roots]
    # 用户级最先;项目各级按深度升序(根=1位,深目录在后)
    assert names[0] == ".rules"
    assert (ws / "lib/.rules").resolve() in [r.resolve() for r in roots]
    assert (ws / "lib/sub/.rules").resolve() in [r.resolve() for r in roots]
    # 深度断言:项目根 .rules 在 lib/ 之前,lib/ 在 lib/sub/ 之前
    rs = [r.resolve() for r in roots]
    assert rs.index((ws / ".rules").resolve()) < rs.index((ws / "lib/.rules").resolve())
    assert rs.index((ws / "lib/.rules").resolve()) < rs.index((ws / "lib/sub/.rules").resolve())


def test_discover_ignores_git(tmp_path: Path):
    ws = tmp_path / "ws"
    _write(ws, ".git/.rules/x.md", "X")   # 该深度应被剪枝
    _write(ws, ".rules/a.md", "A")
    roots = discover_instruction_roots(ws, user_root=tmp_path / "nouser")
    rs = [r.resolve() for r in roots]
    assert (ws / ".git/.rules").resolve() not in rs
    assert (ws / ".rules").resolve() in rs


def test_no_rules_returns_empty(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert discover_instruction_roots(ws, user_root=tmp_path / "nouser") == []


# ── F2 @include ──────────────────────────────────────────────

def test_include_expands_inline(tmp_path: Path):
    base = _write(tmp_path, ".rules/root.md", "@include child.md\nROOT")
    _write(tmp_path, ".rules/child.md", "CHILD")
    out = expand_includes(base.read_text(encoding="utf-8"), base.parent)
    assert "CHILD" in out
    assert "ROOT" in out


def test_include_depth_limit_skips(tmp_path: Path):
    base = _write(tmp_path, ".rules/a.md", "@include b.md\nA")
    _write(tmp_path, ".rules/b.md", "@include c.md\nB")
    _write(tmp_path, ".rules/c.md", "C")
    text = base.read_text(encoding="utf-8")
    out = expand_includes(text, base.parent, depth=3, depth_limit=4)
    assert "B" in out and "A" in out
    assert "C" not in out  # 嵌套到第 4 层被跳过


def test_include_cycle_terminates(tmp_path: Path):
    _write(tmp_path, ".rules/a.md", "@include b.md\nA")
    _write(tmp_path, ".rules/b.md", "@include a.md\nB")
    text = (tmp_path / ".rules/a.md").read_text(encoding="utf-8")
    out = expand_includes(text, tmp_path / ".rules")
    assert "A" in out and "B" in out  # 不无限递归,visited 防环


def test_include_escape_blocked(tmp_path: Path):
    base = _write(tmp_path, "ws/.rules/a.md", "@include ../secret.md\nA")
    _write(tmp_path, "ws/secret.md", "SECRET")
    text = base.read_text(encoding="utf-8")
    out = expand_includes(text, base.parent)
    assert "SECRET" not in out   # 逃逸被拦
    assert "A" in out            # 主文件保留


def test_include_missing_file_skips_rest_kept(tmp_path: Path):
    base = _write(tmp_path, ".rules/a.md", "@include nope.md\nA")
    out = expand_includes(base.read_text(encoding="utf-8"), base.parent)
    assert "@include" not in out
    assert "A" in out


# ── load_instructions 拼接 ───────────────────────────────────

def test_load_instructions_concats_by_root(tmp_path: Path):
    ws = tmp_path / "ws"
    _write(ws, ".rules/a.md", "@include a1.md\nROOT")
    _write(ws, ".rules/a1.md", "R1")
    _write(ws, "lib/.rules/b.md", "SUB")
    text = load_instructions(ws, user_root=tmp_path / "nouser")
    assert "ROOT" in text and "R1" in text and "SUB" in text
    # 顺序:项目根在前,更深子目录在后
    assert text.index("ROOT") < text.index("SUB")


def test_discover_ignores_codepilot_worktrees(tmp_path: Path):
    ws = tmp_path / "ws"
    _write(ws, ".codepilot/worktrees/x/.rules/hidden.md", "H")
    _write(ws, ".rules/visible.md", "V")
    roots = discover_instruction_roots(ws, user_root=tmp_path / "nouser")
    assert (ws / ".rules").resolve() in [r.resolve() for r in roots]
    assert all(".codepilot" not in r.parts for r in roots)


def test_load_instructions_empty_and_bad_file(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert load_instructions(ws, user_root=tmp_path / "nouser") == ""
    _write(ws, ".rules/ok.md", "OK")
    assert "OK" in load_instructions(ws, user_root=tmp_path / "nouser")