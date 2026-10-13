"""T3 单测 —— 解析器(F2)。"""
from codepilot.commands.builtins import build_registry
from codepilot.commands.parser import parse_command


def _reg():
    return build_registry()


def test_blank():
    for t in ["", "   ", "\t\n"]:
        assert parse_command(t, _reg()).kind == "blank"


def test_message():
    for t in ["hello", "你好", "  hi there  ", "what is /plan like"]:
        r = parse_command(t, _reg())
        assert r.kind == "message"
        assert r.spec is None


def test_command_hit():
    r = parse_command("/plan", _reg())
    assert r.kind == "command"
    assert r.spec is not None
    assert r.spec.name == "plan"


def test_command_with_params_and_whitespace():
    r = parse_command("/forget   负责包  ", _reg())
    assert r.kind == "command"
    assert r.spec.name == "forget"
    assert r.params == "负责包"


def test_case_insensitive():
    r = parse_command("/CLEAR", _reg())
    assert r.kind == "command"
    assert r.spec.name == "clear"


def test_alias_hit():
    r = parse_command("/cls", _reg())
    assert r.kind == "command"
    assert r.spec.name == "clear"


def test_unknown_and_bare_slash():
    for t in ["/notacmd", "/", "/ ", "/  "] :
        r = parse_command(t, _reg())
        assert r.kind == "unknown"
        assert r.spec is None