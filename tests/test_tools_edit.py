import pytest
from mewcode.tools.edit import EditTool


@pytest.fixture
def tool(workspace):
    return EditTool(workspace)


class TestEditTool:
    async def test_unique_match_replaces(self, tool, workspace):
        (workspace / "test.py").write_text("hello\nfoo bar\nbaz")
        r = await tool.execute(file_path="test.py", old_string="foo bar", new_string="replaced")
        assert r.error is None
        assert "Replaced" in r.output
        assert "replaced" in (workspace / "test.py").read_text()

    async def test_no_match_returns_error(self, tool, workspace):
        (workspace / "test.py").write_text("hello")
        r = await tool.execute(file_path="test.py", old_string="nonexistent", new_string="x")
        assert r.error and "未找到" in r.error

    async def test_multiple_matches_returns_error(self, tool, workspace):
        (workspace / "test.py").write_text("dup\nother\ndup\nother\ndup")
        r = await tool.execute(file_path="test.py", old_string="dup", new_string="x")
        assert r.error and "3" in r.error

    async def test_file_not_found(self, tool):
        r = await tool.execute(file_path="nonexistent.txt", old_string="x", new_string="y")
        assert r.error and "文件不存在" in r.error

    async def test_traversal_rejected(self, tool):
        r = await tool.execute(file_path="../secret.txt", old_string="x", new_string="y")
        assert r.error and "超出工作目录" in r.error
