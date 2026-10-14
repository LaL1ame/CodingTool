"""T6 单测 —— 内置命令注册完整性(F7)。"""
from codepilot.commands.builtins import build_registry
from codepilot.commands.registry import CommandKind


def test_builtin_full_set_and_no_conflicts():
    r = build_registry()
    r.assert_no_conflicts()                 # 启动校验:不撞
    names = {s.name for s in r.all()}
    expected = {"help", "exit", "plan", "do", "mode", "auto", "manual", "strict",
                "edits", "compact", "continue", "memory", "forget", "clear",
                "session", "permission", "status", "reload"}
    assert names == expected  # state15: /review 移交内置 review Skill(非内置命令)


def test_kinds_assignment():
    r = build_registry()
    kinds = {s.name: s.kind for s in r.all()}
    assert kinds["help"] is CommandKind.LOCAL
    assert kinds["status"] is CommandKind.LOCAL
    assert kinds["session"] is CommandKind.LOCAL
    assert kinds["plan"] is CommandKind.UI
    assert kinds["clear"] is CommandKind.UI
    assert kinds["permission"] is CommandKind.UI


def test_aliases_present():
    r = build_registry()
    assert r.resolve("cls").name == "clear"
    assert r.resolve("?") .name == "help"
    assert r.resolve("quit").name == "exit"


def test_no_hidden_in_default_view():
    r = build_registry()
    shown = r.all(include_hidden=False)
    assert all(not s.hidden for s in shown)
    assert len(shown) == len(r.all(include_hidden=True))