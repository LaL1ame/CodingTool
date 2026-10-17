"""GitRunner 行为测试（真实临时仓库，不 mock Git）。"""

import subprocess

import pytest

from codepilot.worktree.git import GitError, GitRunner


def _wt_path(repo, name="probe"):
    return repo / ".codepilot" / "worktrees" / name


def test_toplevel_and_head(git_repo):
    g = GitRunner(git_repo)
    assert g.toplevel() == git_repo.resolve()
    assert len(g.head()) == 40


def test_not_a_repo_returns_none(tmp_path):
    g = GitRunner(tmp_path)
    assert g.toplevel() is None


def test_branch_exists(git_repo):
    g = GitRunner(git_repo)
    assert g.branch_exists("codepilot/x") is False
    g.add_worktree(_wt_path(git_repo), "codepilot/x", g.head())
    assert g.branch_exists("codepilot/x") is True


def test_add_and_remove_worktree(git_repo):
    g = GitRunner(git_repo)
    path = _wt_path(git_repo)
    g.add_worktree(path, "codepilot/x", g.head())
    assert (path / "a.txt").is_file()
    assert (path / ".git").is_file()
    g.remove_worktree(path)
    assert not path.exists()
    g.delete_branch("codepilot/x")
    assert not g.branch_exists("codepilot/x")


def test_attach_existing_branch(git_repo):
    g = GitRunner(git_repo)
    subprocess.run(["git", "branch", "codepilot/existing"], cwd=git_repo, check=True)
    path = _wt_path(git_repo, "existing")
    g.attach_worktree(path, "codepilot/existing")
    assert (path / ".git").is_file()


def test_status_porcelain_and_dirty(git_repo):
    g = GitRunner(git_repo)
    path = _wt_path(git_repo)
    g.add_worktree(path, "codepilot/x", g.head())
    assert g.status_porcelain(path) == ""
    (path / "new.txt").write_text("x", encoding="utf-8")
    assert "new.txt" in g.status_porcelain(path)


def test_commits_ahead_uses_worktree_cwd(git_repo):
    g = GitRunner(git_repo)
    base = g.head()
    path = _wt_path(git_repo)
    g.add_worktree(path, "codepilot/x", base)
    assert g.commits_ahead(base, cwd=path) == 0
    (path / "new.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=path, check=True)
    assert g.commits_ahead(base, cwd=path) == 1
    # 主仓 HEAD 未移动，证明没有误用主仓 HEAD
    assert g.commits_ahead(base, cwd=git_repo) == 0


def test_merge_base(git_repo):
    g = GitRunner(git_repo)
    head = g.head()
    assert g.merge_base("HEAD", head) == head
    assert g.merge_base("HEAD", "no-such-ref") is None


def test_is_ignored(git_repo):
    g = GitRunner(git_repo)
    (git_repo / "ignored.txt").write_text("x", encoding="utf-8")
    assert g.is_ignored(git_repo / "ignored.txt") is True
    assert g.is_ignored(git_repo / "a.txt") is False


def test_hooks_path_and_worktree_config(git_repo):
    g = GitRunner(git_repo)
    assert g.hooks_path() is None
    path = _wt_path(git_repo)
    g.add_worktree(path, "codepilot/x", g.head())
    g.set_worktree_config(path, "core.hooksPath", ".githooks")
    cp = subprocess.run(
        ["git", "config", "--worktree", "--get", "core.hooksPath"],
        cwd=path, capture_output=True, text=True, check=True)
    assert cp.stdout.strip() == ".githooks"


def test_bad_command_raises_git_error(git_repo):
    g = GitRunner(git_repo)
    with pytest.raises(GitError) as ei:
        g.run(["no-such-git-command"])
    assert "no-such-git-command" in str(ei.value)
