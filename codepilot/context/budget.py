"""上下文预算状态机(F3/F6)。

ContextConfig:配置模型(唯一真源,config.py 只读 YAML 造对象,不回定义)。
ContextBudget:判断是否触发层2摘要、记录熔断态、登记层1回收量。

关键量纲:
  occupied   = estimator.current()  当前上下文占用估算
  window     = context.window       模型窗口上限
  触发自动摘要当 occupied > window - auto_margin(且未熔断)
  连续失败 >= 3 次 → 熔断(is_openckety),停止自动压缩
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codepilot.context.estimator import TokenEstimator

_CIRCUIT_LIMIT = 3


@dataclass
class ContextConfig:
    """上下文管理配置模型(config.yaml 的 context: 段,缺省用默认)。"""

    window: int = 200_000            # 模型上下文窗口上限
    auto_margin: int = 13_000        # 自动触发安全余量(token)
    manual_margin: int = 3_000       # 手动触发安全余量(token)
    tool_threshold_n: int = 1000     # F1 单工具结果阈值(token)
    tool_batch_threshold_m: int = 4000  # F2 同消息工具结果合计阈值(token)
    compact_keep_tokens: int = 10_000   # F3.2 尾部原文保留区(token)
    compact_keep_min_msgs: int = 5       # F3.2 尾部原文至少保留条数


def default_context_config() -> ContextConfig:
    return ContextConfig()


class ContextBudget:
    """判断触发 + 熔断状态;不执行压缩内容,只决策与计数。"""

    def __init__(self, config: ContextConfig, estimator: TokenEstimator) -> None:
        self.config = config
        self.estimator = estimator
        self._summarize_failures = 0
        self._circuit_open = False

    # —— 占用 ——
    @property
    def occupied(self) -> int:
        return self.estimator.current()

    # —— 触发判断 ——
    @property
    def allows_auto_compact(self) -> bool:
        """允许自动压缩:逼近自动窗口且未熔断。"""
        return (not self._circuit_open
                and self.occupied > self.config.window - self.config.auto_margin)

    @property
    def manual_allowed(self) -> bool:
        """手动压缩:只要求未熔断(需有空间?由 caller 决定 margin)。"""
        return not self._circuit_open

    async def should_compact(self) -> bool:
        """自动触发判断(可在子类覆写为更复杂策略;YAGNI 保持此判断)。"""
        return self.allows_auto_compact

    # —— 熔断(F6) ——
    @property
    def is_openckety(self) -> bool:
        return self._circuit_open

    def register_summarize_failure(self) -> None:
        """摘要失败计数;达到 _CIRCUIT_LIMIT 熔断。"""
        self._summarize_failures += 1
        if self._summarize_failures >= _CIRCUIT_LIMIT:
            self._circuit_open = True

    def summarize_succeeded(self) -> None:
        """摘要成功 → 清零失败计数。"""
        self._summarize_failures = 0

    def reset_circuit(self) -> None:
        """解除熔断(F6.3)。"""
        self._circuit_open = False
        self._summarize_failures = 0

    # —— 层1回收登记 ——
    def record_offload(self, saved_tokens: int) -> None:
        """层1存盘回收:把占用估算减去 saved_tokens,保持触发判断口径一致。

        由于估算不精确,此减量是近似;锚点重订(anchor)时会被权威值覆盖。
        """
        # 直接对 estimator 内部做减法不可行(无接口);故改为记录"回收抵免",
        # 但为保持口径简单,这里用估算器 add 负增量不成立 —— 我们通过
        # 把 saved 记为"豁免量"暂存,待 anchor 重订时自然消失。
        self._offload_credit = getattr(self, "_offload_credit", 0) + max(0, int(saved_tokens))

    @property
    def effective_occupied(self) -> int:
        """占用估算扣除层1回收抵免后的有效值(供 TUI 展示与触发参考)。"""
        return max(0, self.occupied - getattr(self, "_offload_credit", 0))

    def anchor(self, input_tokens: int) -> None:
        """每轮重锚:权威值覆盖,层1回收抵免随之归零(新锚点不含旧抵免)。"""
        self.estimator.anchor(input_tokens)
        self._offload_credit = 0