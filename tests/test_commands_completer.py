"""T5 单测 —— Tab 补全候选(F6)。"""
from codepilot.commands.completer import make_completer
from codepilot.commands.registry import CommandKind, CommandRegistry, CommandSpec


def _reg():
    r = CommandRegistry()
    r.register(CommandSpec(name="plan", kind=CommandKind.UI, desc="d",
                           handler=lambda c, p: None, aliases=("build",)))
    r.register(CommandSpec(name="permission", kind=CommandKind.UI, desc="d",
                           handler=lambda c, p: None, hidden=True))
    r.register(CommandSpec(name="help", kind=CommandKind.LOCAL, desc="d",
                           handler=lambda c, p: None))
    return r


def test_slash_lists_all():
    c = make_completer(_reg())
    got = c("/")
    assert "plan" in got and "help" in got
    assert "permission" not in got   # 隐藏不参与


def test_prefix_narrows():
    c = make_completer(_reg())
    got = c("/p")
    assert got == ["plan"]
    assert "help" not in got


def test_alias_in_candidates_dedup():
    c = make_completer(_reg())
    got = c("/build")
    # "build" 是别名,候选返回其规范名去重后的匹配
    assert "build" in got


def test_not_command_returns_empty():
    c = make_completer(_reg())
    assert c("hello /p") == []     # 行首非斜杠
    assert c("") == []