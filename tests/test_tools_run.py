import pytest
from codepilot.tools.run import RunTool


@pytest.fixture
def tool(workspace):
    return RunTool(workspace)


def test_is_side_effect(tool):
    assert tool.side_effect is True


class TestRunTool:
    async def test_run_executes_command(self, tool):
        r = await tool.execute(command="echo hello_test", confirm_callback=lambda c: True)
        assert r.error is None
        assert "hello_test" in r.output

    async def test_run_rejected(self, tool):
        r = await tool.execute(command="rm -rf /", confirm_callback=lambda c: False)
        assert r.error and "拒绝" in r.error

    async def test_run_uses_instance_callback(self, workspace):
        async def always_allow(cmd): return True
        t = RunTool(workspace, confirm_callback=always_allow)
        r = await t.execute(command="echo test")
        assert r.error is None

    async def test_run_with_exit_code(self, tool):
        r = await tool.execute(command="cmd /c exit 2", confirm_callback=lambda c: True)
        assert "Exit code: 2" in r.output

    async def test_run_stderr_captured(self, tool):
        r = await tool.execute(command="cmd /c echo err_msg 1>&2", confirm_callback=lambda c: True)
        assert "err_msg" in r.output.lower() or "STDERR" in r.output
