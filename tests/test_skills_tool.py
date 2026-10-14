"""F4: load_skill 工具行为（system_exempt、渲染返回、未知名报错）。"""

import pytest

from codepilot.skills.manager import SkillManager
from codepilot.skills.model import Skill
from codepilot.skills.tool import LoadSkillTool  # 从 skills 包引，不回环 tools


def _skill(name, body="do {input}", tools=("read",)):
    return Skill(name=name, description="d", body=body, tools=frozenset(tools))


@pytest.mark.asyncio
async def test_execute_returns_success_prefix_and_body():
    m = SkillManager({"commit": _skill("commit", body="按 {input} 提交", tools=("read",))})
    tool = LoadSkillTool(m)
    res = await tool.execute("commit", "  修复 bug  ")
    assert res.error is None
    assert "已加载并激活 Skill「commit」" in res.output   # 成功反馈可判断
    assert "完整 SOP" in res.output
    assert res.output.endswith("按 修复 bug 提交")        # 完整正文仍在
    assert "commit" in m.active


@pytest.mark.asyncio
async def test_execute_unloaded_product():
    m = SkillManager({})
    tool = LoadSkillTool(m)
    res = await tool.execute("nope")
    # 失败也给出可判断的错误（非空 error）
    assert res.error is not None and res.output is None


@pytest.mark.asyncio
async def test_system_exempt_is_true():
    m = SkillManager({})
    tool = LoadSkillTool(m)
    assert tool.system_exempt is True  # 逃生门，不受任何白名单约束


@pytest.mark.asyncio
async def test_tool_name_and_schema():
    m = SkillManager({})
    tool = LoadSkillTool(m)
    assert tool.name == "load_skill"
    assert tool.parameters["required"] == ["name"]