"""上下文管理子包(state9)。

按 spec.md 实现:estimator(估算)→ budget(状态) → offload(层1)/compactor(层2)
→ manager(编排)。依赖单向无环,context 不 import agent/chat(仅注入其对象)。
"""
from codepilot.context.budget import ContextBudget, ContextConfig
from codepilot.context.manager import ContextManager

__all__ = ["ContextManager", "ContextBudget", "ContextConfig"]