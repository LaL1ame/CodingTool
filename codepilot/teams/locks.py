"""跨平台文件锁：O_EXCL 创建 + 过期接管 + token 校验释放。

不依赖 POSIX `flock`，Windows 下同样可用。锁文件内容记录 pid/token/time；
过期判定优先使用内容时间，损坏时回退 mtime。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from types import TracebackType
from typing import Callable

from codepilot.teams.model import TeamLockError

logger = logging.getLogger("codepilot.teams.locks")


class FileLock:
    """非重入文件锁。"""

    def __init__(
        self,
        path: str | Path,
        *,
        timeout: float = 5.0,
        stale_after: float = 30.0,
        retry: float = 0.05,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._path = Path(path)
        self._timeout = timeout
        self._stale_after = stale_after
        self._retry = retry
        self._clock = clock
        self._sleep = sleep
        self._token: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _read_time(self) -> float | None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict):
            ts = raw.get("time")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                return float(ts)
        try:
            return self._path.stat().st_mtime
        except OSError:
            return None

    def _is_stale(self) -> bool:
        ts = self._read_time()
        if ts is None:
            return False
        return (self._clock() - ts) > self._stale_after

    def acquire(self) -> "FileLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        token = f"{os.getpid()}-{uuid.uuid4().hex}"
        deadline = self._clock() + self._timeout
        while True:
            try:
                fd = os.open(
                    str(self._path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if self._is_stale():
                    logger.warning("接管过期锁 %s", self._path)
                    try:
                        self._path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if self._clock() >= deadline:
                    raise TeamLockError(
                        f"获取锁超时: {self._path}（持有者未释放或未过期）")
                self._sleep(self._retry)
                continue
            except OSError as e:
                raise TeamLockError(f"创建锁失败 {self._path}: {e}") from e
            try:
                payload = {
                    "pid": os.getpid(),
                    "token": token,
                    "time": self._clock(),
                }
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
            except OSError as e:
                try:
                    self._path.unlink()
                except OSError:
                    pass
                raise TeamLockError(f"写锁失败 {self._path}: {e}") from e
            self._token = token
            return self

    def release(self) -> None:
        token = self._token
        self._token = None
        if token is None:
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict) and raw.get("token") == token:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
