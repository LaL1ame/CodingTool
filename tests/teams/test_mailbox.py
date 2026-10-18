"""邮箱：五类消息、摘要、已读消费、锁失败与坏行隔离。"""

from pathlib import Path

import pytest

from codepilot.teams.locks import FileLock
from codepilot.teams.mailbox import Mailbox
from codepilot.teams.model import (
    TeamLockError,
    TeamMessageError,
    TeamMessageType,
    TeamSettings,
)
from codepilot.teams.storage import TeamStorage


@pytest.fixture
def box(settings: TeamSettings, tmp_path: Path) -> Mailbox:
    return Mailbox(tmp_path / "alice.jsonl", settings=settings,
                   clock=lambda: 100.0)


def test_text_message_defaults(box: Mailbox):
    msg = box.make(sender="lead", recipient="alice", type="text", body=" hi ")
    assert msg.timestamp == 100.0
    assert msg.read is False
    assert msg.summary == "hi"
    assert msg.id
    box.append(msg)
    assert box.peek()[0].body == " hi "


def test_five_message_types(box: Mailbox):
    messages = [
        box.make(sender="lead", recipient="alice", type="text", body="hi"),
        box.make(sender="lead", recipient="*", type="broadcast", body="all"),
        box.make(sender="alice", recipient="lead", type="plan_request",
                 body="plan", task_id=1),
        box.make(sender="lead", recipient="alice", type="plan_decision",
                 task_id=1, approved=True, reason="ok"),
        box.make(sender="alice", recipient="lead", type="status",
                 status="idle", task_id=1),
    ]
    for m in messages:
        box.append(m)
    got = box.peek()
    assert [m.type for m in got] == [
        TeamMessageType.TEXT, TeamMessageType.BROADCAST,
        TeamMessageType.PLAN_REQUEST, TeamMessageType.PLAN_DECISION,
        TeamMessageType.STATUS,
    ]
    assert got[3].approved is True
    assert got[4].status == "idle"


def test_summary_truncates(settings: TeamSettings, tmp_path: Path):
    short = TeamSettings(**{
        **settings.__dict__, "message_summary_chars": 5,
    })
    box = Mailbox(tmp_path / "x.jsonl", settings=short)
    msg = box.make(
        sender="lead", recipient="alice", type="text",
        body="first   line with spaces\nsecond")
    assert msg.summary == "first"


@pytest.mark.parametrize("kwargs", [
    dict(sender="lead", recipient="alice", type="text", body=""),
    dict(sender="lead", recipient="alice", type="magic", body="x"),
    dict(sender="lead", recipient="alice", type="status", status="weird"),
    dict(sender="lead", recipient="alice", type="plan_request", body="x"),
    dict(sender="lead", recipient="alice", type="plan_decision", task_id=1),
    dict(sender="lead", recipient="alice", type="plan_decision",
         task_id=1, approved="yes"),
    dict(sender="../x", recipient="alice", type="text", body="x"),
    dict(sender="lead", recipient="../x", type="text", body="x"),
])
def test_invalid_messages(box: Mailbox, kwargs):
    with pytest.raises(TeamMessageError):
        box.make(**kwargs)


def test_consume_unread_marks_read(box: Mailbox):
    m1 = box.append(box.make(
        sender="lead", recipient="alice", type="text", body="1"))
    m2 = box.append(box.make(
        sender="lead", recipient="alice", type="text", body="2"))
    first = box.consume_unread(limit=1)
    assert [m.id for m in first] == [m1.id]
    assert [m.read for m in box.peek()] == [True, False]
    rest = box.consume_unread(limit=10)
    assert [m.id for m in rest] == [m2.id]
    assert box.consume_unread(limit=10) == []


def test_consume_matching_keeps_other_unread(box: Mailbox):
    decision = box.append(box.make(
        sender="lead", recipient="alice", type="plan_decision",
        task_id=7, approved=True, reason="ok"))
    other = box.append(box.make(
        sender="lead", recipient="alice", type="text", body="hello"))
    got = box.consume_matching(type="plan_decision", task_id=7)
    assert [m.id for m in got] == [decision.id]
    remaining = box.peek()
    assert {m.id: m.read for m in remaining} == {
        decision.id: True, other.id: False}


def test_lock_failure_raises(settings: TeamSettings, tmp_path: Path):
    path = tmp_path / "locked.jsonl"
    holder = FileLock(
        path.with_suffix(".lock"), timeout=0.2, stale_after=30.0, retry=0.01)
    holder.acquire()
    box = Mailbox(
        path, settings=settings,
        lock=FileLock(
            path.with_suffix(".lock"), timeout=0.05,
            stale_after=30.0, retry=0.01))
    try:
        with pytest.raises(TeamLockError):
            box.append(box.make(
                sender="lead", recipient="alice", type="text", body="x"))
    finally:
        holder.release()


def test_bad_lines_are_skipped(box: Mailbox):
    good = box.make(
        sender="lead", recipient="alice", type="text", body="ok")
    box.append(good)
    TeamStorage.append_jsonl_line(box.path, {"bad": True})
    with box.path.open("a", encoding="utf-8") as f:
        f.write("not-json\n")
    got = box.peek()
    assert [m.id for m in got] == [good.id]
