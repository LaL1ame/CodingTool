"""Hook 系统（state16）：事件 + 条件 + 动作，生命周期关键节点挂自动化动作。"""

from codepilot.hooks.engine import HookEngine
from codepilot.hooks.injector import HookInjector
from codepilot.hooks.loader import load_hooks
from codepilot.hooks.model import EVENTS, HookError

__all__ = ["HookEngine", "HookInjector", "load_hooks", "HookError", "EVENTS"]
