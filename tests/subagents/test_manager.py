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
from codepilot.subagents.runner import RunResult, SubAgentError
from codepilot.subagents.transcript import TranscriptEntry
from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry
from codepilot.worktree.model import ExitResult, WorktreeHandle


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


class _FakeWorktree:
    def __init__(self, *, keep=False, enter_error=None):
        self.keep = keep
        self.enter_error = enter_error
        self.entered: list[tuple[str, str, int]] = []
        self.left: list[str] = []

    def enter(self, name, *, role, task_id):
        if self.enter_error is not None:
            raise self.enter_error
        self.entered.append((name, role, task_id))
        path = Path("wt") / name
        return WorktreeHandle(
            name=name, path=path, branch=f"codepilot/{name}",
            base="b", created_at=1.0)

    def leave(self, handle):
        self.left.append(handle.name)
        reason = "kept/dirty" if self.keep else "removed/clean"
        return ExitResult(self.keep, reason, handle.path, handle.branch)


def _result(summary="summary", entries=None):
    return RunResult(
        summary, None,
        [TranscriptEntry("assistant", summary)] if entries is None else entries)


def _patch_runner(monkeypatch, result=None, error=None):
    """桩掉 runner 两个入口，让 manager 聚焦调度逻辑。"""
    result = _result() if result is None else result

    async def _role(role, prompt, *, depth, shared, worktree=None):
        if error:
            raise error
        return result

    async def _fork(prompt, *, parent_msgs, parent_names, depth, shared):
        if error:
            raise error
        return result

    monkeypatch.setattr(manager_mod, "run_role_agent", _role)
    monkeypatch.setattr(manager_mod, "run_fork_agent", _fork)


def test_role_list_block():
    mgr = _mgr()
    block = mgr.role_list_block()
    assert "- r — d" in block
    assert "agent 工具" in block


def test_role_list_block_empty():
    assert _mgr(known={}).role_list_block() == ""


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
    # 轨迹快照已保存（F11），前台任务同样可查看
    assert [e.text for e in t.entries] == ["summary"]
    # 终态记录完成时间，耗时不随查看时刻增长（F11 展示修正）
    assert t.finished_at > 0
    assert 0 <= t.finished_at - t.created_at


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
    async def _slow(role, prompt, *, depth, shared, worktree=None):
        await asyncio.sleep(0.5)
        return _result("s")
    monkeypatch.setattr(manager_mod, "run_role_agent", _slow)
    mgr = _mgr(max_concurrent=2)
    r1 = await mgr.delegate("defined", "a", role="r", background=True)
    r2 = await mgr.delegate("defined", "b", role="r", background=True)
    assert "已转后台" in r1.output and "已转后台" in r2.output
    assert "只回复一句" in r1.output
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


async def test_foreground_failure_returns_error(monkeypatch):
    """前台失败 → ToolResult(error)，不得误标 done（回归）。"""
    _patch_runner(monkeypatch, error=SubAgentError("front boom"))
    mgr = _mgr()
    res = await mgr.delegate("defined", "x", role="r")
    assert res.error == "front boom"
    assert mgr.tasks()[0].status == "failed"


async def test_cancel_background(monkeypatch):
    async def _slow(role, prompt, *, depth, shared, worktree=None):
        await asyncio.sleep(10)
        return _result("s")
    monkeypatch.setattr(manager_mod, "run_role_agent", _slow)
    mgr = _mgr()
    await mgr.delegate("defined", "x", role="r", background=True)
    tid = mgr.tasks()[0].task_id
    assert mgr.cancel(tid) is True
    assert mgr.tasks()[0].status == "cancelled"
    assert mgr.cancel(999) is False


async def test_foreground_timeout_to_background(monkeypatch):
    """前台超时 → 转后台并返回占位（D11）。"""
    async def _slow(role, prompt, *, depth, shared, worktree=None):
        await asyncio.sleep(1)
        return _result("slow result")
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


async def test_failure_stores_partial_entries(monkeypatch):
    """失败任务保留已产生的部分轨迹（F11）。"""
    partial = [TranscriptEntry("assistant", "做到一半")]
    _patch_runner(monkeypatch, error=SubAgentError("boom", entries=partial))
    mgr = _mgr()
    res = await mgr.delegate("defined", "x", role="r")
    assert res.error == "boom"
    assert mgr.tasks()[0].entries == tuple(partial)


async def test_counts_running_and_done(monkeypatch):
    """提示行概要：(运行中, 已完成)（F11）。"""
    async def _slow(role, prompt, *, depth, shared, worktree=None):
        await asyncio.sleep(0.2)
        return _result("s")

    monkeypatch.setattr(manager_mod, "run_role_agent", _slow)
    mgr = _mgr()
    assert mgr.counts() == (0, 0)
    await mgr.delegate("defined", "x", role="r", background=True)
    assert mgr.counts() == (1, 0)
    await asyncio.sleep(0.3)
    assert mgr.counts() == (0, 1)


async def test_foreground_manual_background(monkeypatch):
    """/bg / Ctrl+B 手动切：前台等待中任务转后台继续跑（D12）。"""
    async def _slow(role, prompt, *, depth, shared, worktree=None):
        await asyncio.sleep(0.5)
        return _result("manual bg result")
    monkeypatch.setattr(manager_mod, "run_role_agent", _slow)
    mgr = _mgr()
    task_fut = asyncio.ensure_future(mgr.delegate("defined", "x", role="r"))
    await asyncio.sleep(0.05)  # 让 delegate 进入前台等待
    assert len(mgr.foreground_task_ids()) == 1
    tid = mgr.foreground_task_ids()[0]
    assert mgr.background_now(tid) is True
    res = await task_fut
    assert "手动切" in res.output
    assert "非超时" in res.output          # 防模型误判成超时（真机回归）
    assert "只回复一句" in res.output      # 约束主 Agent 不要长篇展开
    assert mgr.tasks()[0].status == "backgrounded"
    await asyncio.sleep(0.6)
    assert mgr.tasks()[0].status == "done"
    assert mgr.tasks()[0].summary == "manual bg result"


# —— state18: worktree 隔离 ——

def _isolated_role(**kw):
    return AgentRole(
        name="r", description="d", body="b", isolation="worktree", **kw)


def test_role_list_marks_isolation():
    mgr = _mgr(known={"r": _isolated_role()})
    assert "隔离" in mgr.role_list_block()


async def test_worktree_param_rejected_for_non_isolated(monkeypatch):
    _patch_runner(monkeypatch)
    wt = _FakeWorktree()
    mgr = _mgr(worktree=wt)
    res = await mgr.delegate(
        "defined", "x", role="r", worktree="auth-fix")
    assert "未声明" in res.error
    assert wt.entered == []


async def test_worktree_param_rejected_for_fork(monkeypatch):
    _patch_runner(monkeypatch)
    mgr = _mgr(worktree=_FakeWorktree())
    res = await mgr.delegate("fork", "x", worktree="auth-fix")
    assert "仅适用于 defined" in res.error


async def test_isolated_role_auto_name_enter_leave(monkeypatch):
    _patch_runner(monkeypatch)
    wt = _FakeWorktree()
    mgr = _mgr(known={"r": _isolated_role()}, worktree=wt)
    res = await mgr.delegate("defined", "x", role="r")
    assert res.error is None
    assert wt.entered == [("r-1", "r", 1)]
    assert wt.left == ["r-1"]
    assert mgr.tasks()[0].worktree == "r-1"
    assert "[Worktree]" in mgr.tasks()[0].summary


async def test_isolated_role_keeps_and_reports(monkeypatch):
    _patch_runner(monkeypatch)
    wt = _FakeWorktree(keep=True)
    mgr = _mgr(known={"r": _isolated_role()}, worktree=wt)
    await mgr.delegate(
        "defined", "x", role="r", worktree="auth-fix")
    summary = mgr.tasks()[0].summary
    assert "保留" in summary
    assert "codepilot/auth-fix" in summary


async def test_explicit_worktree_name(monkeypatch):
    _patch_runner(monkeypatch)
    wt = _FakeWorktree()
    mgr = _mgr(known={"r": _isolated_role()}, worktree=wt)
    await mgr.delegate(
        "defined", "x", role="r", worktree="feature/login")
    assert wt.entered[0][0] == "feature/login"


async def test_invalid_worktree_name_rejected(monkeypatch):
    _patch_runner(monkeypatch)
    mgr = _mgr(known={"r": _isolated_role()}, worktree=_FakeWorktree())
    res = await mgr.delegate(
        "defined", "x", role="r", worktree="../x")
    assert "名称非法" in res.error
    assert mgr.tasks() == []


async def test_enter_failure_marks_failed(monkeypatch):
    _patch_runner(monkeypatch)
    wt = _FakeWorktree(enter_error=RuntimeError("boom"))
    mgr = _mgr(known={"r": _isolated_role()}, worktree=wt)
    res = await mgr.delegate("defined", "x", role="r")
    assert "Worktree 创建失败" in res.error
    assert mgr.tasks()[0].status == "failed"


async def test_runner_failure_still_leaves_and_reports(monkeypatch):
    _patch_runner(monkeypatch, error=SubAgentError("模型炸了"))
    wt = _FakeWorktree(keep=True)
    mgr = _mgr(known={"r": _isolated_role()}, worktree=wt)
    res = await mgr.delegate("defined", "x", role="r")
    assert res.error
    assert wt.left == ["r-1"]
    assert "[Worktree]" in mgr.tasks()[0].error


async def test_worktree_disabled_rejected(monkeypatch):
    _patch_runner(monkeypatch)
    mgr = _mgr(known={"r": _isolated_role()})
    res = await mgr.delegate("defined", "x", role="r")
    assert "Worktree 系统未启用" in res.error


async def test_cancel_actually_stops_background_coroutine(monkeypatch):
    """state19：cancel 应真正取消后台 asyncio 协程，而非只改状态。

    旧实现只改 status；后台协程继续跑，完成时 _on_background_done 会覆盖回
    done。改动后：cancel 调 task.cancel()，协程被中断，status 保持 cancelled。
    """
    ran_to_completion = {"v": False}

    async def _never_complete(role, prompt, *, depth, shared, worktree=None):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        ran_to_completion["v"] = True
        return _result("would-be-done")

    monkeypatch.setattr(manager_mod, "run_role_agent", _never_complete)
    mgr = _mgr()
    await mgr.delegate("defined", "x", role="r", background=True)
    tid = mgr.tasks()[0].task_id
    # 等协程真正启动并进入 sleep
    await asyncio.sleep(0.05)
    assert mgr.cancel(tid) is True
    # 给被取消的协程一点时间收尾
    await asyncio.sleep(0.1)
    assert mgr.tasks()[0].status == "cancelled"
    assert ran_to_completion["v"] is False, "后台协程未被真正取消，仍跑到了完成"
