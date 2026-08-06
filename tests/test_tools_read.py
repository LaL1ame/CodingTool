import pytest
from codepilot.tools.read import ReadTool


@pytest.fixture
def tool(workspace):
    return ReadTool(workspace)


class TestReadTool:
    async def test_read_basic(self, tool, workspace_with_files):
        r = await tool.execute(file_path="readme.txt")
        assert r.error is None
        assert "hello world" in r.output
        assert r.output.startswith("     1\t")

    async def test_read_nonexistent(self, tool):
        r = await tool.execute(file_path="nonexistent.txt")
        assert r.error and "文件不存在" in r.error

    async def test_read_traversal_rejected(self, tool):
        r = await tool.execute(file_path="../secret.txt")
        assert r.error and "超出工作目录" in r.error

    async def test_read_with_offset(self, tool, workspace_with_files):
        r = await tool.execute(file_path="readme.txt", offset=1)
        assert "hello world" not in r.output
        assert "line two" in r.output

    async def test_read_with_limit(self, tool, workspace_with_files):
        r = await tool.execute(file_path="readme.txt", limit=1)
        lines = [l for l in r.output.split("\n") if l.strip()]
        assert len(lines) == 1
