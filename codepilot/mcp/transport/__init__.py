"""MCP 传输层:Transport ABC 与共享异常。

职责边界:只负责"把一条 JSON-RPC 消息发出去、收回来",不懂握手/工具语义。
stdio 与 http 各自实现 message 粒度的 request/notify/close。
"""