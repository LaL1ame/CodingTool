"""AnthropicProvider —— 委托 AnthropicProtocol 处理流式请求。"""

from collections.abc import AsyncIterator

from mewcode.config import ProviderConfig
from mewcode.protocols.anthropic import AnthropicProtocol
from mewcode.protocols.base import Delta
from mewcode.providers.base import BaseProvider


class AnthropicProvider(BaseProvider):
    """Anthropic 协议 Provider。"""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._protocol = AnthropicProtocol(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
        )

    async def stream(self, messages: list[dict]) -> AsyncIterator[Delta]:
        async for delta in self._protocol.stream(
            messages, thinking=self.config.thinking
        ):
            yield delta
