"""Provider 抽象层测试 —— 工厂方法 + Delta 透传。"""

import pytest

from mewcode.config import ProviderConfig
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.providers.base import BaseProvider
from mewcode.providers.openai import OpenAIProvider


def make_config(protocol: str = "anthropic") -> ProviderConfig:
    return ProviderConfig(
        name="Test",
        protocol=protocol,
        model="test-model",
        api_key="sk-test",
        base_url="https://api.test.com",
        thinking=False,
    )


def test_factory_creates_anthropic() -> None:
    p = BaseProvider.create(make_config("anthropic"))
    assert isinstance(p, AnthropicProvider)
    assert p.config.name == "Test"


def test_factory_creates_openai() -> None:
    p = BaseProvider.create(make_config("openai"))
    assert isinstance(p, OpenAIProvider)


def test_factory_case_insensitive() -> None:
    p = BaseProvider.create(make_config("Anthropic"))
    assert isinstance(p, AnthropicProvider)


def test_factory_unknown_protocol() -> None:
    with pytest.raises(ValueError, match="不支持的协议类型"):
        BaseProvider.create(make_config("gemini"))


def test_provider_preserves_config() -> None:
    config = make_config("anthropic")
    p = AnthropicProvider(config)
    assert p.config is config
    assert p.config.thinking is False


def test_provider_creates_internal_protocol() -> None:
    config = make_config("openai")
    p = OpenAIProvider(config)
    assert p._protocol is not None
    assert p._protocol.model == "test-model"
