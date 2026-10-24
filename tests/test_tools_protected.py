"""Backlog1 机制层：run/grep 对受保护根（团队持久化目录）的硬屏蔽。"""

from pathlib import Path

from codepilot.tools.grep_tool import GrepTool
from codepilot.tools.run import RunTool


def _tmp_roots(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "workspace"
    ws.mkdir()
    team = tmp_path / "teams" / "teamdemo"
    team.mkdir(parents=True)
    return ws, team


async def test_run_rejects_absolute_team_path(tmp_path):
    ws, team = _tmp_roots(tmp_path)
    tool = RunTool(ws, protected_roots=(team,))
    target = team / "tasks.json"
    result = await tool.execute(command=f"type {target.as_posix()}")
    assert result.error and "团队数据" in result.error
    assert "team_*" in result.error


async def test_run_rejects_home_style_path():
    """~/.config/codepilot/teams 形态的 token（受保护根位于真实 home 下）。"""
    from pathlib import Path

    team = Path.home() / ".config" / "codepilot" / "teams"
    ws = Path.cwd()
    tool = RunTool(ws, protected_roots=(team,))
    result = await tool.execute(
        command="cat ~/.config/codepilot/teams/tasks.json")
    assert result.error and "团队数据" in result.error


async def test_run_allows_normal_command(tmp_path):
    ws, team = _tmp_roots(tmp_path)
    tool = RunTool(ws, protected_roots=(team,))
    result = await tool.execute(command="echo hello")
    assert result.error is None
    assert "hello" in result.output


async def test_run_without_protected_roots_is_unchanged(tmp_path):
    ws, _ = _tmp_roots(tmp_path)
    tool = RunTool(ws)  # 未注入 → 不拦截
    result = await tool.execute(command="echo ok")
    assert result.error is None


async def test_grep_rejects_absolute_team_root(tmp_path):
    ws, team = _tmp_roots(tmp_path)
    tool = GrepTool(ws, protected_roots=(team,))
    result = await tool.execute(pattern="tasks", path=str(team))
    assert result.error and "团队数据" in result.error


async def test_grep_without_protected_roots_unchanged(tmp_path):
    ws, _ = _tmp_roots(tmp_path)
    tool = GrepTool(ws)
    result = await tool.execute(pattern="nope", path=str(ws))
    assert result.error is None
    assert "(no matches)" in result.output
