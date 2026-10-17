"""SubAgent 多层防线纯函数测试（F7/D2/D8/D9）。

覆盖：白名单∪系统 / 黑名单 / agent 禁（can_delegate ∧ depth<max_depth）/ 后台只读 / plan 继承。
"""

from codepilot.subagents.model import AgentRole
from codepilot.subagents.runner import build_fork_tool_filter, build_tool_filter


class _T:
    def __init__(self, name, side_effect=False, system_exempt=False):
        self._n = name
        self._s = side_effect
        self._x = system_exempt

    @property
    def name(self): return self._n
    @property
    def side_effect(self): return self._s
    @property
    def system_exempt(self): return self._x


def _tools(*names):
    return [_T(n) for n in names]


def _role(**kw):
    return AgentRole(name="r", description="d", body="b", **kw)


def _names(ts):
    return {t.name for t in ts}


def test_whitelist_keeps_system_exempt():
    f = build_tool_filter(role=_role(tools=frozenset({"read"})), depth=0,
                          max_depth=1, background=False, inherit_plan=False)
    out = f([_T("read"), _T("run"), _T("load_skill", system_exempt=True)])
    # 白名单含 read；load_skill 是系统工具（system_exempt）→ 保留；run 不在白名单 → 滤掉
    assert _names(out) == {"read", "load_skill"}


def test_agent_removed_even_if_in_whitelist():
    """全局禁止优先：默认角色 can_delegate=False 时，agent 即使在白名单也被 agent 禁层移除（F7）。"""
    f = build_tool_filter(role=_role(tools=frozenset({"read", "agent"})), depth=0,
                          max_depth=1, background=False, inherit_plan=False)
    out = f([_T("read"), _T("agent", system_exempt=True)])
    assert _names(out) == {"read"}


def test_whitelist_empty_no_constraint():
    f = build_tool_filter(role=_role(), depth=0, max_depth=1,
                          background=False, inherit_plan=False)
    assert _names(f(_tools("read", "run"))) == {"read", "run"}


def test_blacklist_removes():
    f = build_tool_filter(role=_role(blocked=frozenset({"run", "agent"})), depth=0,
                          max_depth=1, background=False, inherit_plan=False)
    assert _names(f(_tools("read", "run", "agent"))) == {"read"}


def test_agent_blocked_default():
    """默认 can_delegate=False → agent 工具被禁（全局禁止，F7）。"""
    f = build_tool_filter(role=_role(), depth=0, max_depth=1,
                          background=False, inherit_plan=False)
    assert _names(f(_tools("read", "agent"))) == {"read"}


def test_agent_visible_when_can_delegate_and_depth_ok():
    """can_delegate=True 且 depth < max_depth → agent 保留。"""
    f = build_tool_filter(role=_role(can_delegate=True), depth=1, max_depth=2,
                          background=False, inherit_plan=False)
    assert _names(f(_tools("read", "agent"))) == {"read", "agent"}


def test_agent_hidden_at_max_depth():
    """can_delegate=True 但 depth >= max_depth → 仍禁 agent（深度上限）。"""
    f = build_tool_filter(role=_role(can_delegate=True), depth=2, max_depth=2,
                          background=False, inherit_plan=False)
    assert _names(f(_tools("read", "agent"))) == {"read"}


def test_background_readonly():
    """后台白名单：side_effect 工具被滤（只读子集，F7）。"""
    f = build_tool_filter(role=_role(), depth=0, max_depth=1,
                          background=True, inherit_plan=False)
    out = f([_T("read"), _T("run", side_effect=True), _T("write", side_effect=True)])
    assert _names(out) == {"read"}


def test_inherit_plan_readonly():
    """plan 继承：父在 plan 时子 Agent 只读（D8）。"""
    f = build_tool_filter(role=_role(), depth=0, max_depth=1,
                          background=False, inherit_plan=True)
    out = f([_T("read"), _T("run", side_effect=True)])
    assert _names(out) == {"read"}


def test_fork_filter_inherits_parent_and_readonly():
    """Fork：继承父工具集 + 禁 agent + 后台只读。"""
    f = build_fork_tool_filter(frozenset({"read", "grep", "run"}), background=True)
    out = f([_T("read"), _T("run", side_effect=True), _T("agent"), _T("write", side_effect=True)])
    # run/write 被后台只读滤掉；agent 被禁；read 保留
    assert _names(out) == {"read"}
