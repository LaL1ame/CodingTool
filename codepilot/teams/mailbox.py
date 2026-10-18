"""邮箱文件：消息校验、摘要、追加与消费。

每个成员（含 Lead）一个 JSONL 文件，配套 `<name>.lock`。追加是单行写入，
已读状态在邮箱锁内原子重写；坏行跳过，不影响其余消息。
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from codepilot.teams.locks import FileLock
from codepilot.teams.model import (
    TeamMemberStatus,
    TeamMessage,
    TeamMessageError,
    TeamMessageType,
    TeamSettings,
)
from codepilot.teams.naming import (
    MEMBER_NAME_RESERVED,
    validate_member_name,
)
from codepilot.teams.storage import TeamStorage

logger = logging.getLogger("codepilot.teams.mailbox")


def _message_to_dict(message: TeamMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "sender": message.sender,
        "recipient": message.recipient,
        "type": message.type.value,
        "body": message.body,
        "summary": message.summary,
        "timestamp": message.timestamp,
        "read": message.read,
        "task_id": message.task_id,
        "approved": message.approved,
        "reason": message.reason,
        "status": message.status,
    }


def _message_from_dict(raw: object) -> TeamMessage | None:
    if not isinstance(raw, dict):
        return None
    try:
        return TeamMessage(
            id=str(raw["id"]),
            sender=str(raw["sender"]),
            recipient=str(raw["recipient"]),
            type=TeamMessageType(str(raw["type"])),
            body=str(raw.get("body", "")),
            summary=str(raw.get("summary", "")),
            timestamp=float(raw["timestamp"]),
            read=bool(raw.get("read", False)),
            task_id=raw.get("task_id"),
            approved=raw.get("approved"),
            reason=str(raw.get("reason", "")),
            status=str(raw.get("status", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _summary(body: str, limit: int) -> str:
    for line in (body or "").splitlines():
        text = " ".join(line.split())
        if text:
            return text[:limit]
    return "(无正文)"


class Mailbox:
    def __init__(
        self,
        path: str | Path,
        *,
        settings: TeamSettings,
        lock: FileLock | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path)
        self._settings = settings
        self._clock = clock
        self._lock = lock or FileLock(
            self._path.with_suffix(".lock"),
            timeout=settings.lock_timeout_seconds,
            stale_after=settings.lock_stale_seconds,
            retry=settings.lock_retry_seconds,
            clock=clock,
        )

    @property
    def path(self) -> Path:
        return self._path

    # —— 消息构造与校验 ——
    def make(
        self,
        *,
        sender: str,
        recipient: str,
        type: str | TeamMessageType,
        body: str = "",
        task_id: int | None = None,
        approved: bool | None = None,
        reason: str = "",
        status: str = "",
        summary: str | None = None,
    ) -> TeamMessage:
        try:
            message_type = (
                type if isinstance(type, TeamMessageType)
                else TeamMessageType(str(type))
            )
        except ValueError as e:
            raise TeamMessageError(f"未知消息类型: {type!r}") from e
        if not isinstance(sender, str) or not sender:
            raise TeamMessageError("sender 不能为空")
        if sender != MEMBER_NAME_RESERVED:
            try:
                validate_member_name(sender)
            except Exception as e:
                raise TeamMessageError(str(e)) from e
        if not isinstance(recipient, str) or not recipient:
            raise TeamMessageError("recipient 不能为空")
        if recipient not in (MEMBER_NAME_RESERVED, "*"):
            try:
                validate_member_name(recipient)
            except Exception as e:
                raise TeamMessageError(str(e)) from e
        body = str(body or "")
        if message_type in (TeamMessageType.TEXT, TeamMessageType.BROADCAST):
            if not body.strip():
                raise TeamMessageError(f"{message_type.value} 消息正文不能为空")
        elif message_type is TeamMessageType.PLAN_REQUEST:
            if not body.strip():
                raise TeamMessageError("plan_request 计划正文不能为空")
        elif message_type is TeamMessageType.PLAN_DECISION:
            if not isinstance(approved, bool):
                raise TeamMessageError("plan_decision 必须提供 approved 布尔值")
        elif message_type is TeamMessageType.STATUS:
            if status not in {s.value for s in TeamMemberStatus}:
                raise TeamMessageError(f"status 值非法: {status!r}")
        if message_type in (
            TeamMessageType.PLAN_REQUEST, TeamMessageType.PLAN_DECISION,
        ):
            if isinstance(task_id, bool) or not isinstance(task_id, int):
                raise TeamMessageError(
                    f"{message_type.value} 必须提供整数 task_id")
        return TeamMessage(
            id=uuid.uuid4().hex,
            sender=sender,
            recipient=recipient,
            type=message_type,
            body=body,
            summary=(summary if summary is not None
                     else _summary(body or reason or status,
                                   self._settings.message_summary_chars)),
            timestamp=self._clock(),
            read=False,
            task_id=task_id,
            approved=approved,
            reason=str(reason or ""),
            status=str(status or ""),
        )

    def _validate_message(self, message: TeamMessage) -> None:
        if not isinstance(message.type, TeamMessageType):
            raise TeamMessageError(f"未知消息类型: {message.type!r}")
        if not message.id or not message.sender or not message.recipient:
            raise TeamMessageError("消息缺少 id/sender/recipient")
        if message.type in (TeamMessageType.TEXT, TeamMessageType.BROADCAST):
            if not message.body.strip():
                raise TeamMessageError("消息正文不能为空")
        elif message.type is TeamMessageType.PLAN_REQUEST:
            if message.task_id is None or not message.body.strip():
                raise TeamMessageError("plan_request 缺少 task_id/计划正文")
        elif message.type is TeamMessageType.PLAN_DECISION:
            if message.task_id is None or not isinstance(
                    message.approved, bool):
                raise TeamMessageError("plan_decision 缺少 task_id/approved")
        elif message.type is TeamMessageType.STATUS:
            if message.status not in {s.value for s in TeamMemberStatus}:
                raise TeamMessageError(f"status 值非法: {message.status!r}")

    # —— 读写 ——
    def append(self, message: TeamMessage) -> TeamMessage:
        self._validate_message(message)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            TeamStorage.append_jsonl_line(
                self._path, _message_to_dict(message))
        return message

    def _read_unlocked(self) -> list[TeamMessage]:
        rows, bad = TeamStorage.read_jsonl(self._path)
        out: list[TeamMessage] = []
        for row in rows:
            msg = _message_from_dict(row)
            if msg is None:
                bad += 1
                continue
            out.append(msg)
        if bad:
            logger.warning("邮箱跳过坏消息 %d 条: %s", bad, self._path)
        return out

    def peek(self) -> list[TeamMessage]:
        with self._lock:
            return self._read_unlocked()

    def _consume(self, predicate: Callable[[TeamMessage], bool],
                 limit: int) -> list[TeamMessage]:
        if limit <= 0:
            raise TeamMessageError("limit 必须大于 0")
        with self._lock:
            messages = self._read_unlocked()
            picked: list[TeamMessage] = []
            rewritten: list[TeamMessage] = []
            for msg in messages:
                if not msg.read and len(picked) < limit and predicate(msg):
                    picked.append(replace(msg, read=True))
                    rewritten.append(replace(msg, read=True))
                else:
                    rewritten.append(msg)
            if picked:
                TeamStorage.rewrite_jsonl(
                    self._path, [_message_to_dict(m) for m in rewritten])
            return picked

    def consume_unread(self, *, limit: int) -> list[TeamMessage]:
        return self._consume(lambda _m: True, limit)

    def consume_matching(
        self,
        *,
        type: str | TeamMessageType,
        task_id: int | None,
        limit: int = 1,
    ) -> list[TeamMessage]:
        try:
            wanted = (
                type if isinstance(type, TeamMessageType)
                else TeamMessageType(str(type))
            )
        except ValueError as e:
            raise TeamMessageError(f"未知消息类型: {type!r}") from e
        return self._consume(
            lambda m: m.type is wanted and m.task_id == task_id, limit)
