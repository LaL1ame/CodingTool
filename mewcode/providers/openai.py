"""OpenAIProvider —— 委托 OpenAIProtocol 处理流式请求。"""

from collections.abc import AsyncIterator

from mewcode.config import ProviderConfig
from mewcode.protocols.base import Delta
from mewcode.protocols.openai import OpenAIProtocol
from mewcode.providers.base import BaseProvider


class OpenAIProvider(BaseProvider):
    """OpenAI 协议 Provider。"""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._protocol = OpenAIProtocol(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
        )

    async def stream(self, messages: list[dict]) -> AsyncIterator[Delta]:
        async for delta in self._protocol.stream(messages):
            yield delta
