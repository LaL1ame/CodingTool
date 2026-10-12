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
import secrets
import time
from datetime import datetime
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
    """新会话文件:目录/YYYYMMDD-HHMMSS-xxxx.jsonl(state12)。

    YYYYMMDD-HHMMSS 取本地时间,xxxx 为 4 位十六进制随机后缀(secrets)防同秒撞车。
    可读可排序:文件名即时间序,list_sessions 按名降序即是最新在前。
    ts 仅为测试/确定性注入:None 用当前时间。
    """
    then = datetime.fromtimestamp(ts) if ts is not None else datetime.now()
    stamp = then.strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)  # 4 hex 字符
    path = session_dir(cwd) / f"{stamp}-{suffix}.jsonl"
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


def append_assistant_toolcalls(path: Path, *, content: str,
                               tool_calls: list) -> None:
    """新增行 type=assistant:带工具调用的一轮助手消息(state12)。

    用于跨重启还原完整 LLM 序列(与 tool_result 行配对做 dangling 校验)。
    """
    _write_line(path, {"type": "assistant", "content": content,
                       "tool_calls": tool_calls, "ts": time.time()})


def append_tool_result(path: Path, *, call_id: str | None, name: str | None,
                       output: str | None, error: str | None = None) -> None:
    """新增行 type=tool_result:一条工具调用的执行结果(state12)。"""
    _write_line(path, {"type": "tool_result", "call_id": call_id, "name": name,
                       "output": output, "error": error, "ts": time.time()})


def append_mode(path: Path, snapshot: dict) -> None:
    _write_line(path, {"type": "mode", "snapshot": snapshot, "ts": time.time()})


def list_sessions(cwd: str | Path) -> list[Path]:
    """返回该 cwd 的会话文件,按文件名新→旧(state12:文件名即时间序)。"""
    d = session_dir(cwd)
    if not d.exists():
        return []
    return sorted(d.glob("*.jsonl"), key=lambda p: p.name, reverse=True)


def cleanup_expired(session_dir_root: str | Path, *, max_age_days: int = 30,
                    now: float | None = None) -> list[Path]:
    """删除该目录下早于 max_age_days 的过旧会话文件(state12)。

    只删过期、幂等;返回被删除的文件列表。now 仅供测试确定性注入。
    """
    root = Path(session_dir_root)
    now = now if now is not None else time.time()
    cutoff = now - max_age_days * 86400
    removed: list[Path] = []
    if not root.exists():
        return removed
    for p in root.glob("*.jsonl"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed.append(p)
        except OSError:
            continue
    return removed


def load_session(path: Path) -> dict:
    """读取会话(state12):返回
    {messages(文本消息,含 assistant 叙事行), mode, meta, segments(原始行序段落)}。

    旧 JSONL(仅 meta/message/mode)仍可读:无 tool 行 → segments 为空兼向后兼容。
    """
    messages = []
    mode = None
    meta = None
    segments = []  # state12: 顺序化 assistant/tool_result 段落,供 restore 重建完整序列
    if not path.exists():
        return {"messages": messages, "mode": mode, "meta": meta, "segments": segments}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = obj.get("type")
        if t == "message":
            messages.append({"role": obj.get("role"), "content": obj.get("content", ""),
                             "duration": obj.get("duration")})
            # 文本行也进 segments,保持全局行序(restore 需在工具轮间穿插对话文本)。
            # _last_activity 用 ts 取最近活动:文本行记录的是 timestamp,一并带入。
            segments.append({"type": "message", "role": obj.get("role"),
                             "content": obj.get("content", ""),
                             "ts": obj.get("timestamp")})
        elif t == "assistant":
            segments.append({"type": "assistant", "content": obj.get("content", ""),
                             "tool_calls": obj.get("tool_calls", []),
                             "ts": obj.get("ts")})
            messages.append({"role": "assistant", "content": obj.get("content", ""),
                             "duration": None})
        elif t == "tool_result":
            segments.append({"type": "tool_result", "call_id": obj.get("call_id"),
                             "name": obj.get("name"), "output": obj.get("output"),
                             "error": obj.get("error"), "ts": obj.get("ts")})
        elif t == "mode":
            mode = obj.get("snapshot")
        elif t == "meta":
            meta = obj
    return {"messages": messages, "mode": mode, "meta": meta, "segments": segments}


def summarize(path: str | Path) -> dict:
    """扫 JSONL 现算元信息(state12),不维护 meta 文件。

    返回 {id, title, message_count, last_ts}:
      title 取首条 user 消息前 40 字;message_count 计 message+assistant 行数;
      last_ts 取最后 activity ts。只读,无副作用。
    """
    path = Path(path)
    title = ""
    count = 0
    last_ts = 0.0
    if not path.exists():
        return {"id": path.name, "title": "", "message_count": 0, "last_ts": 0.0}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = obj.get("type")
        if t == "message":
            count += 1
            if not title and obj.get("role") == "user":
                title = str(obj.get("content", ""))[:40].strip()
            if obj.get("timestamp"):
                last_ts = max(last_ts, float(obj["timestamp"]))
        elif t == "assistant":
            count += 1
            if obj.get("ts"):
                last_ts = max(last_ts, float(obj["ts"]))
        elif t == "tool_result":
            if obj.get("ts"):
                last_ts = max(last_ts, float(obj["ts"]))
    return {"id": path.name, "title": title, "message_count": count, "last_ts": last_ts}