"""Worktree 测试公共 fixture。"""

import shutil
import subprocess

import pytest


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace")


@pytest.fixture
def git_repo(tmp_path):
    """一个已提交初始内容的临时 Git 仓库。"""
    if shutil.which("git") is None:
        pytest.skip("git 不可用")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (repo / ".gitignore").write_text(
        ".codepilot/\nignored.txt\n.venv/\n.env*\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo
