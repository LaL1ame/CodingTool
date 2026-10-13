"""CodePilot Skill 系统 — 可复用 AI 操作能力包（state15）。

两级：解析(loader/resolver/model) + 状态管理(manager) + 模型入口(tool)。
内置样板数据在 skills/data/（随包 package-data 分发）。
"""

from codepilot.skills.model import Skill, SkillError
from codepilot.skills.loader import parse_skill_file, render_body
from codepilot.skills.resolver import load_all, discover_skill_dirs
from codepilot.skills.manager import SkillManager
from codepilot.skills.tool import LoadSkillTool

# 内置 Skill 数据根（随包分发）。在包目录下定位，稳妥起见取本文件所在 physical 路径。
from pathlib import Path

BUILTIN_DIR = Path(__file__).parent / "data"

__all__ = [
    "Skill", "SkillError",
    "parse_skill_file", "render_body",
    "load_all", "discover_skill_dirs",
    "SkillManager", "LoadSkillTool", "BUILTIN_DIR",
]