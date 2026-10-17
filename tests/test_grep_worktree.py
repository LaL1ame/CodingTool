"""grep 结果不包含 .codepilot/worktrees 内容。"""

import pytest

from codepilot.tools.grep_tool import GrepTool


@pytest.mark.asyncio
async def test_grep_skips_worktree_root(tmp_path):
    (tmp_path / "a.py").write_text("needle here\n", encoding="utf-8")
    wt = tmp_path / ".codepilot" / "worktrees" / "x"
    wt.mkdir(parents=True)
    (wt / "b.py").write_text("needle inside worktree\n", encoding="utf-8")

    res = await GrepTool(tmp_path).execute("needle")
    assert res.error is None
    assert "a.py" in res.output
    assert "b.py" not in res.output
    assert ".codepilot" not in res.output


@pytest.mark.asyncio
async def test_grep_unchanged_without_worktrees(tmp_path):
    (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")
    res = await GrepTool(tmp_path).execute("needle")
    assert "a.py" in res.output
