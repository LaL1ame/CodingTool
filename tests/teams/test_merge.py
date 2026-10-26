"""A3 TeamMerger 合并与冲突回滚。"""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from codepilot.teams.merge import TeamMerger, select_mergeable
from codepilot.teams.model import (
    MemberRef, TeamMemberBackend, TeamMemberStatus,
)
from codepilot.worktree.git import GitRunner


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git 不可用")
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init")
    _git(r, "config", "user.email", "t@e.com")
    _git(r, "config", "user.name", "T")
    (r / "base.txt").write_text("base\n", encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-m", "init")
    # 记录初始主分支名（master|main 取决于 git 配置）；合并会把主工作区切走，
    # 测试回主分支必须用初始名，而不是当时的 --show-current。
    main = subprocess.run(["git", "branch", "--show-current"], cwd=r,
                          capture_output=True, text=True, check=True).stdout.strip()
    return SimpleNamespace(path=r, main=main)


def _member_ref(name, branch, worktree):
    return MemberRef(
        name=name, role="coder", status=TeamMemberStatus.IDLE,
        backend=TeamMemberBackend.INPROCESS,
        mailbox_path=worktree / "m.jsonl", worktree_path=worktree,
        branch=branch, requires_approval=False)


def _mk_member_branch(repo, name):
    """建成员分支 + worktree（有文件改动，模拟成员产物）。"""
    branch = f"codepilot/team/alpha/{name}"
    wt = repo.path / f"wt-{name}"
    _git(repo.path, "worktree", "add", "-b", branch, str(wt), "HEAD")
    (wt / f"{name}.txt").write_text(f"{name} content\n", encoding="utf-8")
    return _member_ref(name, branch, wt)


def _mk_conflict(repo, name):
    """建成员分支，在成员 worktree 改 base.txt（各分支不同内容 → merge 冲突）。"""
    branch = f"codepilot/team/alpha/{name}"
    wt = repo.path / f"wt-{name}"
    _git(repo.path, "worktree", "add", "-b", branch, str(wt), "HEAD")
    (wt / "base.txt").write_text(f"{name}-override\n", encoding="utf-8")
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", f"{name} 改 base")
    return _member_ref(name, branch, wt)


def test_merge_branch_name():
    from codepilot.teams.merge import merge_branch_name

    assert merge_branch_name("alpha") == "codepilot/team/alpha/merged"


def test_select_mergeable():
    refs = [
        _member_ref("a", "b1", Path("/tmp/wt-a")),
        MemberRef(
            name="lead", role="lead", status=TeamMemberStatus.IDLE,
            backend=TeamMemberBackend.INPROCESS,
            mailbox_path=Path("/tmp/lead"), worktree_path=Path("/tmp"),
            branch="", requires_approval=False),
        MemberRef(
            name="t", role="coder", status=TeamMemberStatus.TERMINATED,
            backend=TeamMemberBackend.INPROCESS,
            mailbox_path=Path("/tmp/t"), worktree_path=Path("/tmp/t"),
            branch="b", requires_approval=False),
    ]
    out = select_mergeable(refs)
    assert [m.name for m in out] == ["a"]


def test_commit_member_changes(repo):
    g = GitRunner(repo.path)
    ref = _mk_member_branch(repo, "alice")
    merger = TeamMerger(repo.path, g)
    changes = merger.commit_member_changes(ref)
    assert changes.dirty is True
    assert changes.committed is True
    # 成员分支现在有提交（ASCII 前缀稳定指纹）
    out = subprocess.run(["git", "log", "--oneline"], cwd=ref.worktree_path,
                         capture_output=True, text=True, check=True)
    assert "team alice:" in out.stdout


def test_merge_no_conflict_to_merged_branch(repo):
    g = GitRunner(repo.path)
    alice = _mk_member_branch(repo, "alice")
    bob = _mk_member_branch(repo, "bob")
    merger = TeamMerger(repo.path, g)
    result = merger.merge_members("alpha", [alice, bob])
    assert result.ok
    assert result.target == "codepilot/team/alpha/merged"
    assert set(result.merged_members) == {"alice", "bob"}
    assert set(result.committed_members) == {"alice", "bob"}
    # 汇聚分支含两者文件；主分支不含
    g.run(["checkout", "codepilot/team/alpha/merged"], cwd=repo.path)
    assert (repo.path / "alice.txt").exists() and (repo.path / "bob.txt").exists()
    g.run(["checkout", repo.main], cwd=repo.path)
    assert not (repo.path / "alice.txt").exists(), "主分支不应被污染"


def test_merge_conflict_rolls_back(repo):
    g = GitRunner(repo.path)
    left = _mk_conflict(repo, "left")   # base.txt -> left-override
    right = _mk_conflict(repo, "right")  # base.txt -> right-override
    merger = TeamMerger(repo.path, g)
    first = merger.merge_members("alpha", [left])
    assert first.ok
    second = merger.merge_members("alpha", [right])
    assert second.ok is False
    assert "冲突" in second.error or second.conflict_files
    # 主分支回到干净 base
    _git(repo.path, "checkout", repo.main)
    assert (repo.path / "base.txt").read_text(encoding="utf-8") == "base\n"


def test_merge_target_is_created(repo):
    g = GitRunner(repo.path)
    bob = _mk_member_branch(repo, "bob")
    merger = TeamMerger(repo.path, g)
    result = merger.merge_members("alpha", [bob])
    assert result.ok and result.target == "codepilot/team/alpha/merged"
    assert g.branch_created("codepilot/team/alpha/merged")