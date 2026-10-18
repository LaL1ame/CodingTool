"""任务板：CRUD、依赖门控、并发领取、状态机与删除保护。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from codepilot.teams.model import TaskStatus, TeamTaskError
from codepilot.teams.storage import TeamStorage
from codepilot.teams.tasks import TaskBoard, dependency_gaps, deps_satisfied


@pytest.fixture
def board(storage: TeamStorage, team: str) -> TaskBoard:
    members = {"alice", "bob"}
    return TaskBoard(storage, team, is_member=members.__contains__)


def _start_and_done(board: TaskBoard, task_id: int) -> None:
    board.update(task_id, status=TaskStatus.IN_PROGRESS)
    board.update(task_id, status=TaskStatus.DONE, result="ok")


def test_create_and_list(board: TaskBoard):
    t1 = board.create(title="A", description="first")
    t2 = board.create(title="B", depends_on=[t1.id])
    assert t1.status is TaskStatus.PENDING
    assert board.get(t2.id).depends_on == (t1.id,)
    assert [t.id for t in board.list()] == [t1.id, t2.id]
    assert [t.id for t in board.list(status="pending")] == [t1.id, t2.id]


def test_create_validation(board: TaskBoard):
    with pytest.raises(TeamTaskError):
        board.create(title="  ")
    with pytest.raises(TeamTaskError):
        board.create(title="A", depends_on=[99])
    with pytest.raises(TeamTaskError):
        board.create(title="A", assignee="lead")
    with pytest.raises(TeamTaskError):
        board.create(title="A", assignee="ghost")


def test_claim_dependency_gate(board: TaskBoard):
    a = board.create(title="A")
    b = board.create(title="B", depends_on=[a.id])
    with pytest.raises(TeamTaskError, match="依赖未完成"):
        board.claim(b.id, "alice")
    _start_and_done(board, a.id)
    claimed = board.claim(b.id, "alice")
    assert claimed.assignee == "alice"


def test_claim_conflicts(board: TaskBoard):
    t = board.create(title="A")
    board.claim(t.id, "alice")
    with pytest.raises(TeamTaskError, match="已被"):
        board.claim(t.id, "bob")
    with pytest.raises(TeamTaskError):
        board.claim(999, "bob")


def test_concurrent_claim_only_one_wins(board: TaskBoard):
    task = board.create(title="race")
    barrier = Barrier(2)

    def claim(member: str):
        barrier.wait()
        try:
            board.claim(task.id, member)
            return "ok"
        except TeamTaskError:
            return "err"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["alice", "bob"]))
    assert sorted(results) == ["err", "ok"]
    assert board.get(task.id).assignee in {"alice", "bob"}


def test_update_and_transitions(board: TaskBoard):
    t = board.create(title="A")
    with pytest.raises(TeamTaskError):
        board.update(t.id, status=TaskStatus.DONE)
    board.update(t.id, status=TaskStatus.IN_PROGRESS)
    board.update(t.id, status=TaskStatus.DONE, result="done")
    with pytest.raises(TeamTaskError, match="仅允许 Lead 重开"):
        board.update(t.id, status=TaskStatus.PENDING)
    reopened = board.update(
        t.id, status=TaskStatus.PENDING, allow_reopen=True)
    assert reopened.status is TaskStatus.PENDING


def test_update_validation(board: TaskBoard):
    t = board.create(title="A")
    with pytest.raises(TeamTaskError):
        board.update(t.id, title="")
    with pytest.raises(TeamTaskError):
        board.update(t.id, unknown=1)
    with pytest.raises(TeamTaskError):
        board.update(t.id, assignee="ghost")
    with pytest.raises(TeamTaskError):
        board.update(t.id, depends_on=[t.id])


def test_delete_protection(board: TaskBoard):
    a = board.create(title="A")
    b = board.create(title="B", depends_on=[a.id])
    with pytest.raises(TeamTaskError, match="仍被依赖"):
        board.delete(a.id)
    board.update(b.id, status=TaskStatus.IN_PROGRESS)
    with pytest.raises(TeamTaskError, match="执行中"):
        board.delete(b.id)
    board.update(b.id, status=TaskStatus.CANCELLED)
    board.delete(b.id)
    board.delete(a.id)
    assert board.list() == []


def test_dependency_helpers():
    from codepilot.teams.model import Task

    a = Task(1, "A", "", TaskStatus.DONE, None, (), 0, 0)
    b = Task(2, "B", "", TaskStatus.PENDING, None, (1, 3), 0, 0)
    tasks = {1: a, 2: b}
    assert dependency_gaps(b, tasks) == (3,)
    ok, gaps = deps_satisfied(a, tasks)
    assert ok and gaps == ()


def test_corrupt_tasks_file_is_backed_up(
    storage: TeamStorage, team: str,
):
    storage.atomic_write_text(storage.tasks_json(team), "{broken")
    board = TaskBoard(storage, team, is_member={"alice"}.__contains__)
    t = board.create(title="A")
    assert t.id == 1
    assert list(storage.team_dir(team).glob("tasks.json.corrupt-*"))
