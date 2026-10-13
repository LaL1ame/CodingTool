"""F4: load_skill 工具行为（system_exempt、渲染返回、未知名报错）。"""

import pytest

from codepilot.skills.manager import SkillManager
from codepilot.skills.model import Skill
from codepilot.skills.tool import LoadSkillTool  # 从 skills 包引，不回环 tools


def _skill(name, body="do {input}"):
    return Skill(name=name, description="d", body=body, tools=frozenset({"read"}))


@pytest.mark.asyncio
async def test_execute_returns_rendered_body():
    m = SkillManager({"commit": _skill("commit", body="按 {input} 提交")})
    tool = LoadSkillTool(m)
    res = await tool.execute("commit", "  修复 bug  ")
    assert res.error is None
    assert res.output == "按 修复 bug 提交"
    assert "commit" in m.active


@pytest.mark.asyncio
async def test_execute_unknown_name_returns_error():
    m = SkillManager({})
    tool = LoadSkillTool(m)
    res = await tool.execute("nope")
    assert res.error is not None
    assert "未知" in res.error


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