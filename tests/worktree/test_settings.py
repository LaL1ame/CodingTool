"""`.worktree.yaml` 读取与校验。"""

import pytest

from codepilot.worktree.settings import (
    DEFAULT_COPY,
    DEFAULT_SYMLINK,
    WorktreeSettingsError,
    load_settings,
)


def _write(tmp_path, text):
    (tmp_path / ".worktree.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def test_missing_file_returns_defaults(tmp_path):
    s = load_settings(tmp_path)
    assert s.ttl_hours == 24.0
    assert s.cleanup_interval_minutes == 60.0
    assert s.copy == DEFAULT_COPY
    assert s.symlink == DEFAULT_SYMLINK
    assert s.include == ()


def test_empty_file_returns_defaults(tmp_path):
    _write(tmp_path, "")
    s = load_settings(tmp_path)
    assert s.copy == DEFAULT_COPY


def test_override_replaces_defaults(tmp_path):
    _write(tmp_path, """
worktree:
  ttl_hours: 12
  cleanup_interval_minutes: 30
  copy: [config.yaml]
  symlink: [.venv]
  include: [".env*", "data/local.json"]
""")
    s = load_settings(tmp_path)
    assert s.ttl_hours == 12
    assert s.cleanup_interval_minutes == 30
    assert s.copy == ("config.yaml",)
    assert s.symlink == (".venv",)
    assert s.include == (".env*", "data/local.json")


def test_partial_override_keeps_other_defaults(tmp_path):
    _write(tmp_path, "worktree:\n  ttl_hours: 6\n")
    s = load_settings(tmp_path)
    assert s.ttl_hours == 6
    assert s.copy == DEFAULT_COPY
    assert s.symlink == DEFAULT_SYMLINK


@pytest.mark.parametrize("text", [
    "worktree: []",
    "worktree:\n  ttl_hours: 0",
    "worktree:\n  ttl_hours: -1",
    "worktree:\n  ttl_hours: abc",
    "worktree:\n  cleanup_interval_minutes: 0",
    "worktree:\n  copy: config.yaml",
    "worktree:\n  copy: [1]",
    "worktree:\n  copy: [/abs]",
    "worktree:\n  copy: [../x]",
    "worktree:\n  symlink: [a\\\\b]",
    "worktree:\n  include: [.]",
    "worktree:\n  include: [./]",
])
def test_invalid_configs(tmp_path, text):
    _write(tmp_path, text)
    with pytest.raises(WorktreeSettingsError):
        load_settings(tmp_path)


def test_yaml_error(tmp_path):
    _write(tmp_path, "worktree: [")
    with pytest.raises(WorktreeSettingsError):
        load_settings(tmp_path)
