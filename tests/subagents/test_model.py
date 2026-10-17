"""SubAgent 数据模型测试：AgentRole frozen/默认值、SubAgentTask 可变字段。"""

from codepilot.subagents.model import AgentRole, RoleError, SubAgentTask


def test_agent_role_defaults():
    r = AgentRole(name="reviewer", description="代码审查", body="你是审查员")
    assert r.tools == frozenset()
    assert r.blocked == frozenset()
    assert r.model is None
    assert r.max_rounds is None
    assert r.permission is None
    assert r.can_delegate is False
    assert r.path is None
    assert r.ordinal == 0


def test_agent_role_is_frozen():
    r = AgentRole(name="x", description="d", body="b")
    try:
        r.name = "y"
    except Exception as e:  # noqa: BLE001 - 冻结后赋值必然抛异常
        assert isinstance(e, (AttributeError, Exception))
    else:
        raise AssertionError("frozen dataclass 不应允许字段赋值")


def test_agent_role_full_fields():
    r = AgentRole(
        name="architect", description="架构师", body="b",
        tools=frozenset({"read", "glob"}), blocked=frozenset({"run"}),
        model="claude-sonnet-4-6", max_rounds=5, permission="strict",
        can_delegate=True,
    )
    assert r.tools == frozenset({"read", "glob"})
    assert r.blocked == frozenset({"run"})
    assert r.model == "claude-sonnet-4-6"
    assert r.max_rounds == 5
    assert r.permission == "strict"
    assert r.can_delegate is True


def test_subagent_task_mutable():
    t = SubAgentTask(task_id=1, kind="defined", role="architect",
                     status="running", prompt="审查设计")
    assert t.status == "running"
    t.status = "done"          # 非 frozen，字段可写
    t.summary = "结论摘要"
    assert t.status == "done"
    assert t.summary == "结论摘要"


def test_role_error_subclass():
    assert issubclass(RoleError, Exception)
