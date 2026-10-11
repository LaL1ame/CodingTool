import sys

import pytest
from codepilot.tools.run import RunTool


@pytest.fixture
def tool(workspace):
    return RunTool(workspace)


def test_is_side_effect(tool):
    assert tool.side_effect is True


class TestRunTool:
    async def test_run_executes_command(self, tool):
        r = await tool.execute(command="echo hello_test")
        assert r.error is None
        assert "hello_test" in r.output

    async def test_run_with_exit_code(self, tool):
        # 跨平台:不依赖 POSIX 的 sh,用跑测试的解释器制造"退出码=2"
        cmd = f'"{sys.executable}" -c "import sys; sys.exit(2)"'
        r = await tool.execute(command=cmd)
        assert "Exit code: 2" in r.output

    async def test_run_stderr_captured(self, tool):
        r = await tool.execute(command="sh -c 'echo err_msg 1>&2'")
        assert "err_msg" in r.output.lower() or "STDERR" in r.output