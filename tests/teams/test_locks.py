"""FileLock：获取、重试、超时、过期接管、token 释放。"""

import json
import time
from pathlib import Path

import pytest

from codepilot.teams.locks import FileLock
from codepilot.teams.model import TeamLockError


def _lock(path: Path, **kw) -> FileLock:
    kw.setdefault("timeout", 0.3)
    kw.setdefault("stale_after", 10.0)
    kw.setdefault("retry", 0.01)
    return FileLock(path, **kw)


def test_acquire_release(tmp_path: Path):
    path = tmp_path / "x.lock"
    with _lock(path):
        assert path.exists()
    assert not path.exists()


def test_second_lock_times_out(tmp_path: Path):
    path = tmp_path / "x.lock"
    with _lock(path):
        with pytest.raises(TeamLockError):
            _lock(path, timeout=0.05).acquire()


def test_stale_lock_is_taken_over(tmp_path: Path):
    path = tmp_path / "x.lock"
    path.write_text(json.dumps({
        "pid": 999, "token": "old", "time": time.time() - 100,
    }), encoding="utf-8")
    with _lock(path, stale_after=1.0):
        assert path.exists()
    assert not path.exists()


def test_release_token_mismatch_keeps_file(tmp_path: Path):
    path = tmp_path / "x.lock"
    lock = _lock(path)
    lock.acquire()
    path.write_text(json.dumps({"token": "other", "time": time.time()}),
                    encoding="utf-8")
    lock.release()
    assert path.exists()


def test_context_manager_releases_on_error(tmp_path: Path):
    path = tmp_path / "x.lock"
    with pytest.raises(RuntimeError):
        with _lock(path):
            raise RuntimeError("boom")
    assert not path.exists()


def test_timeout_clock_injection(tmp_path: Path):
    path = tmp_path / "x.lock"
    with _lock(path):
        now = [0.0]

        def clock():
            now[0] += 0.1
            return now[0]

        with pytest.raises(TeamLockError):
            FileLock(
                path, timeout=0.25, stale_after=10.0, retry=0.0,
                clock=clock, sleep=lambda _s: None,
            ).acquire()
