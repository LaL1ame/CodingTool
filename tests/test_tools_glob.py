import pytest
from codepilot.tools.glob_tool import GlobTool


@pytest.fixture
def tool(workspace):
    return GlobTool(workspace)


class TestGlobTool:
    async def test_glob_finds_files(self, tool, workspace):
        (workspace / "a.py").write_text("")
        (workspace / "b.py").write_text("")
        (workspace / "readme.md").write_text("")
        r = await tool.execute(pattern="**/*.py")
        assert r.error is None
        assert "a.py" in r.output and "b.py" in r.output
        assert "readme.md" not in r.output

    async def test_glob_no_matches(self, tool):
        r = await tool.execute(pattern="**/*.rs")
        assert "(no matches)" in r.output

    async def test_glob_skips_git(self, tool, workspace):
        (workspace / ".git").mkdir(exist_ok=True)
        (workspace / ".git" / "config").write_text("")
        (workspace / "normal.txt").write_text("")
        r = await tool.execute(pattern="**/*")
        assert "normal.txt" in r.output
        assert ".git" not in r.output
