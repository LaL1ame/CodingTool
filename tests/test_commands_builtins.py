"""T6 单测 —— 内置命令注册完整性(F7)。"""
from pathlib import Path
from types import SimpleNamespace

from codepilot.commands.builtins import build_registry
from codepilot.commands.registry import CommandKind


def test_builtin_full_set_and_no_conflicts():
    r = build_registry()
    r.assert_no_conflicts()                 # 启动校验:不撞
    names = {s.name for s in r.all()}
    expected = {"help", "exit", "plan", "do", "mode", "auto", "manual", "strict",
                "edits", "compact", "continue", "memory", "forget", "clear",
                "session", "permission", "status", "reload",
                "agents", "bg", "worktree"}  # state17: +agents/bg; state18: +worktree
    assert names == expected


def test_kinds_assignment():
    r = build_registry()
    kinds = {s.name: s.kind for s in r.all()}
    assert kinds["help"] is CommandKind.LOCAL
    assert kinds["status"] is CommandKind.LOCAL
    assert kinds["session"] is CommandKind.LOCAL
    assert kinds["plan"] is CommandKind.UI
    assert kinds["clear"] is CommandKind.UI
    assert kinds["permission"] is CommandKind.UI


def test_aliases_present():
    r = build_registry()
    assert r.resolve("cls").name == "clear"
    assert r.resolve("?") .name == "help"
    assert r.resolve("quit").name == "exit"


def test_no_hidden_in_default_view():
    r = build_registry()
    shown = r.all(include_hidden=False)
    assert all(not s.hidden for s in shown)
    assert len(shown) == len(r.all(include_hidden=True))


# —— state17: /agents /bg ——

class _Ctx:
    def __init__(self, manager="missing"):
        if manager != "missing":
            self.subagent_manager = manager
        self.shown: list[str] = []

    def show(self, message: str) -> None:
        self.shown.append(message)


class _Mgr:
    def tasks(self):
        return [SimpleNamespace(task_id=1, kind="defined", role="architect",
                                status="done", usage=None)]

    def background_now(self, task_id):
        return task_id == 1


def test_agents_lists_tasks():
    ctx = _Ctx(_Mgr())
    build_registry().resolve("agents").handler(ctx, "")
    assert len(ctx.shown) == 1
    assert "#1" in ctx.shown[0]
    assert "architect" in ctx.shown[0]


def test_agents_not_enabled_degrades():
    ctx = _Ctx()  # 无 subagent_manager 属性 → getattr None
    build_registry().resolve("agents").handler(ctx, "")
    assert "未启用" in ctx.shown[0]


def test_bg_switches_task():
    ctx = _Ctx(_Mgr())
    build_registry().resolve("bg").handler(ctx, "1")
    assert "切到后台" in ctx.shown[0]


def test_bg_invalid_and_unknown():
    ctx = _Ctx(_Mgr())
    build_registry().resolve("bg").handler(ctx, "")
    assert "用法" in ctx.shown[-1]
    build_registry().resolve("bg").handler(ctx, "abc")
    assert "无效 task_id" in ctx.shown[-1]
    build_registry().resolve("bg").handler(ctx, "99")
    assert "不在前台等待中" in ctx.shown[-1]


def test_bg_not_enabled_degrades():
    ctx = _Ctx()  # 无 manager → 安全降级
    build_registry().resolve("bg").handler(ctx, "1")
    assert "未启用" in ctx.shown[-1]


# —— state18: /worktree ——

class _WTCtx(_Ctx):
    def __init__(self, manager=None):
        super().__init__()
        if manager is not None:
            self.worktree_manager = manager


class _WTMgr:
    def __init__(self):
        self.removed = []

    def list_status(self):
        return [SimpleNamespace(
            name="auth-fix", branch="codepilot/auth-fix", locked_by=None,
            dirty=True, ahead=2, path=Path("wt/auth-fix"))]

    def remove(self, name, force=False):
        self.removed.append((name, force))
        return SimpleNamespace(
            kept=False, reason="removed/clean",
            path=Path("wt") / name, branch=f"codepilot/{name}")


def test_worktree_list_output():
    ctx = _WTCtx(_WTMgr())
    build_registry().resolve("worktree").handler(ctx, "list")
    out = ctx.shown[-1]
    assert "auth-fix" in out
    assert "codepilot/auth-fix" in out
    assert "dirty" in out and "ahead 2" in out


def test_worktree_rm_and_force():
    mgr = _WTMgr()
    ctx = _WTCtx(mgr)
    handler = build_registry().resolve("worktree").handler
    handler(ctx, "rm auth-fix")
    handler(ctx, "rm auth-fix --force")
    assert mgr.removed == [("auth-fix", False), ("auth-fix", True)]
    assert "已删除" in ctx.shown[-1]
    assert "分支保留" in ctx.shown[-1]


def test_worktree_rm_error_reported():
    class _Bad(_WTMgr):
        def remove(self, name, force=False):
            raise RuntimeError("有未提交改动")

    ctx = _WTCtx(_Bad())
    build_registry().resolve("worktree").handler(ctx, "rm x")
    assert "删除失败" in ctx.shown[-1]
    assert "有未提交改动" in ctx.shown[-1]


def test_worktree_usage_and_disabled():
    ctx = _WTCtx(_WTMgr())
    handler = build_registry().resolve("worktree").handler
    handler(ctx, "rm")
    assert "用法" in ctx.shown[-1]
    handler(ctx, "bogus")
    assert "用法" in ctx.shown[-1]
    disabled = _WTCtx()
    handler(disabled, "list")
    assert "未启用" in disabled.shown[-1]