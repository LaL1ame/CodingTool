# CodePilot MCP 客户端 Plan

> 状态:已批准(plan.md 阶段)｜基于已批准 spec.md

## 架构概览

新子包 `codepilot/mcp/`,与 `tools/`、`permissions/`、`session/` 平级。按职责拆 5 层:

```
┌─ 配置层 config.py ────────────────────────────────┐
│  解析 mcp: 段 + 两层合并 + ${VAR} 展开(N4)          │→ 产出 McpServerSpec 列表
└──────────────────────┬────────────────────────────┘
                       ▼
┌─ 传输层 mcp/transport/ ───────────────────────────┐
│  base.py  Transport ABC(JSON-RPC 消息粒度 request)  │
│  stdio.py 子进程 + Content-Length 帧 + id 配对       │
│  http.py  httpx Streamable HTTP + SSE/单 JSON +    │
│          optional Mcp-Session-Id 会话头            │
└──────────────────────┬────────────────────────────┘
                       ▼
┌─ 会话层 mcp/client.py ────────────────────────────┐
│  McpClient：三步握手 initialize→list_tools→call_tool│
│  (F4) + 生命周期                                   │
└──────────────────────┬────────────────────────────┘
                       ▼
┌─ 适配层 mcp/adapter.py ───────────────────────────┐
│  McpToolAdapter(Tool)：name/description/parameters/│
│  side_effect 映射 + 执行时 call_tool → ToolResult   │
└──────────────────────┬────────────────────────────┘
                       ▼
┌─ 编排层 mcp/manager.py ──────────────────────────┐
│  McpManager：多 server 连接/缓存/隔离(F7)+ 注册进  │
│  ToolRegistry(命名 server.tool,F5)                │
└──────────────────────┬────────────────────────────┘
                       ▼
                  main.py 接线：启动时 manager 连接 →
                  把 adapter 注册进 registry → 交给 Agent
```

**职责边界**:传输层只管收发消息;会话层只管三次握手与顺序;适配层只管单工具包装;编排层只管多 server 生命周期与注册;配置层只管从两份文件读合并列表。

**关键架构事实(F6):** 权限门控在 `Agent._execute_one` 对**所有工具统一执行**,MCP 工具一旦注册成普通 `Tool`,manual/auto/strict 与规则自动生效,adapter 不需要自实现权限。

## 核心数据结构

### 配置层(config.py 追加)

```python
@dataclass
class McpServerSpec:
    name: str
    type: Literal["stdio", "http"]
    read_only: bool = False          # 决策4：false→side_effect=True；true→只读
    # stdio
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # http
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

def load_mcp_config(user_path: Path | None, project_path: Path | None) -> list[McpServerSpec]:
    """并读两份 config.yaml 的 mcp: 段,map 合并(项目盖用户),${VAR} 展开(N4)。
       未定义 ${VAR} → 该 server 判 ConfigError,调用方放弃它并告警。"""

def expand_env(value: str, env: Mapping[str, str]) -> str:
    """把 ${VAR} 用 env 替换;未定义抛 KeyError/ValueError。"""
```

### 传输层 mcp/transport/(消息粒度)

```python
class Transport(ABC):
    async def connect(self) -> None: ...
    async def request(self, method: str, params: dict) -> dict:
        """发出带自增 id 的请求,回收按 id 配对的 result(或抛 RpcError)。"""
    async def notify(self, method: str, params: dict) -> None: ...   # fire-and-forget
    async def close(self) -> None: ...

class StdioTransport(Transport):
    def __init__(self, command, args, env): ...
    # 内部:asyncio.create_subprocess_exec + Content-Length 帧 + 读者任务 id→future 配对

class HttpTransport(Transport):
    def __init__(self, url, headers, *, timeout): ...
    # 内部:httpx.AsyncClient;请求 POST body;响应可能是"单 JSON"或 "SSE 流",
    #      也处理可选 Mcp-Session-Id 会话头
```

### 会话层 mcp/client.py

```python
class McpClient:
    def __init__(self, name: str, transport: Transport): ...
    async def start(self) -> dict:
        """initialize 握手 + notifications/initialized;返回 server 能力/协议版本。"""
    async def list_tools(self) -> list[tuple[str, str, dict]]:
        """tools/list → [(name, description, inputSchema)]"""
    async def call_tool(self, name: str, arguments: dict) -> MCPCallResult:
        """tools/call → 结构化结果"""
    async def close(self) -> None: ...

@dataclass
class MCPCallResult:
    output: str          # 展平后的纯文本
    error: str | None    # isError 时给错误
```

### 适配层 mcp/adapter.py

```python
class McpToolAdapter(Tool):          # implements tools/base.Tool
    def __init__(self, server: str, tool_name: str, desc: str,
                 input_schema: dict, client: McpClient, read_only: bool): ...
    name        -> f"{server}.{tool_name}"     # 决策3
    description -> desc
    parameters  -> input_schema                 # MCP inputSchema 即 JSON Schema
    side_effect -> not read_only                # 决策4
    async def execute(self, **kwargs) -> ToolResult:
        # client.call_tool(name, kwargs) → 映射 ToolResult(output=... / error=...)
```

### 编排层 mcp/manager.py

```python
class McpManager:
    def __init__(self, registry: ToolRegistry): ...
    async def connect_all(self, specs: list[McpServerSpec]) -> int:
        """并发连接各 server(各自 try/except 隔离,N1);发现的工具经 adapter
           注册进 registry;返回成功注册工具数;失败 server 打日志不影响其它。"""
    async def close_all(self) -> None: ...
```

### main.py 接线

```python
mcp_specs = load_mcp_config(user_cfg, project_cfg)
mgr = McpManager(registry)
await mgr.connect_all(mcp_specs)     # N2:工具已注册,Agent 消费时registry 就绪
agent = Agent(..., registry=registry)  # 用同一个 registry
# 退出: on_unmount → await mgr.close_all()
```

## 模块交互

### 一次启动的调用时序

```
main.main()
  │ find_config() 取 user/project 两个路径
  │ load_mcp_config(user, project) ──► [list[McpServerSpec]]
  ├─ McpManager(registry)
  │    └─ gather(各 server)         并发,各自 try/except 隔离(N1)
  │         ├─ Stdio/HttpTransport.connect()
  │         ├─ McpClient.start()    initialize + notifications/initialized
  │         ├─ McpClient.list_tools()
  │         └─ for 每工具: McpToolAdapter(...) → registry.register()  (F5)
  │    └─ 返回注册总数;失败 server 仅 log (N1)
  ▼
Agent(provider, registry, permission)      ← 同一个 registry,已含 MCP 工具
  │ 每轮 tool call:
  │   _execute_one: permission.check() → tool.execute()   [F6]
  │      └─ (MCP) adapter.execute(**kw) → client.call_tool
  │           → transport.request("tools/call") → 映射 ToolResult
  ▼
退出: app on_unmount → manager.close_all() → 关 transport
```

## 文件组织

```
codepilot/
├── config.py                  (M) 追加 McpServerSpec/load_mcp_config/expand_env
├── mcp/
│   ├── __init__.py              导出 McpManager, McpToolAdapter
│   ├── rpc.py                 JSON-RPC 2.0 信封构造/校验 辅助
│   ├── transport/
│   │   ├── __init__.py
│   │   ├── base.py            Transport ABC
│   │   ├── stdio.py           子进程 + Content-Length 帧 + id 配对
│   │   └── http.py            httpx + SSE/单 JSON + 会话头
│   ├── client.py              McpClient(三步握手)
│   ├── adapter.py             McpToolAdapter(Tool)
│   └── manager.py             McpManager(多 server 生命周期/注册)
├── main.py                    (M) 启动接线 + 退出关闭
└── tests/
    └── mcp/
        ├── test_config.py     两层合并/${VAR}/未定义告警(AC1,AC8)
        ├── test_rpc.py        id 配对/错误信封(AC3)
        ├── test_stdio.py      假的子进程脚本:帧解析/配对/超时(AC2,AC4)
        ├── test_http.py       httpx.MockTransport:单 JSON + SSE(AC2,AC3)
        ├── test_adapter.py    tool 名/参数/side_effect/execute→ToolResult(AC5,AC6)
        └── test_manager.py    一个坏 server 不影响其它(AC1,AC7)
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 依赖 | 手写 JSON-RPC + `asyncio.subprocess` + 已有 `httpx`,**不引 `mcp` SDK** | 选 A;协议全程可测可控,轻量 |
| stdio 帧 | `Content-Length` 头(非换行 JSON) | MCP 标准;LSP 式定长帧,免粘连 |
| http 响应 | 兼容"单 JSON"与"SSE 流"两种 | Streamable HTTP 服务端实现不一,Mock 两种都测 |
| http 会话 | 可选 `Mcp-Session-Id` 透传 | 服务端可能要求;满足则不重连 |
| 隔离 | `gather` 每 server 单任务 + try/except | N1;单点失败只 log,不崩启动 |
| 权限 | 注册即天然被 `Agent._execute_one` 门控 | F6 零成本;adapter 不重复造 |
| 命名 | `server.tool` 前缀 | 决策3;registry 唯一性稳 |
| 副作用 | 默认有(side_effect=True),`read_only: true` 可声明只读 | 决策4;plan 模式保守隐藏 |
| 超时 | 每次 `request` 设超时(N3);`Agent._execute_one` 本有 120s 兜底 | 双层兜底,防远端挂死 |