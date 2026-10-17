"""环境初始化 copy / include / symlink / hooks。"""

import subprocess

import pytest

from codepilot.worktree.git import GitRunner
from codepilot.worktree.initializer import Initializer, WorktreeInitError
from codepilot.worktree.settings import WorktreeSettings


def _add_wt(repo, name="t"):
    g = GitRunner(repo)
    path = repo / ".codepilot" / "worktrees" / name
    g.add_worktree(path, f"codepilot/{name}", g.head())
    return g, path


def _run(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_copy_configs(git_repo):
    (git_repo / "config.yaml").write_text("secret: 1\n", encoding="utf-8")
    (git_repo / ".hooks.yaml").write_text("hooks: []\n", encoding="utf-8")
    (git_repo / "codepilot-permissions.local.yaml").write_text(
        "rules: []\n", encoding="utf-8")
    g, wt = _add_wt(git_repo)
    Initializer(git_repo, WorktreeSettings(), g).initialize(wt)
    assert (wt / "config.yaml").read_text(encoding="utf-8") == "secret: 1\n"
    assert (wt / ".hooks.yaml").is_file()
    assert (wt / "codepilot-permissions.local.yaml").is_file()


def test_copy_does_not_overwrite(git_repo):
    (git_repo / "config.yaml").write_text("new\n", encoding="utf-8")
    g, wt = _add_wt(git_repo)
    (wt / "config.yaml").write_text("old\n", encoding="utf-8")
    Initializer(git_repo, WorktreeSettings(), g).initialize(wt)
    assert (wt / "config.yaml").read_text(encoding="utf-8") == "old\n"


def test_include_only_copies_ignored(git_repo):
    (git_repo / "ignored.txt").write_text("ignored data\n", encoding="utf-8")
    g, wt = _add_wt(git_repo)
    (wt / "tracked.txt").unlink()  # 若被错误复制，测试可观察到它重新出现
    settings = WorktreeSettings(
        copy=(), symlink=(), include=("ignored.txt", "tracked.txt"))
    Initializer(git_repo, settings, g).initialize(wt)
    assert (wt / "ignored.txt").read_text(encoding="utf-8") == "ignored data\n"
    assert not (wt / "tracked.txt").exists()


def test_symlink_dependency_dir(git_repo):
    venv = git_repo / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    g, wt = _add_wt(git_repo)
    Initializer(git_repo, WorktreeSettings(copy=(), include=()), g).initialize(wt)
    assert (wt / ".venv" / "bin" / "python").is_file()


def test_missing_source_is_skipped(git_repo):
    g, wt = _add_wt(git_repo)
    settings = WorktreeSettings(copy=("no-such-file.txt",), symlink=(), include=())
    Initializer(git_repo, settings, g).initialize(wt)  # 不抛


def test_escape_rule_rejected(git_repo, tmp_path):
    (tmp_path / "outside.txt").write_text("x", encoding="utf-8")
    g, wt = _add_wt(git_repo)
    settings = WorktreeSettings(copy=("../outside.txt",), symlink=(), include=())
    with pytest.raises(WorktreeInitError):
        Initializer(git_repo, settings, g).initialize(wt)


def test_copy_io_failure_raises(git_repo, monkeypatch):
    (git_repo / "config.yaml").write_text("x", encoding="utf-8")
    g, wt = _add_wt(git_repo)

    def _boom(*a, **k):
        raise OSError("disk full")

    import codepilot.worktree.initializer as init_mod
    monkeypatch.setattr(init_mod.shutil, "copy2", _boom)
    with pytest.raises(WorktreeInitError):
        Initializer(git_repo, WorktreeSettings(), g).initialize(wt)


def test_githooks_linked_and_configured(git_repo):
    hooks = git_repo / ".githooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    g, wt = _add_wt(git_repo)
    Initializer(git_repo, WorktreeSettings(copy=(), symlink=(), include=()),
                g).initialize(wt)
    assert (wt / ".githooks" / "pre-commit").is_file()
    cp = subprocess.run(
        ["git", "config", "--worktree", "--get", "core.hooksPath"],
        cwd=wt, capture_output=True, text=True, check=True)
    assert cp.stdout.strip() == str(hooks.resolve())


def test_relative_hooks_path_made_absolute(git_repo):
    _run(git_repo, "config", "core.hooksPath", ".githooks")
    g, wt = _add_wt(git_repo)
    Initializer(git_repo, WorktreeSettings(copy=(), symlink=(), include=()),
                g).initialize(wt)
    cp = subprocess.run(
        ["git", "config", "--worktree", "--get", "core.hooksPath"],
        cwd=wt, capture_output=True, text=True, check=True)
    assert cp.stdout.strip() == str((git_repo / ".githooks").resolve())


def test_default_hooks_no_config(git_repo):
    g, wt = _add_wt(git_repo)
    Initializer(git_repo, WorktreeSettings(copy=(), symlink=(), include=()),
                g).initialize(wt)
    cp = subprocess.run(
        ["git", "config", "--worktree", "--get", "core.hooksPath"],
        cwd=wt, capture_output=True, text=True)
    assert cp.returncode != 0
