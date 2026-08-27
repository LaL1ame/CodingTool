"""测试配置层 — AgentConfig 解析。"""

from codepilot.config import AgentConfig, load_agent_config


def _write(tmp_path, text: str):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_defaults_when_no_agent_section(tmp_path):
    p = _write(tmp_path, "providers:\n  - name: a\n    protocol: openai\n    model: m\n    api_key: k\n    base_url: http://x\n")
    assert load_agent_config(p) == AgentConfig()


def test_agent_section_parsed(tmp_path):
    p = _write(tmp_path, "agent:\n  max_rounds: 5\n  max_unknown: 3\n")
    cfg = load_agent_config(p)
    assert cfg.max_rounds == 5
    assert cfg.max_unknown == 3


def test_partial_agent_section_uses_defaults(tmp_path):
    p = _write(tmp_path, "agent:\n  max_rounds: 7\n")
    cfg = load_agent_config(p)
    assert cfg.max_rounds == 7
    assert cfg.max_unknown == 2


def test_missing_file_returns_defaults(tmp_path):
    assert load_agent_config(tmp_path / "nope.yaml") == AgentConfig()
