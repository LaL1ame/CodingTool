"""TeamSettings 读取、默认值与后端选择。"""

from pathlib import Path

import pytest

from codepilot.teams.model import (
    TeamBackendError,
    TeamMemberBackend,
    TeamSettingsError,
)
from codepilot.teams.settings import (
    DEFAULT_ROOT,
    load_team_settings,
    select_backend,
)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_uses_defaults(tmp_path: Path):
    s = load_team_settings(tmp_path / "nope.yaml")
    assert s.root == DEFAULT_ROOT.resolve()
    assert s.default_backend == "auto"
    assert s.approval_timeout_seconds == 600.0


def test_section_overrides(tmp_path: Path):
    root = tmp_path / "myteams"
    path = _write(tmp_path, f"""
team:
  root: {root.as_posix()}
  approval_timeout_seconds: 12
  lock_timeout_seconds: 2
  message_summary_chars: 30
  default_backend: inprocess
""")
    s = load_team_settings(path)
    assert s.root == root.resolve()
    assert s.approval_timeout_seconds == 12.0
    assert s.lock_timeout_seconds == 2.0
    assert s.message_summary_chars == 30
    assert s.default_backend == "inprocess"


@pytest.mark.parametrize("text", [
    "team:\n  approval_timeout_seconds: 0\n",
    "team:\n  approval_timeout_seconds: -1\n",
    "team:\n  approval_timeout_seconds: true\n",
    "team:\n  lock_retry_seconds: nope\n",
    "team:\n  default_backend: magic\n",
    "team: []\n",
])
def test_invalid_settings(tmp_path: Path, text: str):
    with pytest.raises(TeamSettingsError):
        load_team_settings(_write(tmp_path, text))


def test_backend_selection(tmp_path: Path):
    s = load_team_settings(tmp_path / "none.yaml")
    assert select_backend("auto", s) is TeamMemberBackend.INPROCESS
    assert select_backend("inprocess", s) is TeamMemberBackend.INPROCESS
    assert select_backend(None, s) is TeamMemberBackend.INPROCESS
    with pytest.raises(TeamBackendError):
        select_backend("terminal", s)
    with pytest.raises(TeamBackendError):
        select_backend("unknown", s)
