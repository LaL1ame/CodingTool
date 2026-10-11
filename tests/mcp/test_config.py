"""config.py 的 MCP 段:两层合并(AC1) + ${VAR} 展开(AC8) + 未定义跳过(N4)。

均用 tmp_path 写 YAML,不污染真实配置。
"""

import logging

import pytest

from codepilot.config import (
    ConfigError, McpServerSpec, expand_env, load_mcp_config,
)


def _write_yaml(path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


# —— expand_env ——

def test_expand_env_replaces_known_var():
    assert expand_env("https://${HOST}:${PORT}/mcp",
                      {"HOST": "api.example.com", "PORT": "443"}) == (
        "https://api.example.com:443/mcp")


def test_expand_env_left_intact_when_no_marker():
    assert expand_env("no marker here", {}) == "no marker here"


def test_expand_env_undefined_raises():
    with pytest.raises(ConfigError):
        expand_env("x=${MISSING_VAR_XYZ}", {"OTHER": "1"})


# —— 两层合并(AC1) ——

def test_merge_project_overrides_user_same_name(tmp_path):
    user = tmp_path / "user.yaml"
    proj = tmp_path / "proj.yaml"
    _write_yaml(user, "mcp:\n  api:\n    type: http\n    url: http://user/api\n")
    _write_yaml(proj, "mcp:\n  api:\n    type: http\n    url: http://proj/api\n")
    specs = load_mcp_config(user, proj)
    assert len(specs) == 1
    assert specs[0].url == "http://proj/api"  # 项目盖用户


def test_merge_distinct_names_both_kept(tmp_path):
    user = tmp_path / "u.yaml"
    proj = tmp_path / "p.yaml"
    _write_yaml(user, "mcp:\n  a:\n    type: stdio\n    command: ca\n")
    _write_yaml(proj, "mcp:\n  b:\n    type: stdio\n    command: cb\n")
    specs = load_mcp_config(user, proj)
    names = {s.name for s in specs}
    assert names == {"a", "b"}


def test_merge_only_in_user_kept(tmp_path):
    user = tmp_path / "u.yaml"
    proj = tmp_path / "p.yaml"
    _write_yaml(user, "mcp:\n  a:\n    type: http\n    url: http://u\n")
    _write_yaml(proj, "mcp:\n  b:\n    type: http\n    url: http://p\n")
    assert {s.name for s in load_mcp_config(user, proj)} == {"a", "b"}


# —— ${VAR} 展开 + 未定义跳过(AC8/N4) ——

def test_expand_in_command(tmp_path):
    u = tmp_path / "u.yaml"
    _write_yaml(u, "mcp:\n  s:\n    type: stdio\n    command: ${NODE}\n    env:\n      KEY: ${TOK}\n")
    specs = load_mcp_config(u, None, {"NODE": "node", "TOK": "abc"})
    assert specs[0].command == "node"
    assert specs[0].env == {"KEY": "abc"}


def test_undefined_var_skips_server_and_warns(tmp_path, caplog):
    u = tmp_path / "u.yaml"
    # 好的 server 保留;含未定义变量的 server 被跳过
    _write_yaml(u, (
        "mcp:\n"
        "  ok:\n"
        "    type: stdio\n"
        "    command: node\n"
        "  bad:\n"
        "    type: stdio\n"
        "    command: ${NOPE}\n"
    ))
    with caplog.at_level(logging.WARNING, logger="codepilot.config"):
        specs = load_mcp_config(u, None)
    assert [s.name for s in specs] == ["ok"]
    assert any("bad" in r.message for r in caplog.records)


def test_read_only_flag_parsed(tmp_path):
    u = tmp_path / "u.yaml"
    _write_yaml(u, "mcp:\n  s:\n    type: http\n    url: http://x\n    read_only: true\n")
    assert load_mcp_config(u, None)[0].read_only is True