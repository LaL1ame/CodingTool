"""团队持久化布局与原子 IO。

目录布局::

    <root>/
    ├── current.json
    ├── locks/current.lock
    └── <team>/
        ├── team.json
        ├── registry.json
        ├── tasks.json
        ├── locks/team.lock
        ├── members/<name>.json
        └── mailboxes/<name>.jsonl + <name>.lock

所有 JSON 写入使用「同目录临时文件 + os.replace」；JSONL 重写同样原子。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

from codepilot.teams.locks import FileLock
from codepilot.teams.model import TeamError, TeamSettings
from codepilot.teams.naming import (
    MEMBER_NAME_RESERVED,
    validate_member_name,
    validate_team_name,
)

logger = logging.getLogger("codepilot.teams.storage")


class TeamStorage:
    def __init__(self, root: str | Path, settings: TeamSettings) -> None:
        self._root = Path(root).expanduser().resolve()
        self._settings = settings

    @property
    def root(self) -> Path:
        return self._root

    @property
    def settings(self) -> TeamSettings:
        return self._settings

    # —— 路径 ——
    @property
    def current_path(self) -> Path:
        return self._root / "current.json"

    @property
    def current_lock_path(self) -> Path:
        return self._root / "locks" / "current.lock"

    def team_dir(self, team: str) -> Path:
        validate_team_name(team)
        path = (self._root / team).resolve()
        if not path.is_relative_to(self._root):
            raise TeamError(f"小组路径越界: {team!r}")
        return path

    def team_json(self, team: str) -> Path:
        return self.team_dir(team) / "team.json"

    def registry_json(self, team: str) -> Path:
        return self.team_dir(team) / "registry.json"

    def tasks_json(self, team: str) -> Path:
        return self.team_dir(team) / "tasks.json"

    def team_lock_path(self, team: str) -> Path:
        return self.team_dir(team) / "locks" / "team.lock"

    def members_dir(self, team: str) -> Path:
        return self.team_dir(team) / "members"

    def member_meta_path(self, team: str, member: str) -> Path:
        validate_member_name(member)
        return (self.members_dir(team) / f"{member}.json").resolve()

    def mailboxes_dir(self, team: str) -> Path:
        return self.team_dir(team) / "mailboxes"

    def mailbox_path(self, team: str, owner: str) -> Path:
        if owner != MEMBER_NAME_RESERVED:
            validate_member_name(owner)
        return (self.mailboxes_dir(team) / f"{owner}.jsonl").resolve()

    def mailbox_lock_path(self, team: str, owner: str) -> Path:
        return self.mailbox_path(team, owner).with_suffix(".lock")

    def ensure_team_layout(self, team: str) -> Path:
        path = self.team_dir(team)
        for sub in (path, path / "locks", path / "members", path / "mailboxes"):
            sub.mkdir(parents=True, exist_ok=True)
        return path

    # —— 原子 JSON ——
    @staticmethod
    def atomic_write_json(path: str | Path, data: object) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def atomic_write_text(path: str | Path, text: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def read_json(self, path: str | Path, default: object = None) -> object:
        target = Path(path)
        if not target.exists():
            return default
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise TeamError(f"读取 JSON 失败 {target}: {e}") from e

    def backup_corrupt(self, path: str | Path) -> Path:
        """把损坏文件改名备份；返回备份路径。"""
        target = Path(path)
        stamp = int(time.time())
        backup = target.with_name(f"{target.name}.corrupt-{stamp}")
        n = 1
        while backup.exists():
            backup = target.with_name(f"{target.name}.corrupt-{stamp}-{n}")
            n += 1
        try:
            os.replace(target, backup)
        except OSError as e:
            logger.warning("备份损坏文件失败 %s: %s", target, e)
        logger.warning("损坏文件已备份: %s -> %s", target, backup)
        return backup

    def read_json_safe(
        self, path: str | Path, default: object, *, backup: bool = True,
    ) -> object:
        try:
            return self.read_json(path, default)
        except TeamError:
            if backup:
                self.backup_corrupt(path)
            return default

    # —— JSONL ——
    @staticmethod
    def read_jsonl(path: str | Path) -> tuple[list[dict], int]:
        target = Path(path)
        if not target.exists():
            return [], 0
        rows: list[dict] = []
        bad = 0
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            logger.warning("读取 JSONL 失败 %s: %s", target, e)
            return [], 1
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if not isinstance(row, dict):
                bad += 1
                continue
            rows.append(row)
        if bad:
            logger.warning("JSONL 跳过坏行 %d 条: %s", bad, target)
        return rows, bad

    @staticmethod
    def rewrite_jsonl(path: str | Path, rows: list[dict]) -> None:
        text = "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        TeamStorage.atomic_write_text(path, text)

    @staticmethod
    def append_jsonl_line(path: str | Path, row: dict) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False)
        with target.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    # —— 当前组指针 ——
    def read_current(self) -> str | None:
        try:
            raw = self.read_json(self.current_path, None)
        except TeamError:
            logger.warning("当前组指针损坏: %s", self.current_path)
            return None
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            return None
        try:
            return validate_team_name(name)
        except Exception:
            logger.warning("当前组指针含非法组名: %r", name)
            return None

    def write_current(self, name: str) -> None:
        validate_team_name(name)
        lock = FileLock(
            self.current_lock_path,
            timeout=self._settings.lock_timeout_seconds,
            stale_after=self._settings.lock_stale_seconds,
            retry=self._settings.lock_retry_seconds,
        )
        with lock:
            self.atomic_write_json(self.current_path, {"name": name})
