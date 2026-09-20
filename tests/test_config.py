"""测试配置层 — AgentConfig / LogConfig 解析。"""

from codepilot.config import AgentConfig, LogConfig, load_agent_config, load_log_config


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


# ---- LogConfig ----

def test_log_defaults_no_section(tmp_path):
    p = _write(tmp_path, "providers:\n  - name: a\n    protocol: openai\n    model: m\n    api_key: k\n    base_url: http://x\n")
    assert load_log_config(p) == LogConfig()


def test_log_section_parsed(tmp_path):
    p = _write(tmp_path, "log:\n  file: logs/app.log\n  level: DEBUG\n")
    cfg = load_log_config(p)
    assert cfg.file == "logs/app.log"
    assert cfg.level == "DEBUG"


def test_log_default_level(tmp_path):
    p = _write(tmp_path, "log:\n  file: logs/a.log\n")
    cfg = load_log_config(p)
    assert cfg.file == "logs/a.log"
    assert cfg.level == "INFO"


def test_log_missing_file_returns_defaults(tmp_path):
    assert load_log_config(tmp_path / "nope.yaml") == LogConfig()
