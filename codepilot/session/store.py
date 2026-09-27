"""会话持久化 —— 对齐 Claude Code 的 resume。

会话 JSONL 存**用户级** `~/.codepilot/projects/<sha1(cwd)>/*.jsonl`(重克隆/换目录不丢);
项目内 `.codepilot/` 只放轻量索引/设置(不入 git)。

JSONL 行:
  {"type":"meta","provider":..., "model":..., "cwd":...}
  {"type":"message","role":"user|assistant","content":..., "timestamp":..., "duration":...}
  {"type":"mode","snapshot":{tool_axis,release,accept_edits}}
工具调用行按需追加;resume 主要恢复 message 上下文 + mode 快照。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

HOME = Path.home() / ".codepilot"
INDEX_DIR_NAME = ".codepilot"


def session_dir(cwd: str | Path) -> Path:
    """按 cwd 编码出用户级会话目录,稳定且唯一定位。"""
    key = hashlib.sha1(str(cwd).encode("utf-8")).hexdigest()
    return HOME / "projects" / key


def project_index_dir(cwd: str | Path) -> Path:
    """项目内轻量目录(.codepilot/),放索引与设置。"""
    return Path(cwd) / INDEX_DIR_NAME


def new_session_path(cwd: str | Path, ts: float | None = None) -> Path:
    """新会话文件:目录/UTC-<timestamp>.jsonl。文件一行 JSON 自描述。"""
    ts = ts if ts is not None else time.time()
    path = session_dir(cwd) / f"{ts:.0f}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_line(path: Path, obj: dict) -> None:
    path = Path(path)  # 兼容 str / Path,避免调用方类型不一致
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def append_meta(path: Path, *, provider: str, model: str, cwd: str) -> None:
    _write_line(path, {"type": "meta", "provider": provider, "model": model,
                       "cwd": cwd, "ts": time.time()})


def append_message(path: Path, *, role: str, content: str,
                   duration: float | None = None) -> None:
    _write_line(path, {"type": "message", "role": role, "content": content,
                       "timestamp": time.time(), "duration": duration})


def append_mode(path: Path, snapshot: dict) -> None:
    _write_line(path, {"type": "mode", "snapshot": snapshot, "ts": time.time()})


def list_sessions(cwd: str | Path) -> list[Path]:
    """返回该 cwd 的会话文件,按修改时间新→旧。"""
    d = session_dir(cwd)
    if not d.exists():
        return []
    return sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


def load_session(path: Path) -> dict:
    """读取会话:返回 {messages:[{role,content,duration}], mode: dict|None, meta: dict|None}。"""
    messages = []
    mode = None
    meta = None
    if not path.exists():
        return {"messages": messages, "mode": mode, "meta": meta}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = obj.get("type")
        if t == "message":
            messages.append({"role": obj.get("role"), "content": obj.get("content", ""),
                             "duration": obj.get("duration")})
        elif t == "mode":
            mode = obj.get("snapshot")
        elif t == "meta":
            meta = obj
    return {"messages": messages, "mode": mode, "meta": meta}