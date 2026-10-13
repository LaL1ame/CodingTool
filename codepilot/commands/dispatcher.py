"""命令分发器(state14) —— 把命中命令交给处理函数(F3/F4)。

处理函数契约 handler(ctx, params) -> str | None:
- 返回一条用户消息 → 上层转发给 AI
- 返回 None → 已本地处理完
支持同步与协程两类处理器(N4)。
"""
from __future__ import annotations

import inspect


def dispatch_command(spec, ctx, params: str):
    """调用 spec.handler。同步处理器直接返回其 str|None;协程处理器返回协程。

    返回：
      - str  → 经解析出的转发消息(交给 AI)
      - None → 本地处理完
      - coroutine → 需调用方 run_worker 执行(内部会再产出 str|None)
    """
    handler = spec.handler
    if inspect.iscoroutinefunction(handler):
        return handler(ctx, params)
    return handler(ctx, params)