"""CodePilot SubAgent 系统（state17）—— 主 Agent 委派子任务的独立执行通道。

模块：model(数据模型) / loader(角色四级加载) / runner(独立 Agent 执行 + 多层防线) /
manager(委派中心 + 后台任务) / tool(agent 工具)。
内置角色数据在 subagents/data/（随包 package-data 分发）。
"""

from codepilot.subagents.loader import BUILTIN_DIR, load_all, parse_role_file
from codepilot.subagents.manager import SubAgentManager
from codepilot.subagents.model import AgentRole, RoleError, SubAgentTask
from codepilot.subagents.runner import SubAgentError
from codepilot.subagents.tool import AgentTool
from codepilot.subagents.transcript import TranscriptEntry

__all__ = [
    "AgentRole", "RoleError", "SubAgentTask", "TranscriptEntry",
    "parse_role_file", "load_all", "BUILTIN_DIR",
    "SubAgentManager", "SubAgentError", "AgentTool",
]
