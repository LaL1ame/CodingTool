"""协议层基类：Delta 统一数据载体 + BaseProtocol 抽象类。"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class Delta:
    """所有协议层向上的统一增量载体。

    一次只填充一个字段：
    - 正文增量：text 有值
    - 思考增量：thinking 有值（上层接收即丢弃）
    - 结束信号：done=True
    - 错误信号：error 有值
    """

    text: str | None = None
    thinking: str | None = None
    done: bool = False
    error: str | None = None


class BaseProtocol(ABC):
    """LLM 协议适配的抽象基类。

    每个子类封装一种 API 协议的 HTTP 请求构造与 SSE 响应解析，
    对外输出统一的 Delta 增量流。
    """

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    @abstractmethod
    async def stream(
        self, messages: list[dict], thinking: bool = False
    ) -> AsyncIterator[Delta]:
        """构造请求 → 发送 POST → 逐行解析 SSE → yield Delta。"""
        ...
