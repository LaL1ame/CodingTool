import pytest
from mewcode.tools.write import WriteTool


@pytest.fixture
def tool(workspace):
    return WriteTool(workspace)


class TestWriteTool:
    async def test_write_new_file(self, tool, workspace):
        r = await tool.execute(file_path="hello.py", content="print('hi')")
        assert r.error is None
        assert "Wrote" in r.output
        assert (workspace / "hello.py").read_text() == "print('hi')"

    async def test_write_creates_parent_dirs(self, tool, workspace):
        r = await tool.execute(file_path="a/b/c/test.txt", content="deep")
        assert r.error is None
        assert (workspace / "a/b/c/test.txt").read_text() == "deep"

    async def test_write_overwrite(self, tool, workspace):
        (workspace / "existing.txt").write_text("old")
        r = await tool.execute(file_path="existing.txt", content="new")
        assert r.error is None
        assert "Overwrote" in r.output
        assert (workspace / "existing.txt").read_text() == "new"

    async def test_write_traversal_rejected(self, tool):
        r = await tool.execute(file_path="../outside.txt", content="x")
        assert r.error and "超出工作目录" in r.error

    async def test_write_empty_content(self, tool, workspace):
        r = await tool.execute(file_path="empty.txt", content="")
        assert r.error is None
        assert (workspace / "empty.txt").exists()
