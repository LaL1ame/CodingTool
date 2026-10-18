"""小组/成员名校验与 worktree 名映射。"""

import pytest

from codepilot.teams.model import TeamNameError
from codepilot.teams.naming import (
    NAME_MAX,
    member_worktree_name,
    validate_member_name,
    validate_team_name,
)


@pytest.mark.parametrize("name", ["a", "team1", "a.b", "a_b", "a-b", "x" * NAME_MAX])
def test_valid_names(name):
    assert validate_team_name(name) == name
    assert validate_member_name(name) == name


@pytest.mark.parametrize("name", [
    "", " ", "A", "Team", ".", "..", ".a", "-a", "a/b", "a\\b",
    "a.", "a ", " a", "con", "CON", "con.txt", "nul", "com1",
    "x" * (NAME_MAX + 1), "a@b", "中文",
])
def test_invalid_names(name):
    with pytest.raises(TeamNameError):
        validate_team_name(name)
    with pytest.raises(TeamNameError):
        validate_member_name(name)


def test_lead_reserved():
    assert validate_team_name("lead") == "lead"
    with pytest.raises(TeamNameError):
        validate_member_name("lead")


def test_worktree_name_is_nested_and_valid():
    name = member_worktree_name("alpha", "bob")
    assert name == "team/alpha/bob"
    assert len(name) <= 64


def test_worktree_name_long_but_valid():
    team = "t" * NAME_MAX
    member = "m" * NAME_MAX
    name = member_worktree_name(team, member)
    assert name == f"team/{team}/{member}"
    assert len(name) <= 64
