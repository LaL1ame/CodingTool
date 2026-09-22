"""正交模式状态机 modes.py 单测。"""
import pytest

from codepilot.modes import ModeState, ReleasePolicy, ToolAxis


def test_default_state():
    m = ModeState()
    assert m.tool_axis is ToolAxis.BUILD
    assert m.release is ReleasePolicy.MANUAL
    assert m.accept_edits is False


def test_cycle_tool_axis_alternates():
    m = ModeState()
    assert m.cycle_tool_axis() is ToolAxis.PLAN
    assert m.cycle_tool_axis() is ToolAxis.BUILD
    assert m.cycle_tool_axis() is ToolAxis.PLAN


def test_set_axis_and_release():
    m = ModeState()
    m.set_axis(ToolAxis.PLAN)
    assert m.tool_axis is ToolAxis.PLAN
    m.set_release(ReleasePolicy.AUTO)
    assert m.release is ReleasePolicy.AUTO
    # release → PermissionMode 名映射
    assert m.release_permission_name() == "PERMISSIVE"
    m.set_release(ReleasePolicy.STRICT)
    assert m.release_permission_name() == "STRICT"
    m.set_release(ReleasePolicy.MANUAL)
    assert m.release_permission_name() == "DEFAULT"


def test_toggle_accept_edits():
    m = ModeState()
    assert m.toggle_accept_edits(True) is True
    assert m.accept_edits is True
    assert m.toggle_accept_edits() is False  # 无参翻转
    assert m.accept_edits is False


@pytest.mark.parametrize("policy,perm", [
    (ReleasePolicy.MANUAL, "DEFAULT"),
    (ReleasePolicy.AUTO, "PERMISSIVE"),
    (ReleasePolicy.STRICT, "STRICT"),
])
def test_release_to_permission_mapping_table(policy, perm):
    assert next(m.release_permission_name() for m in [ModeState(release=policy)]) == perm


def test_snapshot_restore_roundtrip():
    m = ModeState(tool_axis=ToolAxis.PLAN, release=ReleasePolicy.STRICT,
                  accept_edits=True)
    snap = m.snapshot()
    assert snap == {"tool_axis": "plan", "release": "strict", "accept_edits": True}
    n = ModeState()
    n.restore(snap)
    assert n.tool_axis is ToolAxis.PLAN
    assert n.release is ReleasePolicy.STRICT
    assert n.accept_edits is True


def test_restore_defaults_when_empty():
    n = ModeState()
    n.restore({})
    assert n.tool_axis is ToolAxis.BUILD
    assert n.release is ReleasePolicy.MANUAL
    assert n.accept_edits is False