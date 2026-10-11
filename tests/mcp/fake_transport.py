"""内存假 Transport:供 client/adapter 单测,不碰真实子进程/网络。"""


class FakeMcpTransport:
    """按 method 回脚本化响应,记录调用顺序供断言。"""

    def __init__(self, call_result=None, fail_connect=False):
        self.calls: list[tuple] = []
        self.close_called = False
        self._call_result = call_result or {
            "content": [{"type": "text", "text": "ok"}], "isError": False}
        self._fail_connect = fail_connect

    async def connect(self):
        if self._fail_connect:
            raise ConnectionError("connect failed")

    async def request(self, method, params=None):
        self.calls.append((method, params))
        if method == "initialize":
            return {"protocolVersion": "2024-11-05", "capabilities": {},
                    "serverInfo": {"name": "fake", "version": "1.0"}}
        if method == "tools/list":
            return {"tools": [{"name": "sum", "description": "add",
                               "inputSchema": {"type": "object"}}]}
        if method == "tools/call":
            return self._call_result
        return {}

    async def notify(self, method, params=None):
        self.calls.append(("notify:" + method, params))

    async def close(self):
        self.close_called = True