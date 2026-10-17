"""WorktreeManager 生命周期、保留判定与清理。"""

import asyncio
import subprocess
import time
from dataclasses import replace

import pytest

from codepilot.worktree.git import GitRunner
from codepilot.worktree.manager import (
    WorktreeError,
    WorktreeManager,
    decide_retention,
)
from codepilot.worktree.meta import read_meta, write_meta
from codepilot.worktree.model import WorktreeMeta
from codepilot.worktree.naming import WorktreeNameError
from codepilot.worktree.settings import WorktreeSettings


class _NoGit:
    """快速恢复路径一旦调用 Git 就失败。"""

    def __getattr__(self, name):
        raise AssertionError(f"快速恢复不应调用 Git.{name}")


def _mgr(repo, **kw):
    return WorktreeManager(repo, **kw)


def _enter(mgr, name="t", role="r", task_id=1):
    return mgr.enter(name, role=role, task_id=task_id)


def _age_meta(mgr, name, hours=25):
    meta = read_meta(mgr.root, name)
    write_meta(mgr.root, replace(
        meta, created_at=time.time() - hours * 3600))


def _commit_in(path, text="change"):
    (path / "new.txt").write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=path, check=True)


# —— T8: 快速恢复 / 校验 / 占用 ——

def test_missing_dir_creates_and_fast_recovery(git_repo):
    mgr = _mgr(git_repo)
    h1 = _enter(mgr, "auth-fix")
    assert not h1.reused and not h1.attached
    assert (h1.path / "a.txt").is_file()
    assert GitRunner(git_repo).branch_exists("codepilot/auth-fix")

    # 新 manager + 会抛错的 Git：快速恢复不应触发任何 Git 调用
    mgr2 = WorktreeManager(git_repo, git=_NoGit())
    h2 = _enter(mgr2, "auth-fix", task_id=2)
    assert h2.reused
    assert h2.path == h1.path
    assert h2.branch == h1.branch
    assert mgr.leave(h1).kept is False


def test_foreign_directory_rejected(git_repo):
    path = git_repo / ".codepilot" / "worktrees" / "foreign"
    path.mkdir(parents=True)
    with pytest.raises(WorktreeError):
        _enter(_mgr(git_repo), "foreign")


def test_dir_with_git_but_no_meta_rejected(git_repo):
    path = git_repo / ".codepilot" / "worktrees" / "x"
    path.mkdir(parents=True)
    (path / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")
    with pytest.raises(WorktreeError):
        _enter(_mgr(git_repo), "x")


def test_occupied_name_rejected(git_repo):
    mgr = _mgr(git_repo)
    _enter(mgr, "x", task_id=3)
    with pytest.raises(WorktreeError) as ei:
        _enter(mgr, "x", task_id=4)
    assert "#3" in str(ei.value)


def test_invalid_name_rejected(git_repo):
    with pytest.raises(WorktreeNameError):
        _enter(_mgr(git_repo), "../x")


def test_non_repo_rejected(tmp_path):
    with pytest.raises(WorktreeError):
        _enter(_mgr(tmp_path), "x")


# —— T9: 创建 / 挂接 / 失败清理 ——

def test_new_branch_base_and_meta(git_repo):
    base = GitRunner(git_repo).head()
    mgr = _mgr(git_repo)
    h = _enter(mgr, "x", role="architect", task_id=9)
    assert h.base == base
    meta = read_meta(mgr.root, "x")
    assert meta == WorktreeMeta(
        name="x", branch="codepilot/x", base=base,
        created_at=h.created_at, role="architect", task_id=9)
    mgr.leave(h)


def test_auto_name(git_repo):
    mgr = _mgr(git_repo)
    h = _enter(mgr, None, role="Architect", task_id=7)
    assert h.name == "architect-7"
    mgr.leave(h)


def test_attach_existing_branch(git_repo):
    subprocess.run(
        ["git", "branch", "codepilot/existing"], cwd=git_repo, check=True)
    mgr = _mgr(git_repo)
    h = _enter(mgr, "existing")
    assert h.attached is True
    assert h.reused is False
    assert (h.path / "a.txt").is_file()
    mgr.leave(h)


def test_branch_checked_out_elsewhere_fails(git_repo):
    g = GitRunner(git_repo)
    other = git_repo / ".codepilot" / "worktrees" / "other"
    g.add_worktree(other, "codepilot/x", g.head())
    mgr = _mgr(git_repo)
    with pytest.raises(WorktreeError):
        _enter(mgr, "x")
    assert not (git_repo / ".codepilot" / "worktrees" / "x").exists()
    # 失败后锁已释放：修掉占用后可再次进入
    g.remove_worktree(other)
    h = _enter(mgr, "x")
    mgr.leave(h)


def test_init_failure_cleans_up(git_repo):
    mgr = _mgr(git_repo)

    def _boom(_path):
        raise RuntimeError("init boom")

    mgr._initializer.initialize = _boom
    with pytest.raises(WorktreeError) as ei:
        _enter(mgr, "x")
    assert "init boom" in str(ei.value)
    root = git_repo / ".codepilot" / "worktrees"
    assert not (root / "x").exists()
    assert not GitRunner(git_repo).branch_exists("codepilot/x")
    assert read_meta(mgr.root, "x") is None

    mgr._initializer.initialize = lambda _p: None
    h = _enter(mgr, "x")
    mgr.leave(h)


# —— T10: 保留 / 删除 ——

def test_decide_retention_table():
    assert decide_retention(dirty=False, ahead=0).keep is False
    assert decide_retention(dirty=True, ahead=0).keep is True
    assert decide_retention(dirty=False, ahead=2).keep is True


def test_leave_clean_removes_everything(git_repo):
    mgr = _mgr(git_repo)
    h = _enter(mgr, "x")
    res = mgr.leave(h)
    assert res.kept is False
    assert not h.path.exists()
    assert not GitRunner(git_repo).branch_exists("codepilot/x")
    assert read_meta(mgr.root, "x") is None


def test_leave_dirty_keeps(git_repo):
    mgr = _mgr(git_repo)
    h = _enter(mgr, "x")
    (h.path / "dirty.txt").write_text("x", encoding="utf-8")
    res = mgr.leave(h)
    assert res.kept is True
    assert "dirty" in res.reason
    assert h.path.exists()
    assert GitRunner(git_repo).branch_exists("codepilot/x")
    mgr.remove("x", force=True)
    assert not h.path.exists()
    assert GitRunner(git_repo).branch_exists("codepilot/x")


def test_leave_ahead_keeps(git_repo):
    mgr = _mgr(git_repo)
    h = _enter(mgr, "x")
    _commit_in(h.path)
    res = mgr.leave(h)
    assert res.kept is True
    assert "ahead" in res.reason
    mgr.remove("x", force=True)


def test_remove_refuses_dirty_and_force_keeps_branch(git_repo):
    mgr = _mgr(git_repo)
    h = _enter(mgr, "x")
    (h.path / "dirty.txt").write_text("x", encoding="utf-8")
    mgr.leave(h)
    with pytest.raises(WorktreeError):
        mgr.remove("x")
    res = mgr.remove("x", force=True)
    assert res.kept is False
    assert not h.path.exists()
    assert GitRunner(git_repo).branch_exists("codepilot/x")


def test_remove_clean_deletes_branch(git_repo):
    mgr1 = _mgr(git_repo)
    h = _enter(mgr1, "x")
    mgr2 = _mgr(git_repo)  # 独立 manager 模拟跨进程视角
    res = mgr2.remove("x")
    assert res.kept is False
    assert not h.path.exists()
    assert not GitRunner(git_repo).branch_exists("codepilot/x")


def test_remove_occupied_rejected(git_repo):
    mgr = _mgr(git_repo)
    h = _enter(mgr, "x")
    with pytest.raises(WorktreeError):
        mgr.remove("x")
    mgr.leave(h)


def test_remove_foreign_rejected(git_repo):
    path = git_repo / ".codepilot" / "worktrees" / "foreign"
    path.mkdir(parents=True)
    with pytest.raises(WorktreeError):
        _mgr(git_repo).remove("foreign")


# —— T11: 列表 / 清理 / 周期 ——

def test_list_status(git_repo):
    mgr = _mgr(git_repo)
    h1 = _enter(mgr, "a", task_id=1)
    h2 = _enter(mgr, "b", task_id=2)
    (h2.path / "dirty.txt").write_text("x", encoding="utf-8")
    entries = {e.name: e for e in mgr.list_status()}
    assert entries["a"].locked_by == 1
    assert entries["a"].dirty is False
    assert entries["b"].locked_by == 2
    assert entries["b"].dirty is True
    mgr.leave(h1)
    mgr.leave(h2)
    mgr.remove("b", force=True)


def test_list_skips_missing_dir(git_repo):
    mgr = _mgr(git_repo)
    write_meta(mgr.root, WorktreeMeta(
        name="gone", branch="codepilot/gone", base="x",
        created_at=time.time()))
    assert mgr.list_status() == []


def test_cleanup_removes_expired_clean(git_repo):
    owner = _mgr(git_repo)
    h = _enter(owner, "old")
    _age_meta(owner, "old")
    cleaner = _mgr(git_repo)
    report = cleaner.cleanup_expired()
    assert report.removed == ("old",)
    assert not h.path.exists()
    assert not GitRunner(git_repo).branch_exists("codepilot/old")


def test_cleanup_skips_fresh(git_repo):
    owner = _mgr(git_repo)
    h = _enter(owner, "fresh")
    report = _mgr(git_repo).cleanup_expired()
    assert report.removed == ()
    owner.leave(h)


def test_cleanup_skips_dirty(git_repo):
    owner = _mgr(git_repo)
    h = _enter(owner, "dirty")
    (h.path / "dirty.txt").write_text("x", encoding="utf-8")
    _age_meta(owner, "dirty")
    report = _mgr(git_repo).cleanup_expired()
    assert report.removed == ()
    assert any(name == "dirty" for name, _ in report.skipped)
    owner.leave(h)  # 释放占用锁（dirty 会保留）
    owner.remove("dirty", force=True)


def test_cleanup_skips_occupied(git_repo):
    mgr = _mgr(git_repo)
    _enter(mgr, "busy")
    _age_meta(mgr, "busy")
    report = mgr.cleanup_expired()
    assert report.removed == ()
    assert ("busy", "占用中") in report.skipped


def test_cleanup_skips_foreign(git_repo):
    mgr = _mgr(git_repo)
    path = mgr.root / "foreign"
    path.mkdir(parents=True)
    write_meta(mgr.root, WorktreeMeta(
        name="foreign", branch="codepilot/foreign", base="x",
        created_at=time.time() - 25 * 3600))
    report = mgr.cleanup_expired()
    assert report.removed == ()
    assert any(name == "foreign" for name, _ in report.skipped)


async def test_periodic_cleanup_runs_immediately(git_repo):
    owner = _mgr(git_repo)
    h = _enter(owner, "old")
    _age_meta(owner, "old")
    cleaner = WorktreeManager(
        git_repo, WorktreeSettings(ttl_hours=24,
                                   cleanup_interval_minutes=0.001))
    task = asyncio.create_task(cleaner.run_periodic_cleanup())
    try:
        for _ in range(20):
            await asyncio.sleep(0.05)
            if not h.path.exists():
                break
        assert not h.path.exists()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
