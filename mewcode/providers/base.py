"""Provider 抽象层：统一对话接口 + 工厂方法。"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from mewcode.config import ProviderConfig
from mewcode.protocols.base import Delta


class BaseProvider(ABC):
    """LLM Provider 抽象基类。

    对上（对话管理层）暴露统一的流式对话接口，
    对下（协议层）委托具体 Protocol 完成 HTTP 通信。
    """

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @abstractmethod
    async def stream(self, messages: list[dict]) -> AsyncIterator[Delta]:
        """流式对话 —— 接收消息列表，产出 Delta 增量流。"""
        ...

    @staticmethod
    def create(config: ProviderConfig) -> "BaseProvider":
        """工厂方法：根据 protocol 字段创建对应 Provider 实例。"""
        from mewcode.providers.anthropic import AnthropicProvider
        from mewcode.providers.openai import OpenAIProvider

        proto = config.protocol.lower()
        if proto == "anthropic":
            return AnthropicProvider(config)
        elif proto == "openai":
            return OpenAIProvider(config)
        else:
            raise ValueError(f"不支持的协议类型: {config.protocol}")
