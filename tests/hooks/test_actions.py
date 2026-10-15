"""state16 T5：四动作执行器。"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from codepilot.hooks.actions import (
    render,
    run_command,
    run_http,
    run_prompt,
    run_subagent,
)
from codepilot.hooks.injector import HookInjector

PY = sys.executable


class TestRender:
    def test_placeholder_replaced(self):
        assert render("fmt {file_path}", {"file_path": "a.py"}) == "fmt a.py"

    def test_missing_field_kept(self):
        assert render("fmt {nope}", {}) == "fmt {nope}"

    def test_unbalanced_brace_kept_as_is(self):
        assert render("echo {", {}) == "echo {"


class TestRunCommand:
    @pytest.mark.asyncio
    async def test_exit_zero(self):
        o = await run_command(f'"{PY}" -c "pass"', {}, timeout=10)
        assert o.exit_code == 0 and not o.timed_out and not o.error

    @pytest.mark.asyncio
    async def test_nonzero_and_stderr(self):
        o = await run_command(
            f'"{PY}" -c "import sys; sys.stderr.write(\\"deny reason\\"); sys.exit(3)"',
            {}, timeout=10)
        assert o.exit_code == 3
        assert "deny reason" in o.stderr

    @pytest.mark.asyncio
    async def test_timeout(self):
        o = await run_command(f'"{PY}" -c "import time; time.sleep(5)"',
                              {}, timeout=0.5)
        assert o.timed_out is True

    @pytest.mark.asyncio
    async def test_env_context_passed(self):
        o = await run_command(
            f'"{PY}" -c "import os,json; print(os.environ[\\"CODEPILOT_HOOK_EVENT\\"]); '
            f'print(json.loads(os.environ[\\"CODEPILOT_TOOL_ARGS\\"])[\\"command\\"])"',
            {"command": "git status"}, timeout=10, event="tool_pre", tool="run")
        assert o.exit_code == 0
        lines = o.stdout.strip().splitlines()
        assert lines[0] == "tool_pre"
        assert lines[1] == "git status"


class _Handler(BaseHTTPRequestHandler):
    received: dict = {}

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Handler.received = {
            "path": self.path,
            "body": body.decode(),
            "x_hook": self.headers.get("X-Hook"),
        }
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def http_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


class TestRunHttp:
    @pytest.mark.asyncio
    async def test_post_with_body_and_headers(self, http_server):
        ok, detail = await run_http(
            http_server + "/hook", method="POST",
            headers={"X-Hook": "codepilot"}, body="hello", timeout=5)
        assert ok, detail
        assert _Handler.received["path"] == "/hook"
        assert _Handler.received["body"] == "hello"
        assert _Handler.received["x_hook"] == "codepilot"

    @pytest.mark.asyncio
    async def test_failure_isolated(self):
        ok, detail = await run_http("http://127.0.0.1:1/nope", timeout=2)
        assert ok is False
        assert detail  # 有原因文本，不抛异常


class TestRunPrompt:
    def test_inject_rendered(self):
        inj = HookInjector()
        run_prompt("工作区文件: {file_path}", {"file_path": "a.py"}, inj, "s#0")
        assert inj.hook_blocks() == ["工作区文件: a.py"]

    def test_same_key_dedup(self):
        inj = HookInjector()
        run_prompt("v1", {}, inj, "s#0")
        run_prompt("v2", {}, inj, "s#0")
        assert inj.hook_blocks() == ["v2"]


class TestRunSubagent:
    def test_placeholder_logs(self, caplog):
        with caplog.at_level("INFO", logger="codepilot.hooks"):
            run_subagent()
        assert "未实现" in caplog.text
