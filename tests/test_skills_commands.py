"""F8: Skill 命令动态注册、撞名胜出、replace 热更新、/clear 联动复位。"""

from codepilot.commands import CommandRegistry, CommandSpec, CommandKind
from codepilot.skills.manager import SkillManager
from codepilot.skills.model import Skill


def _skill(name, *, mode="shared", tools=("read",)):
    return Skill(name=name, description=f"{name}-desc", body="do {input}",
                 tools=frozenset(tools), mode=mode)


def _skill_spec(skill, handler):
    """与 App._register_skill_command 一致的 mapping：isolated→LOCAL，shared→UI。"""
    return CommandSpec(
        name=skill.name,
        kind=CommandKind.LOCAL if skill.mode == "isolated" else CommandKind.UI,
        desc=skill.description, handler=handler,
    )


def test_skill_command_registers_and_resolves():
    r = CommandRegistry()
    s = _skill("commit")
    r.register(_skill_spec(s, lambda c, p: None))
    assert r.resolve("commit") is not None
    assert r.resolve("commit").desc == "commit-desc"


def test_skill_command_overrides_builtin_by_replace():
    r = CommandRegistry()
    # 预置同名「旧」命令(如旧 PROMPT /review)
    r.register(CommandSpec(name="review", kind=CommandKind.PROMPT,
                           desc="old", handler=lambda c, p: None))
    s = _skill("review", mode="isolated")
    r.register(_skill_spec(s, lambda c, p: None), replace=True)
    assert r.resolve("review").desc == "review-desc"
    assert r.resolve("review").kind is CommandKind.LOCAL  # isolated → LOCAL 执行


def test_hot_reload_replace_keeps_single_entry():
    r = CommandRegistry()
    s = _skill("commit")
    r.register(_skill_spec(s, lambda c, p: None))
    r.register(_skill_spec(s, lambda c, p: None), replace=True)  # 热更新重注册
    matches = [x for x in r.all() if x.name == "commit"]
    assert len(matches) == 1


def test_non_replace_conflict_still_raises():
    from codepilot.commands.registry import CommandConflictError
    r = CommandRegistry()
    r.register(_skill_spec(_skill("a"), lambda c, p: None))
    try:
        r.register(_skill_spec(_skill("a"), lambda c, p: None))  # 非 replace 撞名
        assert False, "should raise"
    except CommandConflictError:
        pass


def test_manager_load_triggers_on_register_command():
    calls = []
    m = SkillManager({"job": _skill("job")},
                     on_register_command=lambda n, h: calls.append(n))
    m.load("job")
    assert calls == ["job"]   # 加载即触发命令注册回调


def test_deactivate_resets_whitelist_keeps_command_registered():
    r = CommandRegistry()
    m = SkillManager({"commit": _skill("commit", tools=("read", "run"))})
    # App 在启动/加载时注册命令;模拟一次
    r.register(_skill_spec(m.require("commit"), m.command_handler("commit")))
    m.load("commit")
    assert m.active_whitelist() == {"read", "run"}
    m.deactivate_all()                       # /clear 联动
    assert m.active_whitelist() is None
    assert r.resolve("commit") is not None   # 命令保留(可再次触发)
    assert not m.active                       # 激活态已复位


def test_isolated_vs_shared_kind_mapping():
    r = CommandRegistry()
    r.register(_skill_spec(_skill("shared"), lambda c, p: None))
    r.register(_skill_spec(_skill("iso", mode="isolated"), lambda c, p: None))
    assert r.resolve("shared").kind is CommandKind.UI
    assert r.resolve("iso").kind is CommandKind.LOCAL