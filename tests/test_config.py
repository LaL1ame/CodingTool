"""配置层测试 —— YAML 加载、校验。"""

import tempfile
from pathlib import Path

import pytest

from mewcode.config import ConfigError, ProviderConfig, load_config


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ---- 合法配置 --------------------------------------------------------------

def test_single_provider() -> None:
    d = Path(tempfile.mkdtemp())
    cfg = d / "config.yaml"
    _write_yaml(
        cfg,
        """\
providers:
  - name: Test
    protocol: anthropic
    model: claude-3
    api_key: sk-test
    base_url: https://api.anthropic.com
    thinking: true
""",
    )
    configs = load_config(cfg)
    assert len(configs) == 1
    c = configs[0]
    assert c.name == "Test"
    assert c.protocol == "anthropic"
    assert c.model == "claude-3"
    assert c.thinking is True


def test_multiple_providers() -> None:
    d = Path(tempfile.mkdtemp())
    cfg = d / "config.yaml"
    _write_yaml(
        cfg,
        """\
providers:
  - name: Claude
    protocol: anthropic
    model: claude-sonnet-4-6
    api_key: sk-a
    base_url: https://api.anthropic.com
  - name: GPT
    protocol: openai
    model: gpt-4o
    api_key: sk-b
    base_url: https://api.openai.com
    thinking: false
""",
    )
    configs = load_config(cfg)
    assert len(configs) == 2
    assert configs[0].name == "Claude"
    assert configs[1].name == "GPT"


def test_thinking_defaults_to_false() -> None:
    d = Path(tempfile.mkdtemp())
    cfg = d / "config.yaml"
    _write_yaml(
        cfg,
        """\
providers:
  - name: Test
    protocol: openai
    model: gpt-4o
    api_key: sk-test
    base_url: https://api.openai.com
""",
    )
    configs = load_config(cfg)
    assert configs[0].thinking is False


# ---- 非法配置 ---------------------------------------------------------------

def test_missing_file() -> None:
    with pytest.raises(ConfigError, match="配置文件未找到"):
        load_config("/nonexistent/path/config.yaml")


def test_not_yaml() -> None:
    d = Path(tempfile.mkdtemp())
    cfg = d / "config.yaml"
    # PyYAML 对单行字符串不会报错，需要真正的 YAML 语法错误
    _write_yaml(cfg, "providers:\n  - [invalid: *bad_anchor")
    with pytest.raises(ConfigError):
        load_config(cfg)


def test_missing_providers_key() -> None:
    d = Path(tempfile.mkdtemp())
    cfg = d / "config.yaml"
    _write_yaml(cfg, "foo: bar")
    with pytest.raises(ConfigError, match="providers"):
        load_config(cfg)


def test_empty_providers_list() -> None:
    d = Path(tempfile.mkdtemp())
    cfg = d / "config.yaml"
    _write_yaml(cfg, "providers: []")
    with pytest.raises(ConfigError, match="未配置任何 provider"):
        load_config(cfg)


def test_missing_fields() -> None:
    d = Path(tempfile.mkdtemp())
    cfg = d / "config.yaml"
    _write_yaml(
        cfg,
        """\
providers:
  - name: Test
    protocol: anthropic
""",
    )
    with pytest.raises(ConfigError, match="缺少必要字段"):
        load_config(cfg)


# ---- ProviderConfig 数据类 ------------------------------------------------

def test_provider_config_creation() -> None:
    c = ProviderConfig(
        name="Test",
        protocol="anthropic",
        model="claude-3",
        api_key="sk-123",
        base_url="https://api.anthropic.com",
        thinking=True,
    )
    assert c.name == "Test"
    assert c.thinking is True
