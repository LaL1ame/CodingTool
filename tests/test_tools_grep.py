import pytest
from codepilot.tools.grep_tool import GrepTool


@pytest.fixture
def tool(workspace):
    return GrepTool(workspace)


class TestGrepTool:
    async def test_grep_finds_pattern(self, tool, workspace):
        (workspace / "a.py").write_text("import os\nimport sys\n")
        (workspace / "b.py").write_text("print('hi')\n")
        r = await tool.execute(pattern="import")
        assert r.error is None
        assert "a.py" in r.output and "b.py" not in r.output

    async def test_grep_case_insensitive(self, tool, workspace):
        (workspace / "test.py").write_text("Hello World\nHELLO again\n")
        r = await tool.execute(pattern="hello", ignore_case=True)
        assert "Hello" in r.output or "HELLO" in r.output

    async def test_grep_case_sensitive(self, tool, workspace):
        (workspace / "test.py").write_text("Hello World\nHELLO\n")
        r = await tool.execute(pattern="Hello")
        assert "HELLO" not in r.output

    async def test_grep_invalid_regex(self, tool):
        r = await tool.execute(pattern="[invalid(regex")
        assert r.error is not None

    async def test_grep_no_matches(self, tool, workspace):
        (workspace / "test.py").write_text("hello")
        r = await tool.execute(pattern="zzz_nonexistent")
        assert "(no matches)" in r.output

    async def test_grep_skips_git(self, tool, workspace):
        (workspace / ".git").mkdir(exist_ok=True)
        (workspace / ".git" / "config").write_text("hello world")
        (workspace / "normal.txt").write_text("hello world")
        r = await tool.execute(pattern="hello")
        assert "normal.txt" in r.output
        assert ".git" not in r.output
