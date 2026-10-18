"""名称注册表：两段式消息投递的第一段，成员元数据的读写与重建。

`team.json` + `members/<name>.json` 是真源；`registry.json` 是派生索引。
索引缺失、损坏或与成员文件不一致时从真源重建。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from codepilot.teams.locks import FileLock
from codepilot.teams.model import (
    MemberRef,
    TeamMember,
    TeamMemberBackend,
    TeamMemberError,
    TeamMemberStatus,
    TeamMeta,
)
from codepilot.teams.naming import (
    MEMBER_NAME_RESERVED,
    validate_member_name,
    validate_team_name,
)
from codepilot.teams.storage import TeamStorage

logger = logging.getLogger("codepilot.teams.registry")

_TERMINAL_STATES = {TeamMemberStatus.TERMINATED}
_UNREACHABLE_STATES = {
    TeamMemberStatus.OFFLINE,
    TeamMemberStatus.FAILED,
}


def _member_to_dict(member: TeamMember) -> dict[str, Any]:
    return {
        "name": member.name,
        "role": member.role,
        "worktree_path": str(member.worktree_path),
        "branch": member.branch,
        "backend": member.backend.value,
        "requires_approval": member.requires_approval,
        "status": member.status.value,
        "created_at": member.created_at,
        "updated_at": member.updated_at,
        "note": member.note,
    }


def _member_from_dict(raw: object) -> TeamMember | None:
    if not isinstance(raw, dict):
        return None
    try:
        return TeamMember(
            name=validate_member_name(str(raw["name"])),
            role=str(raw["role"]),
            worktree_path=Path(str(raw["worktree_path"])),
            branch=str(raw["branch"]),
            backend=TeamMemberBackend(str(raw["backend"])),
            requires_approval=bool(raw.get("requires_approval", False)),
            status=TeamMemberStatus(str(raw["status"])),
            created_at=float(raw.get("created_at", 0.0)),
            updated_at=float(raw.get("updated_at", 0.0)),
            note=str(raw.get("note", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


class MemberRegistry:
    def __init__(
        self,
        storage: TeamStorage,
        team: str,
        *,
        workspace: Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        validate_team_name(team)
        self._storage = storage
        self._team = team
        self._workspace = Path(workspace).resolve()
        self._clock = clock

    @property
    def team(self) -> str:
        return self._team

    def _lock(self) -> FileLock:
        settings = self._storage.settings
        return FileLock(
            self._storage.team_lock_path(self._team),
            timeout=settings.lock_timeout_seconds,
            stale_after=settings.lock_stale_seconds,
            retry=settings.lock_retry_seconds,
            clock=self._clock,
        )

    # —— 真源读取 ——
    def _read_team_meta_unlocked(self) -> TeamMeta:
        path = self._storage.team_json(self._team)
        raw = self._storage.read_json_safe(path, None)
        if isinstance(raw, dict):
            try:
                members_raw = raw.get("members", [])
                if not isinstance(members_raw, list):
                    raise ValueError("members 必须是列表")
                members = tuple(
                    validate_member_name(str(m)) for m in members_raw)
                name = str(raw.get("name", self._team))
                if name != self._team:
                    raise ValueError("team.json 组名不匹配")
                return TeamMeta(
                    name=name,
                    lead=str(raw.get("lead", MEMBER_NAME_RESERVED)),
                    members=members,
                    created_at=float(raw.get("created_at", 0.0)),
                    next_member_seq=int(raw.get("next_member_seq", 1)),
                )
            except (TypeError, ValueError) as e:
                logger.warning("team.json 损坏，尝试从成员文件恢复: %s", e)
                self._storage.backup_corrupt(path)
        # 恢复：扫描 members/*.json
        names: list[str] = []
        members_dir = self._storage.members_dir(self._team)
        if members_dir.exists():
            for p in sorted(members_dir.glob("*.json")):
                try:
                    names.append(validate_member_name(p.stem))
                except Exception:
                    continue
        if not names:
            raise TeamMemberError(
                f"小组 {self._team!r} 的 team.json 损坏且无成员文件可恢复")
        meta = TeamMeta(
            name=self._team, lead=MEMBER_NAME_RESERVED,
            members=tuple(names), created_at=0.0, next_member_seq=1)
        self._write_team_meta_unlocked(meta)
        return meta

    def _write_team_meta_unlocked(self, meta: TeamMeta) -> None:
        self._storage.atomic_write_json(
            self._storage.team_json(self._team),
            {
                "name": meta.name,
                "lead": meta.lead,
                "members": list(meta.members),
                "created_at": meta.created_at,
                "next_member_seq": meta.next_member_seq,
            },
        )

    def _read_member_unlocked(self, name: str) -> TeamMember | None:
        path = self._storage.member_meta_path(self._team, name)
        if not path.exists():
            return None
        raw = self._storage.read_json_safe(path, None)
        member = _member_from_dict(raw)
        if member is None or member.name != name:
            logger.warning("跳过损坏成员元数据: %s", path)
            return None
        return member

    def _iter_members_unlocked(self) -> list[TeamMember]:
        meta = self._read_team_meta_unlocked()
        out: list[TeamMember] = []
        for name in meta.members:
            member = self._read_member_unlocked(name)
            if member is not None:
                out.append(member)
        return out

    # —— 注册表索引 ——
    def _ref_from_member(self, member: TeamMember) -> MemberRef:
        return MemberRef(
            name=member.name,
            role=member.role,
            status=member.status,
            backend=member.backend,
            mailbox_path=self._storage.mailbox_path(
                self._team, member.name),
            worktree_path=member.worktree_path,
            branch=member.branch,
            requires_approval=member.requires_approval,
        )

    def _lead_ref(self) -> MemberRef:
        return MemberRef(
            name=MEMBER_NAME_RESERVED,
            role=MEMBER_NAME_RESERVED,
            status=TeamMemberStatus.IDLE,
            backend=TeamMemberBackend.INPROCESS,
            mailbox_path=self._storage.mailbox_path(
                self._team, MEMBER_NAME_RESERVED),
            worktree_path=self._workspace,
            branch="",
            requires_approval=False,
        )

    def _doc_for_members(self, members: list[TeamMember]) -> dict[str, Any]:
        return {
            "team": self._team,
            "lead": MEMBER_NAME_RESERVED,
            "members": {
                m.name: {
                    "role": m.role,
                    "status": m.status.value,
                    "backend": m.backend.value,
                    "mailbox": str(self._storage.mailbox_path(
                        self._team, m.name)),
                    "worktree": str(m.worktree_path),
                    "branch": m.branch,
                    "requires_approval": m.requires_approval,
                }
                for m in members
            },
        }

    def _registry_doc_unlocked(self) -> dict[str, Any]:
        return self._doc_for_members(self._iter_members_unlocked())

    def _sync_registry_unlocked(self, members: list[TeamMember]) -> None:
        expected = self._doc_for_members(members)
        current = self._storage.read_json_safe(
            self._storage.registry_json(self._team), None)
        if current != expected:
            self._storage.atomic_write_json(
                self._storage.registry_json(self._team), expected)

    def _write_registry_unlocked(self) -> None:
        self._storage.atomic_write_json(
            self._storage.registry_json(self._team),
            self._registry_doc_unlocked(),
        )

    def rebuild(self) -> list[MemberRef]:
        with self._lock():
            members = self._iter_members_unlocked()
            self._write_registry_unlocked()
            return [self._ref_from_member(m) for m in members]

    def list(self) -> list[MemberRef]:
        with self._lock():
            # 以成员元数据为真源；registry.json 只是派生索引。
            members = self._iter_members_unlocked()
            self._sync_registry_unlocked(members)
            return [self._ref_from_member(m) for m in members]

    def _ref_from_entry(self, name: str, entry: object) -> MemberRef | None:
        if not isinstance(entry, dict):
            return None
        try:
            validate_member_name(name)
            return MemberRef(
                name=name,
                role=str(entry.get("role", "")),
                status=TeamMemberStatus(str(entry["status"])),
                backend=TeamMemberBackend(str(entry["backend"])),
                mailbox_path=Path(str(entry["mailbox"])),
                worktree_path=Path(str(entry["worktree"])),
                branch=str(entry.get("branch", "")),
                requires_approval=bool(entry.get("requires_approval", False)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    # —— 解析 ——
    def _find_member_unlocked(self, name: str) -> TeamMember | None:
        meta = self._read_team_meta_unlocked()
        if name not in meta.members:
            return None
        return self._read_member_unlocked(name)

    def resolve(self, name: str) -> MemberRef:
        if name == MEMBER_NAME_RESERVED:
            return self._lead_ref()
        validate_member_name(name)
        with self._lock():
            member = self._find_member_unlocked(name)
            if member is not None:
                members = self._iter_members_unlocked()
                self._sync_registry_unlocked(members)
                return self._ref_from_member(member)
        raise TeamMemberError(f"成员不存在: {name!r}")

    def resolve_active(self, name: str) -> MemberRef:
        ref = self.resolve(name)
        if ref.status in _TERMINAL_STATES:
            raise TeamMemberError(f"成员已终止: {name!r}")
        if ref.status in _UNREACHABLE_STATES:
            raise TeamMemberError(
                f"成员当前不可达（{ref.status.value}）: {name!r}，"
                "请重新派生后再发送")
        return ref

    def is_assignable(self, name: str) -> bool:
        try:
            ref = self.resolve(name)
        except TeamMemberError:
            return False
        return ref.name != MEMBER_NAME_RESERVED and ref.status not in (
            _TERMINAL_STATES | _UNREACHABLE_STATES)

    # —— 写入 ——
    def team_meta(self) -> TeamMeta:
        with self._lock():
            return self._read_team_meta_unlocked()

    def add_member(self, member: TeamMember) -> TeamMember:
        validate_member_name(member.name)
        with self._lock():
            meta = self._read_team_meta_unlocked()
            if member.name in meta.members:
                raise TeamMemberError(f"成员已存在: {member.name!r}")
            if self._read_member_unlocked(member.name) is not None:
                raise TeamMemberError(
                    f"成员元数据已存在但不在花名册: {member.name!r}")
            self._storage.atomic_write_json(
                self._storage.member_meta_path(self._team, member.name),
                _member_to_dict(member),
            )
            updated = replace(
                meta,
                members=(*meta.members, member.name),
                next_member_seq=meta.next_member_seq + 1,
            )
            self._write_team_meta_unlocked(updated)
            self._write_registry_unlocked()
            return member

    def remove_member(self, name: str) -> None:
        """从花名册移除成员并删除元数据（派生失败回滚用）。"""
        validate_member_name(name)
        with self._lock():
            meta = self._read_team_meta_unlocked()
            if name not in meta.members:
                return
            updated = replace(
                meta, members=tuple(m for m in meta.members if m != name))
            self._write_team_meta_unlocked(updated)
            try:
                self._storage.member_meta_path(self._team, name).unlink()
            except FileNotFoundError:
                pass
            self._write_registry_unlocked()

    def update(self, name: str, **fields: Any) -> TeamMember:
        allowed = {
            "role", "worktree_path", "branch", "backend",
            "requires_approval", "status", "note",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise TeamMemberError(
                f"不支持的成员字段: {', '.join(sorted(unknown))}")
        with self._lock():
            member = self._read_member_unlocked(name)
            if member is None:
                raise TeamMemberError(f"成员不存在或元数据损坏: {name!r}")
            changes: dict[str, Any] = {
                "updated_at": self._clock(),
            }
            if "role" in fields:
                changes["role"] = str(fields["role"])
            if "worktree_path" in fields:
                changes["worktree_path"] = Path(fields["worktree_path"])
            if "branch" in fields:
                changes["branch"] = str(fields["branch"])
            if "backend" in fields:
                raw = fields["backend"]
                changes["backend"] = (
                    raw if isinstance(raw, TeamMemberBackend)
                    else TeamMemberBackend(str(raw)))
            if "requires_approval" in fields:
                changes["requires_approval"] = bool(fields["requires_approval"])
            if "status" in fields:
                raw = fields["status"]
                changes["status"] = (
                    raw if isinstance(raw, TeamMemberStatus)
                    else TeamMemberStatus(str(raw)))
            if "note" in fields:
                changes["note"] = str(fields["note"] or "")
            updated = replace(member, **changes)
            self._storage.atomic_write_json(
                self._storage.member_meta_path(self._team, name),
                _member_to_dict(updated),
            )
            self._write_registry_unlocked()
            return updated

    def mark_all_offline(self) -> list[MemberRef]:
        """进程重启后：非 terminated 成员统一 offline。"""
        with self._lock():
            members = self._iter_members_unlocked()
            changed: list[TeamMember] = []
            for member in members:
                if member.status is TeamMemberStatus.TERMINATED:
                    changed.append(member)
                    continue
                updated = replace(
                    member,
                    status=TeamMemberStatus.OFFLINE,
                    updated_at=self._clock(),
                )
                self._storage.atomic_write_json(
                    self._storage.member_meta_path(self._team, member.name),
                    _member_to_dict(updated),
                )
                changed.append(updated)
            self._write_registry_unlocked()
            return [self._ref_from_member(m) for m in changed]
