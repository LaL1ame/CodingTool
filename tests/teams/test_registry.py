"""名称注册表：解析、重建、离线标记与元数据更新。"""

from pathlib import Path

import pytest

from codepilot.teams.model import (
    TeamMember,
    TeamMemberBackend,
    TeamMemberError,
    TeamMemberStatus,
)
from codepilot.teams.registry import MemberRegistry
from codepilot.teams.storage import TeamStorage


def _member(name: str, tmp_path: Path, **kw) -> TeamMember:
    return TeamMember(
        name=name,
        role="coder",
        worktree_path=kw.get("worktree_path", tmp_path / f"wt-{name}"),
        branch=kw.get("branch", f"codepilot/team/alpha/{name}"),
        backend=TeamMemberBackend.INPROCESS,
        requires_approval=kw.get("requires_approval", False),
        status=kw.get("status", TeamMemberStatus.IDLE),
        created_at=1.0,
        updated_at=1.0,
    )


@pytest.fixture
def registry(
    storage: TeamStorage, team: str, tmp_path: Path,
) -> MemberRegistry:
    return MemberRegistry(storage, team, workspace=tmp_path)


def test_add_and_resolve(
    registry: MemberRegistry, tmp_path: Path, storage: TeamStorage,
):
    registry.add_member(_member("alice", tmp_path))
    ref = registry.resolve("alice")
    assert ref.name == "alice"
    assert ref.branch == "codepilot/team/alpha/alice"
    assert ref.mailbox_path == storage.mailbox_path("alpha", "alice")
    assert registry.resolve("lead").name == "lead"
    assert registry.team_meta().members == ("alice",)
    assert registry.team_meta().next_member_seq == 2
    assert [r.name for r in registry.list()] == ["alice"]


def test_duplicate_and_unknown(registry: MemberRegistry, tmp_path: Path):
    registry.add_member(_member("alice", tmp_path))
    with pytest.raises(TeamMemberError):
        registry.add_member(_member("alice", tmp_path))
    with pytest.raises(TeamMemberError):
        registry.resolve("ghost")
    with pytest.raises(TeamMemberError):
        registry.resolve_active("ghost")


def test_terminated_and_offline_not_active(
    registry: MemberRegistry, tmp_path: Path,
):
    registry.add_member(_member(
        "alice", tmp_path, status=TeamMemberStatus.TERMINATED))
    with pytest.raises(TeamMemberError, match="已终止"):
        registry.resolve_active("alice")
    registry.add_member(_member(
        "bob", tmp_path, status=TeamMemberStatus.OFFLINE))
    with pytest.raises(TeamMemberError, match="不可达"):
        registry.resolve_active("bob")
    assert not registry.is_assignable("alice")
    assert not registry.is_assignable("bob")


def test_update_reflects_in_registry(
    registry: MemberRegistry, tmp_path: Path, storage: TeamStorage,
):
    registry.add_member(_member("alice", tmp_path))
    registry.update("alice", status=TeamMemberStatus.BLOCKED, note="waiting")
    ref = registry.resolve("alice")
    assert ref.status is TeamMemberStatus.BLOCKED
    raw = storage.read_json(storage.registry_json("alpha"))
    assert raw["members"]["alice"]["status"] == "blocked"


def test_rebuild_after_registry_loss(
    registry: MemberRegistry, tmp_path: Path, storage: TeamStorage,
):
    registry.add_member(_member("alice", tmp_path))
    storage.registry_json("alpha").unlink()
    ref = registry.resolve("alice")
    assert ref.name == "alice"
    assert storage.registry_json("alpha").exists()


def test_mark_all_offline(registry: MemberRegistry, tmp_path: Path):
    registry.add_member(_member("alice", tmp_path))
    registry.add_member(_member(
        "bob", tmp_path, status=TeamMemberStatus.TERMINATED))
    refs = {r.name: r.status for r in registry.mark_all_offline()}
    assert refs["alice"] is TeamMemberStatus.OFFLINE
    assert refs["bob"] is TeamMemberStatus.TERMINATED


def test_corrupt_member_is_skipped(
    registry: MemberRegistry, tmp_path: Path, storage: TeamStorage,
):
    registry.add_member(_member("alice", tmp_path))
    storage.member_meta_path("alpha", "alice").write_text(
        "{broken", encoding="utf-8")
    assert registry.list() == []
    with pytest.raises(TeamMemberError):
        registry.resolve("alice")


def test_update_unknown_field(registry: MemberRegistry, tmp_path: Path):
    registry.add_member(_member("alice", tmp_path))
    with pytest.raises(TeamMemberError):
        registry.update("alice", wild=1)
