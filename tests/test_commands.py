"""命令解析(state14) —— 斜杠命令 + message/unknown 判定(真实 registry 驱动)。"""

from codepilot.commands import build_registry, parse_command


def _reg():
    return build_registry()


def test_plan_alone():
    r = parse_command("/plan", _reg())
    assert r.kind == "command" and r.spec.name == "plan" and r.params == ""


def test_plan_with_message():
    r = parse_command("/plan 帮我写个文件", _reg())
    assert r.kind == "command" and r.spec.name == "plan" and r.params == "帮我写个文件"


def test_plan_with_extra_spaces():
    r = parse_command("/plan   重构 app.py", _reg())
    assert r.kind == "command" and r.params == "重构 app.py"


def test_do_alone_and_with_message():
    assert parse_command("/do", _reg()).spec.name == "do"
    assert parse_command("/do 继续执行", _reg()).params == "继续执行"


def test_continue_alone():
    assert parse_command("/continue", _reg()).spec.name == "continue"


def test_memory_alone():
    assert parse_command("/memory", _reg()).spec.name == "memory"


def test_forget_with_keyword():
    assert parse_command("/forget vim", _reg()).params == "vim"
    assert parse_command("/forget  vim 键位 ", _reg()).params == "vim 键位"
    assert parse_command("/forget", _reg()).params == ""


def test_plain_message_is_message():
    r = parse_command("帮我写个文件", _reg())
    assert r.kind == "message" and r.spec is None


def test_unknown_slash_is_unknown():
    for t in ("/planx", "/unknown"):
        r = parse_command(t, _reg())
        assert r.kind == "unknown" and r.spec is None