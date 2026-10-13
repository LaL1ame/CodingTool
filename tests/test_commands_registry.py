"""T2 单测 —— 命令注册中心(F1/N1)。"""
import pytest

from codepilot.commands.registry import (
    CommandConflictError,
    CommandKind,
    CommandRegistry,
    CommandSpec,
)


def _spec(name, *, aliases=(), hidden=False):
    return CommandSpec(
        name=name, kind=CommandKind.LOCAL, desc="d",
        handler=lambda ctx, p: None, aliases=aliases, hidden=hidden,
    )


def test_register_and_resolve_name():
    r = CommandRegistry()
    r.register(_spec("clear", aliases=("cls",)))
    assert r.resolve("clear") is not None
    assert r.resolve("CLS") is not None        # 别名 + 大小写不敏感
    assert r.resolve("nope") is None


def test_register_duplicate_name_raises():
    r = CommandRegistry()
    r.register(_spec("clear"))
    with pytest.raises(CommandConflictError) as e:
        r.register(_spec("clear"))
    assert "clear" in str(e.value)


def test_register_clashes_with_existing_alias():
    r = CommandRegistry()
    r.register(_spec("clear", aliases=("cls",)))
    with pytest.raises(CommandConflictError) as e:
        r.register(_spec("cls"))              # 新命令名撞已有别名
    assert "cls" in str(e.value)


def test_assert_no_conflicts_catches_alias_aliasing():
    # register 即时查重已拦下绝大多数撞口;此处直接向内部注入冲突,
    # 单测启动兜底门 assert_no_conflicts 本身能在全量两两校验中检出不登记口的互撞。
    a = CommandSpec(name="a", kind=CommandKind.LOCAL, desc="d",
                    handler=lambda c, p: None, aliases=("x",))
    b = CommandSpec(name="b", kind=CommandKind.LOCAL, desc="d",
                    handler=lambda c, p: None, aliases=("_y",))
    r = CommandRegistry()
    r.register(a)
    r.register(b)
    # 绕过 register 的即时校验,直接注入一个共享 token 的假冲突
    r._specs.append(CommandSpec(name="x", kind=CommandKind.LOCAL, desc="d",
                                handler=lambda c, p: None))
    with pytest.raises(CommandConflictError) as e:
        r.assert_no_conflicts()
    assert "x" in str(e.value)


def test_all_sorted_and_hidden_excluded():
    r = CommandRegistry()
    r.register(_spec("zeta"))
    r.register(_spec("alpha", hidden=True))
    names = [s.name for s in r.all(include_hidden=True)]
    assert names == ["alpha", "zeta"]
    shown = [s.name for s in r.all(include_hidden=False)]
    assert shown == ["zeta"]


def test_candidates_prefix_hidden_and_dedup():
    r = CommandRegistry()
    r.register(_spec("plan", aliases=("build",)))
    r.register(_spec("permission", hidden=True))
    r.register(_spec("help"))
    got = r.candidates("")
    assert "plan" in got and "build" in got and "help" in got
    assert "permission" not in got            # 隐藏命令不参与补全
    assert got.count("plan") == 1             # alias 去重
    assert all(t.startswith("p") for t in r.candidates("p"))