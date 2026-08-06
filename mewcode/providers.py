"""Provider 抽象层 + 工厂方法。"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from mewcode.config import ProviderConfig
from mewcode.protocols import AnthropicProtocol, Delta, OpenAIProtocol


class BaseProvider(ABC):
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @abstractmethod
    async def stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[Delta]:
        ...

    @staticmethod
    def create(config: ProviderConfig) -> "BaseProvider":
        proto = config.protocol.lower()
        if proto == "anthropic":
            return AnthropicProvider(config)
        elif proto == "openai":
            return OpenAIProvider(config)
        else:
            raise ValueError(f"不支持的协议: {config.protocol}")


class AnthropicProvider(BaseProvider):
    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._protocol = AnthropicProtocol(config.base_url, config.api_key, config.model)

    async def stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[Delta]:
        async for d in self._protocol.stream(
            messages, thinking=self.config.thinking, tools=tools
        ):
            yield d


class OpenAIProvider(BaseProvider):
    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._protocol = OpenAIProtocol(config.base_url, config.api_key, config.model)

    async def stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[Delta]:
        async for d in self._protocol.stream(messages, tools=tools):
            yield d
