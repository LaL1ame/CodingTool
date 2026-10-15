"""state16 T3：两级加载 + F10 集中校验全集。"""

import pytest

from codepilot.hooks.loader import load_hooks
from codepilot.hooks.model import HookError


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "proj.yaml", tmp_path / "user.yaml"


class TestLoadAndMerge:
    def test_missing_files_ignored(self, paths):
        assert load_hooks(*paths) == []

    def test_empty_file_ignored(self, paths, tmp_path):
        _write(paths[0], "")
        _write(paths[1], "hooks: []\n")
        assert load_hooks(*paths) == []

    def test_two_level_merge_order(self, paths):
        # 用户级在前、项目级在后
        _write(paths[1], """
hooks:
  - event: session_start
    action: {type: prompt, text: user-level}
""")
        _write(paths[0], """
hooks:
  - event: session_start
    action: {type: prompt, text: proj-level}
""")
        rules = load_hooks(*paths)
        assert [r.action.fields["text"] for r in rules] == ["user-level", "proj-level"]
        assert [r.ordinal for r in rules] == [0, 0]
        assert rules[0].source != rules[1].source

    def test_full_parse(self, paths):
        _write(paths[0], """
hooks:
  - event: tool_pre
    if:
      all:
        - tool: run
        - command: "!git *"
    action:
      type: command
      cmd: python guard.py
      timeout: 10
      once: true
  - event: tool_post
    if: {tool: write}
    action: {type: command, cmd: "ruff format {file_path}", async: true}
""")
        rules = load_hooks(*paths)
        assert len(rules) == 2
        r0 = rules[0]
        assert r0.event == "tool_pre"
        assert r0.condition.mode == "all"
        assert [(c.field, c.pattern) for c in r0.condition.items] == [
            ("tool", "run"), ("command", "!git *")]
        assert r0.action.type == "command"
        assert r0.action.fields == {"cmd": "python guard.py"}
        assert r0.action.timeout == 10.0
        assert r0.action.once is True
        assert r0.action.is_async is False
        r1 = rules[1]
        assert r1.condition.mode == "all"  # 简写 {tool: write} → all 单条
        assert r1.condition.items[0].field == "tool"
        assert r1.action.is_async is True

    def test_no_condition_is_none(self, paths):
        _write(paths[0], """
hooks:
  - event: compact
    action: {type: prompt, text: compacted}
""")
        (rule,) = load_hooks(*paths)
        assert rule.condition is None


class TestValidation:
    """F10 全集：逐条触发 HookError。"""

    def _load(self, paths, yaml_text):
        _write(paths[0], yaml_text)
        with pytest.raises(HookError):
            load_hooks(*paths)

    def test_bad_yaml(self, paths):
        self._load(paths, "hooks: [unclosed\n")

    def test_hooks_not_list(self, paths):
        self._load(paths, "hooks: {a: 1}\n")

    def test_missing_event(self, paths):
        self._load(paths, "hooks:\n  - action: {type: prompt, text: x}\n")

    def test_unknown_event(self, paths):
        self._load(paths, "hooks:\n  - event: tool_exec\n"
                          "    action: {type: prompt, text: x}\n")

    def test_missing_action(self, paths):
        self._load(paths, "hooks:\n  - event: compact\n")

    def test_unknown_action_type(self, paths):
        self._load(paths, "hooks:\n  - event: compact\n"
                          "    action: {type: email, to: a@b.c}\n")

    def test_all_any_mixed(self, paths):
        self._load(paths, "hooks:\n  - event: user_message\n"
                          "    if: {all: [{text: a}], any: [{text: b}]}\n"
                          "    action: {type: prompt, text: x}\n")

    def test_empty_condition_list(self, paths):
        self._load(paths, "hooks:\n  - event: user_message\n"
                          "    if: {all: []}\n"
                          "    action: {type: prompt, text: x}\n")

    def test_invalid_regex(self, paths):
        self._load(paths, "hooks:\n  - event: tool_pre\n"
                          "    if: {command: '/[unclosed/'}\n"
                          "    action: {type: command, cmd: x}\n")

    def test_invalid_regex_negated(self, paths):
        # ! 前缀剥离后仍应检出正则非法
        self._load(paths, "hooks:\n  - event: tool_pre\n"
                          "    if: {command: '!/[unclosed/'}\n"
                          "    action: {type: command, cmd: x}\n")

    def test_command_missing_cmd(self, paths):
        self._load(paths, "hooks:\n  - event: tool_post\n"
                          "    action: {type: command}\n")

    def test_http_missing_url(self, paths):
        self._load(paths, "hooks:\n  - event: tool_post\n"
                          "    action: {type: http, method: POST}\n")

    def test_prompt_missing_text(self, paths):
        self._load(paths, "hooks:\n  - event: session_start\n"
                          "    action: {type: prompt}\n")

    def test_tool_pre_async_forbidden(self, paths):
        self._load(paths, "hooks:\n  - event: tool_pre\n"
                          "    action: {type: command, cmd: x, async: true}\n")

    def test_unknown_field_on_non_tool_event(self, paths):
        self._load(paths, "hooks:\n  - event: user_message\n"
                          "    if: {tool: run}\n"
                          "    action: {type: prompt, text: x}\n")

    def test_tool_event_param_field_allowed(self, paths):
        # tool 事件的参数键（command/file_path/任意 MCP 参数）放开不校验
        _write(paths[0], "hooks:\n  - event: tool_pre\n"
                         "    if: {some_mcp_param: 'x*'}\n"
                         "    action: {type: command, cmd: y}\n")
        (rule,) = load_hooks(*paths)
        assert rule.condition.items[0].field == "some_mcp_param"

    def test_bad_timeout(self, paths):
        self._load(paths, "hooks:\n  - event: tool_post\n"
                          "    action: {type: command, cmd: x, timeout: -1}\n")

    def test_subagent_placeholder_loads(self, paths):
        _write(paths[0], "hooks:\n  - event: stop\n"
                         "    action: {type: subagent}\n")
        (rule,) = load_hooks(*paths)
        assert rule.action.type == "subagent"
