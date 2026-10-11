"""Token 估算模块(纯函数,无 LLM 依赖)。

N1:不引精确 tokenizer。用近似估算——code 侧重按字符(约 chars//4),
prose 侧重按词(words×1.3)。锚定上一次请求的 usage.input_tokens(LLM 亲测),
对自那之后新增的增量按字符估算。

设计:估算器维护「锚点 + 增量」两个量。anchor() 用 LLM 返回的真实 input_tokens
重设锚点(校准累计误差);add() 只对新增内容估算增量。current() = 锚点 + 增量。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Estimate:
    """一次估算的结果。"""

    tokens: int
    source: str  # "code" | "prose" | "usage"


def estimate_text(text: str, kind: str = "code") -> int:
    """按内容类型估算 token 数。

    kind="code"  → 按字符,约 chars//4(代码 token 密度接近 4 字符/token)。
    kind="prose" → 按词,words×1.3(英文散文约 1.3 token/词)。
    中文字符与代码同按字符估算(偏保守)。
    """
    if not text:
        return 0
    if kind == "prose":
        return max(1, int(len(text.split()) * 1.3))
    return max(1, len(text) // 4)


class TokenEstimator:
    """增量估算器:锚定上次 usage.input_tokens + 对增量按字符估算。"""

    def __init__(self, last_input_tokens: int | None = None) -> None:
        self._anchor: int = last_input_tokens if last_input_tokens is not None else 0
        self._delta: int = 0

    def anchor(self, input_tokens: int) -> None:
        """用 LLM 亲测的完整上文 token 数重锚,并清零增量。

        每轮 message_stop 后调用。锚点是权威值,增量随之归零(误差被吸收)。
        """
        self._anchor = int(input_tokens)
        self._delta = 0

    def add(self, text: str, kind: str = "code") -> int:
        """对新增内容做增量估算;返回本次增量 token 数。"""
        inc = estimate_text(text, kind)
        self._delta += inc
        return inc

    def current(self) -> int:
        """当前累计估算(= 锚点 + 增量)。"""
        return self._anchor + self._delta