"""A4 coordinator 双锁、工具面收窄与提示块。"""

from types import SimpleNamespace

from codepilot.teams.coordinator import (
    _WRITE_TOOLS,
    coordinator_context_block,
    coordinator_enabled,
    coordinator_tool_filter,
)


class _T:
    def __init__(self, name):
        self._n = name

    @property
    def name(self):
        return self._n


def _settings(cfg_on: bool):
    return SimpleNamespace(coordinator_enabled=cfg_on)


def test_two_locks_only():
    """四组合中仅 config+env 双开才生效。"""
    cases = [
        (True, "1", True, "config+env"),
        (True, "0", False, "config"),
        (False, "1", False, "env"),
        (False, "0", False, ""),
        (True, "true", True, "config+env"),
        (True, "on", True, "config+env"),
    ]
    for cfg, env, want, src in cases:
        s = coordinator_enabled(_settings(cfg), env={"CODEPILOT_COORDINATOR": env})
        assert s.enabled is want, (cfg, env)
        assert s.source == src


def test_env_missing_defaults_off():
    s = coordinator_enabled(_settings(True), env={})
    assert s.enabled is False and s.source == "config"
    s2 = coordinator_enabled(_settings(False), env={})
    assert s2.enabled is False and s2.source == ""


def test_tool_filter_removes_write_only_when_enabled():
    tools = [_T("write"), _T("edit"), _T("read"), _T("run"),
             _T("team_merge"), _T("grep"), _T("glob")]
    on = coordinator_enabled(_settings(True), env={"CODEPILOT_COORDINATOR": "1"})
    filtered = coordinator_tool_filter(tools, on)
    names = {t.name for t in filtered}
    assert names == {"read", "run", "team_merge", "grep", "glob"}
    assert names.isdisjoint(_WRITE_TOOLS)

    off = coordinator_enabled(_settings(False), env={})
    unfiltered = coordinator_tool_filter(tools, off)
    assert {t.name for t in unfiltered} == {t.name for t in tools}


def test_context_block_present_only_when_enabled():
    on = coordinator_enabled(_settings(True), env={"CODEPILOT_COORDINATOR": "1"})
    block = coordinator_context_block(on)
    assert "coordinator" in block and "team_merge" in block and "写文件" in block
    off = coordinator_enabled(_settings(False), env={})
    assert coordinator_context_block(off) == ""