"""测试用假 MCP stdio server。

以子进程方式被 StdioTransport 拉起,读 stdin 的 Content-Length 帧、写 stdout 帧。
支持 initialize / tools/list / tools/call(sum);通知(无 id)不回应。
运行方式: `python fake_mcp_server.py`
"""

import json
import sys


def read_frame():
    """读一帧;stream 关闭返回 None。"""
    content_length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        text = line.decode(errors="ignore").rstrip("\r\n")
        if text == "":
            break
        if text.lower().startswith("content-length:"):
            content_length = int(text.split(":", 1)[1].strip())
    if content_length is None:
        return None
    return json.loads(sys.stdin.buffer.read(content_length))


def write_frame(obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()


def handle(msg):
    req_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "fake", "version": "1.0"}}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": [
            {"name": "sum", "description": "a + b",
             "inputSchema": {"type": "object",
                             "properties": {"a": {"type": "number"},
                                            "b": {"type": "number"}},
                             "required": ["a", "b"]}},
        ]}}

    if method == "tools/call":
        args = params.get("arguments") or {}
        total = int(args.get("a", 0)) + int(args.get("b", 0))
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": str(total)}], "isError": False}}

    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"unknown method {method}"}}


if __name__ == "__main__":
    while True:
        msg = read_frame()
        if msg is None:
            break
        if msg.get("id") is None:  # 通知,不回应
            continue
        write_frame(handle(msg))