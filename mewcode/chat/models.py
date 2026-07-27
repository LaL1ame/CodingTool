"""对话数据模型。"""

import time
from dataclasses import dataclass, field


@dataclass
class Message:
    """单条对话消息。"""

    role: str           # "system" | "user" | "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)
    duration: float | None = None   # 仅 assistant，秒
