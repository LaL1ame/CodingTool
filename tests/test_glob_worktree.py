"""glob 结果不包含 .codepilot/worktrees 内容。"""

import pytest

from codepilot.tools.glob_tool import GlobTool


@pytest.mark.asyncio
async def test_glob_skips_worktree_root(tmp_path):
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    wt = tmp_path / ".codepilot" / "worktrees" / "x"
    wt.mkdir(parents=True)
    (wt / "b.py").write_text("b", encoding="utf-8")

    res = await GlobTool(tmp_path).execute("**/*.py")
    assert res.error is None
    assert "a.py" in res.output
    assert "b.py" not in res.output
    assert ".codepilot" not in res.output


@pytest.mark.asyncio
async def test_glob_unchanged_without_worktrees(tmp_path):
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("b", encoding="utf-8")
    res = await GlobTool(tmp_path).execute("**/*.py")
    assert "a.py" in res.output and "b.py" in res.output
