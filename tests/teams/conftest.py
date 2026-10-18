"""团队测试共用 fixture：临时根目录、已初始化的小组。"""

from __future__ import annotations

from pathlib import Path

import pytest

from codepilot.teams.model import TeamSettings
from codepilot.teams.storage import TeamStorage


@pytest.fixture
def settings(tmp_path: Path) -> TeamSettings:
    return TeamSettings(root=tmp_path / "teams")


@pytest.fixture
def storage(settings: TeamSettings) -> TeamStorage:
    return TeamStorage(settings.root, settings)


@pytest.fixture
def team(storage: TeamStorage) -> str:
    """初始化一个空小组 alpha（team.json + tasks.json）。"""
    name = "alpha"
    storage.ensure_team_layout(name)
    storage.atomic_write_json(
        storage.team_json(name),
        {
            "name": name,
            "lead": "lead",
            "members": [],
            "created_at": 1.0,
            "next_member_seq": 1,
        },
    )
    storage.atomic_write_json(
        storage.tasks_json(name), {"next_id": 1, "tasks": {}})
    return name
