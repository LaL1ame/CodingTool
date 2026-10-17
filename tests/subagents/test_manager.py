"""SubAgentManager 测试：委派分流、并发/深度、转后台、取消、状态追踪。

对 runner 打桩（monkeypatch），聚焦 manager 的调度逻辑而非 Agent 执行。
"""

import asyncio
from pathlib import Path

from codepilot.config import ProviderConfig
from codepilot.permissions import PermissionManager
from codepilot.providers import BaseProvider
from codepilot.prompt import EnvContext
from codepilot.subagents import manager as manager_mod
from codepilot.subagents.manager import SubAgentManager
from codepilot.subagents.model import AgentRole
from codepilot.subagents.runner import SubAgentError
from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry


class _MockProvider(BaseProvider):
    def __init__(self):
        self.config = ProviderConfig(name="m", protocol="openai", model="m",
                                     api_key="k", base_url="http://m")

    async def stream(self, messages, tools=None, system=None):
        return
        yield  # pragma: no cover

    async def complete(self, system, messages):
        return ""


class _T(Tool):
    def __init__(self, name):
        self._n = name

    @property
    def name(self): return self._n
    @property
    def description(self): return self._n
    @property
    def parameters(self): return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        from codepilot.protocols import ToolResult
        return ToolResult(call_id="", name=self._n, output="ok")


def _mgr(**kw) -> SubAgentManager:
    reg = ToolRegistry()
    reg.register(_T("read"))
    base = dict(
        known={"r": AgentRole(name="r", description="d", body="b")},
        workspace=Path("."),
        provider=_MockProvider(),
        registry=reg,
        env=EnvContext(cwd=".", date="2026-10-17"),
        parent_permission=PermissionManager(workspace=Path(".")),
    )
    base.update(kw)
    return SubAgentManager(**base)


def _patch_runner(monkeypatch, result=("summary", None), error=None):
    """桩掉 runner 两个入口，让 manager 聚焦调度逻辑。"""
    async def _role(role, prompt, *, depth, shared):
        if error:
            raise error
        return result
    async def _fork(prompt, *, parent_msgs, parent_names, depth, shared):
        if error:
            raise error
        return result
    monkeypatch.setattr(manager_mod, "run_role_agent", _role)
    monkeypatch.setattr(manager_mod, "run_fork_agent", _fork)


async def test_delegate_defined_foreground(monkeypatch):
    _patch_runner(monkeypatch)
    mgr = _mgr()
    res = await mgr.delegate("defined", "做任务", role="r")
    assert res.error is None
    assert res.output == "summary"
    t = mgr.tasks()[0]
    assert t.status == "done"
    assert t.kind == "defined"
    assert t.role == "r"


async def test_delegate_unknown_role(monkeypatch):
    _patch_runner(monkeypatch)
    mgr = _mgr()
    res = await mgr.delegate("defined", "x", role="nope")
    assert res.error == "未知角色: 'nope'"
    assert mgr.tasks() == []


async def test_delegate_missing_role():
    mgr = _mgr()
    res = await mgr.delegate("defined", "x")
    assert "需指定 role" in res.error


async def test_delegate_fork_forced_background(monkeypatch):
    _patch_runner(monkeypatch)
    mgr = _mgr()
    res = await mgr.delegate("fork", "总结")
    assert "已转后台" in res.output
    # 后台完成需事件循环跑完回调
    await asyncio.sleep(0)
    t = mgr.tasks()[0]
    assert t.status in ("done", "running")
    assert t.kind == "fork"
    assert t.role is None


async def test_concurrency_limit(monkeypatch):
    """后台并发上限：达到后新委派被拒（F10）。"""
    async def _slow(role, prompt, *, depth, shared):
        await asyncio.sleep(0.5)
        return ("s", None)
    monkeypatch.setattr(manager_mod, "run_role_agent", _slow)
    mgr = _mgr(max_concurrent=2)
    r1 = await mgr.delegate("defined", "a", role="r", background=True)
    r2 = await mgr.delegate("defined", "b", role="r", background=True)
    assert "已转后台" in r1.output and "已转后台" in r2.output
    r3 = await mgr.delegate("defined", "c", role="r", background=True)
    assert "并发已满" in r3.error


async def test_background_done_callback(monkeypatch):
    """后台完成触发 on_task_done 回调（回灌主对话接缝）。"""
    _patch_runner(monkeypatch)
    done = []
    mgr = _mgr(on_task_done=lambda t: done.append(t))
    await mgr.delegate("defined", "x", role="r", background=True)
    await asyncio.sleep(0.05)
    assert len(done) == 1
    assert done[0].summary == "summary"


async def test_failure_isolated(monkeypatch):
    """子 Agent 失败：task 状态 failed，不外抛（N2）。"""
    _patch_runner(monkeypatch, error=SubAgentError("boom"))
    done = []
    mgr = _mgr(on_task_done=lambda t: done.append(t))
    await mgr.delegate("defined", "x", role="r", background=True)
    await asyncio.sleep(0.05)
    t = mgr.tasks()[0]
    assert t.status == "failed"
    assert t.error == "boom"


async def test_cancel_background(monkeypatch):
    async def _slow(role, prompt, *, depth, shared):
        await asyncio.sleep(10)
        return ("s", None)
    monkeypatch.setattr(manager_mod, "run_role_agent", _slow)
    mgr = _mgr()
    await mgr.delegate("defined", "x", role="r", background=True)
    tid = mgr.tasks()[0].task_id
    assert mgr.cancel(tid) is True
    assert mgr.tasks()[0].status == "cancelled"
    assert mgr.cancel(999) is False


async def test_foreground_timeout_to_background(monkeypatch):
    """前台超时 → 转后台并返回占位（D11）。"""
    async def _slow(role, prompt, *, depth, shared):
        await asyncio.sleep(1)
        return ("slow result", None)
    monkeypatch.setattr(manager_mod, "run_role_agent", _slow)
    mgr = _mgr()
    res = await mgr.delegate("defined", "x", role="r", timeout=0.05)
    assert "已超时转后台" in res.output
    assert "task_id=" in res.output
    t = mgr.tasks()[0]
    assert t.status == "backgrounded"
    # 等它真正完成
    await asyncio.sleep(1.1)
    assert t.status == "done"
    assert t.summary == "slow result"


async def test_foreground_manual_background(monkeypatch):
    """/bg 手动切：前台等待中任务转后台继续跑（D12）。"""
    async def _slow(role, prompt, *, depth, shared):
        await asyncio.sleep(0.5)
        return ("manual bg result", None)
    monkeypatch.setattr(manager_mod, "run_role_agent", _slow)
    mgr = _mgr()
    task_fut = asyncio.ensure_future(mgr.delegate("defined", "x", role="r"))
    await asyncio.sleep(0.05)  # 让 delegate 进入前台等待
    tid = next(iter(mgr._foreground))
    assert mgr.background_now(tid) is True
    res = await task_fut
    assert "手动切" in res.output
    assert mgr.tasks()[0].status == "backgrounded"
    await asyncio.sleep(0.6)
    assert mgr.tasks()[0].status == "done"
    assert mgr.tasks()[0].summary == "manual bg result"
