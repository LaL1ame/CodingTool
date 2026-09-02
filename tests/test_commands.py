"""测试命令解析 — /exit /plan /do 及带消息的元命令。"""

from codepilot.commands import parse_command


def test_exit_alone():
    assert parse_command("/exit") == ("exit", "")


def test_plan_alone():
    assert parse_command("/plan") == ("plan", "")


def test_plan_with_message():
    assert parse_command("/plan 帮我写个文件") == ("plan", "帮我写个文件")


def test_plan_with_extra_spaces():
    assert parse_command("/plan   重构 app.py") == ("plan", "重构 app.py")


def test_do_alone():
    assert parse_command("/do") == ("do", "")


def test_do_with_message():
    assert parse_command("/do 继续执行") == ("do", "继续执行")


def test_plain_message_is_not_command():
    assert parse_command("帮我写个文件") == (None, "帮我写个文件")


def test_unknown_slash_is_not_command():
    assert parse_command("/planx") == (None, "/planx")
    assert parse_command("/unknown") == (None, "/unknown")
