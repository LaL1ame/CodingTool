# CodePilot 工具系统 Plan

## 架构概览

在现有平层架构（8 文件）之上新增 `Agent` 层负责工具编排，`tools/` 子包负责工具实现。
ChatManager 收缩为纯历史管理，Agent 接管"一轮对话的完整生命周期"——消息构建、工具发送、工具执行、结果回灌、第二轮请求。

```
┌─────────────────────────────────────────────┐
│  main.py         入口：配置 → App            │
├─────────────────────────────────────────────┤
│  app.py          TUI：对话区 + 工具行 + 弹窗  │
│                   ↓↑ Delta 流               │
│  chat.py         ChatManager：历史管理       │
│                   ↓↑ 消息 + 工具调用          │
│  agent.py  [新]  Agent：工具编排             │
│                   ↓↑ stream() + tools       │
│  providers.py    BaseProvider → 协议适配     │
│  protocols.py    SSE 解析（text/think/tool）  │
├─────────────────────────────────────────────┤
│  tools/   [新]   工具子包                    │
│    base.py       Tool ABC + validate_path    │
│    registry.py   ToolRegistry + 格式转换     │
│    read/write/edit/glob_tool/grep_tool/run   │
├─────────────────────────────────────────────┤
│  config.py       ProviderConfig              │
└─────────────────────────────────────────────┘
```

## 核心数据结构

### `ToolCall`（定义于 protocols.py）

```python
@dataclass
class ToolCall:
    id: str            # 模型生成的唯一 ID（Anthropic: tool_use.id, OpenAI: tool_call.id）
    name: str          # 工具名（如 "read", "write"）
    arguments: dict    # 已解析的 JSON 参数对象 {"file_path": "app.py", "offset": 0}
```

### `ToolResult`（定义于 protocols.py）

```python
@dataclass
class ToolResult:
    call_id: str              # 对应 ToolCall.id
    name: str                 # 工具名
    output: str | None = None # 成功输出（可能已截断）
    error: str | None = None  # 失败时的错误信息
    truncated: bool = False   # 输出是否被截断
    total_items: int | None = None  # 截断前的原始总量（行数/条数/字符数）
```

### `Delta`（扩展，protocols.py）

```python
@dataclass
class Delta:
    text: str | None = None
    thinking: str | None = None
    tool_calls: list[ToolCall] | None = None   # 新增：一批完整的工具调用
    tool_result: ToolResult | None = None       # 新增：单个工具执行结果
    done: bool = False
    error: str | None = None
```

工具调用流式 Delta 序列：

```
Delta(text="我来读一下...")     ← 模型正文
Delta(tool_calls=[              ← 工具调用到达（全部），TUI 创建工具行
    ToolCall(id="1", name="read", ...),
    ToolCall(id="2", name="glob", ...),
])
Delta(tool_result=ToolResult(call_id="1", output="..."))  ← 逐个完成更新
Delta(tool_result=ToolResult(call_id="2", output="..."))
Delta(text="根据文件内容...")    ← 第二轮模型回复
Delta(done=True)
```

### `Tool`（tools/base.py）

```python
class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...
    @property
    @abstractmethod
    def description(self) -> str: ...
    @property
    @abstractmethod
    def parameters(self) -> dict: ...  # JSON Schema 对象
    async def execute(self, **kwargs) -> ToolResult: ...
```

### `ToolRegistry`（tools/registry.py）

```python
class ToolRegistry:
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool | None: ...
    def list_all(self) -> list[Tool]: ...
    def to_anthropic_tools(self) -> list[dict]: ...
        # → [{"name": "read", "description": "...", "input_schema": {...}}, ...]
    def to_openai_tools(self) -> list[dict]: ...
        # → [{"type": "function", "function": {"name": "read", "description": "...", "parameters": {...}}}, ...]
```

### `Agent`（agent.py）

```python
class Agent:
    def __init__(self, provider: BaseProvider, registry: ToolRegistry,
                 workspace: Path, timeout: float = 120.0,
                 confirm_callback: Callable | None = None): ...
    async def run(self, messages: list[dict]) -> AsyncIterator[Delta]: ...
```

## 模块设计

### protocols.py [修改]

- 新增 `ToolCall`, `ToolResult` 数据类
- `Delta` 新增 `tool_calls`, `tool_result` 字段
- `AnthropicProtocol.stream()` 新增 tool_use content_block 解析（识别 content_block_start/delta/stop 序列，拼接 name + JSON 参数碎片，stop 时产出完整 ToolCall）
- `OpenAIProtocol.stream()` 新增 tool_calls delta 解析（按 index 分桶，拼接 function.name + function.arguments，finish_reason=tool_calls 时产出 ToolCall 列表）
- 正确区分三类增量：文本（text_delta）→ 正常输出；思考（thinking_delta）→ 接收即丢弃；工具调用（tool_use / tool_calls delta）→ 流式拼接

**依赖：** httpx（不变），无新增依赖

### providers.py [修改]

- `BaseProvider.stream()` 签名扩展：`stream(messages, tools: list[dict] | None = None)`
- `AnthropicProvider.stream()` 将 tools 注入 `body["tools"]`
- `OpenAIProvider.stream()` 将 tools 注入 `body["tools"]`

**依赖：** protocols, config（不变）

### tools/ [新建子包]

| 文件 | 职责 |
|------|------|
| `__init__.py` | 聚合导出 Tool, ToolRegistry, 六个工具类 |
| `base.py` | Tool ABC + `validate_path(relative_path, workspace_root) → Path` |
| `registry.py` | ToolRegistry + Anthropic/OpenAI 格式转换方法 |
| `read.py` | ReadTool — cat -n 格式，2000 行上限，30s 超时 |
| `write.py` | WriteTool — 覆盖 + 自动建父目录，10s 超时 |
| `edit.py` | EditTool — 原文唯一匹配替换，三结果（唯一/零处/多处），10s 超时 |
| `glob_tool.py` | GlobTool — Path.rglob + mtime 排序，500 上限，跳过 .git，30s 超时 |
| `grep_tool.py` | GrepTool — 正则搜索 + -i 大小写控制，250 上限，跳过 .git，30s 超时 |
| `run.py` | RunTool — asyncio.create_subprocess_shell + confirm_callback，120s 超时，8000 字符上限 |

**依赖：** codepilot.protocols (ToolResult)

### agent.py [新建]

Agent 负责单轮对话的工具编排：

1. 构建 tools 列表（按当前协议格式，从 registry 获取）
2. 第一轮 `provider.stream(messages, tools)` → 流式产出的 text/thinking delta 直接透传
3. 检测到 `Delta(tool_calls=[...])`：
   a. 停止第一轮消费
   b. 从 registry 查找工具 → `asyncio.gather` 并行执行，每个包 `asyncio.wait_for` 超时控制
   c. 异常捕获 → 转为 `ToolResult(error=...)`
   d. 逐个 yield `Delta(tool_result=...)`（TUI 据此更新工具行）
   e. 构建 tool_result 消息注入 messages 副本：
      - Anthropic: `{"role": "user", "content": [{"type": "tool_result", "tool_use_id": ..., "content": ...}]}`
      - OpenAI: `{"role": "tool", "tool_call_id": ..., "content": ...}`
   f. 第二轮 `provider.stream(messages + tool_results, tools)` → 流式产出 text delta 到 done
4. 若无 tool_calls → 直接产出 text delta 到 done（与 MVP 纯文本路径一致）

**依赖：** codepilot.providers, codepilot.tools, codepilot.protocols

### chat.py [修改]

- `ChatManager.__init__` 新增参数 `agent: Agent`（替代 `provider: BaseProvider`）
- `send_message()` 内部：调 `self.agent.run(self.build_context())` 代替原来的 `self.provider.stream(ctx)`
- text delta → 累积写入 history（不变）
- tool_result delta → 透传，不写入 history（工具结果由 Agent 负责注入第二轮请求的消息）
- 自身专注于历史管理和 context 构建

**依赖：** codepilot.agent, codepilot.protocols

### app.py [修改]

- `on_mount()` 中创建 ToolRegistry + 注册六个工具 + 创建 Agent + 传入 ChatManager
- `on_input_submitted()` 中处理新的 Delta 类型：
  - `delta.tool_calls` → 为每个 ToolCall 创建工具行 Static widget（`● Read(app.py)`），挂载到 chat 区
  - `delta.tool_result` → 更新对应工具行文本（成功：`● Read(42 lines)`；失败：`✕ Error(...)`）
  - `delta.text` → 现有流式逻辑不变
- 新增 `_confirm_command(command: str) -> bool` 方法：
  - 在当前屏幕挂载列表选择组件（Claude Code 风格），显示命令文本 + 三选项（1.批准 / 2.拒绝 / 3.始终允许）
  - ↑↓ 切换高亮，Enter 确认，数字键 1/2/3 直达
  - 返回 bool；选"始终允许"后，后续 `run` 调用自动跳过确认
- 创建 RunTool 时传入 `self._confirm_command` 作为 `confirm_callback`

**依赖：** 新增 codepilot.tools, codepilot.agent

### main.py [修改]

- 创建 workspace 路径（`Path.cwd()`），组装 Registry → Agent → ChatManager → App 依赖链
- 其余启动逻辑（配置加载、provider 选择）不变

**依赖：** 新增 codepilot.tools, codepilot.agent

## 模块交互

### 纯文本对话（与 MVP 一致，满足 N9）

```
App → ChatManager.send_message() → Agent.run(messages)
  → Provider.stream(messages, tools) → Protocol.stream()
  → yield Delta(text=...) → 透传回 App → 流式渲染
```

Delta 序列与 MVP 完全一致：text → text → ... → done。

### 工具调用对话

```
App → ChatManager.send_message() → Agent.run(messages)
  → Provider.stream(messages, tools)                    [第一轮]
  → Protocol 产出 Delta(text=...), Delta(tool_calls=[...])
  → Agent 检测到 tool_calls:
    → asyncio.gather(并行执行所有工具)
    → yield Delta(tool_result=...) × N                  [TUI 更新工具行]
    → 注入 tool_result 到 messages（按协议格式）
  → Provider.stream(messages, tools)                    [第二轮]
  → Protocol 产出 Delta(text=...) 
  → 透传回 App → 流式渲染 → Delta(done)
```

### 命令确认分支

```
Agent → registry.get("run").execute(command, confirm_callback=...)
  → RunTool 调 confirm_callback(command)
  → App 挂载列表选择组件 → 用户 ↑↓ 选 + Enter 确认
    1. 批准 → RunTool 执行命令
    2. 拒绝 → ToolResult(error="用户拒绝执行")
    3. 始终允许 → 执行 + 本会话后续 run 自动跳过确认
```

### 错误处理流

```
工具执行异常 / 超时 / 参数无效
  → asyncio.wait_for 超时: ToolResult(error="超时 (30s)")
  → execute() 内部异常: ToolResult(error=str(e))
  → Agent yield Delta(tool_result=ToolResult(error=...))
  → TUI 更新工具行 → ✕ Error: ...
  → Agent 将 error ToolResult 注入 history (Anthropic: is_error=True)
  → 第二轮请求 → 模型看到错误 → 调整策略 → 生成回复
```

成功和失败都是 ToolResult，都注入 history，都触发第二轮请求。错误不是异常，是数据。

## 文件组织

```
codepilot/
├── __init__.py              (不变)
├── __main__.py              (不变)
├── config.py                [修改] + ToolConfig（timeout 覆盖值）
├── protocols.py             [修改] + ToolCall, ToolResult, Delta 扩展, 双协议工具解析
├── providers.py             [修改] stream() 签名加 tools 参数
├── agent.py                 [新建] Agent 类 — 工具编排
├── chat.py                  [修改] ChatManager 委托 Agent
├── app.py                   [修改] 工具行渲染 + 命令确认列表选择
├── main.py                  [修改] 组装 Agent + Registry + 注入回调
│
└── tools/                   [新建子包]
    ├── __init__.py          聚合导出
    ├── base.py              Tool ABC + validate_path() 公共函数
    ├── registry.py          ToolRegistry + format converters
    ├── read.py              ReadTool
    ├── write.py             WriteTool
    ├── edit.py              EditTool
    ├── glob_tool.py         GlobTool
    ├── grep_tool.py         GrepTool
    └── run.py               RunTool

tests/                        [新建测试目录]
├── __init__.py
├── test_protocols.py        [修改] + 工具调用解析用例
├── test_providers.py        [修改] + tools 参数传递用例
├── test_agent.py            [新建] Agent 编排测试
├── test_tools_base.py       [新建] Tool 接口 + validate_path 测试
├── test_tools_registry.py   [新建] 注册 + 格式转换测试
├── test_tools_read.py       [新建]
├── test_tools_write.py      [新建]
├── test_tools_edit.py       [新建]
├── test_tools_glob.py       [新建]
├── test_tools_grep.py       [新建]
└── test_tools_run.py        [新建]
```

| 类型 | 数量 |
|------|------|
| 新建 | agent.py + tools/ (9) = 10 |
| 修改 | 6 |
| 不变 | 2 |
| 测试 | 新建 8 + 修改 2 = 10 |

## 技术决策

| # | 决策点 | 选择 | 理由 |
|---|--------|------|------|
| 1 | 工具编排在哪 | 独立 Agent 类 | ChatManager 管历史、Agent 管执行流程。下一章 Agent Loop 只需在 Agent 内部加 while 循环 |
| 2 | 工具注册模式 | Tool ABC + ToolRegistry | 行业标准做法。添加新工具 = 实现接口 + 注册一行 |
| 3 | 工具调用内部表示 | 统一 ToolCall(id, name, arguments) | 协议层做差异适配、上层只用一种类型 |
| 4 | Delta 承载工具事件 | tool_calls 和 tool_result 加到现有 Delta | 已有流已跑通，新增字段比新建事件类型更简单 |
| 5 | 并行工具执行 | asyncio.gather + asyncio.wait_for | Python 原生方案，每个工具独立超时 + 异常隔离 |
| 6 | 路径安全 | 集中 validate_path() 在 tools/base.py | 六个工具有五个需要路径校验，集中后一处修处处生效 |
| 7 | 命令确认 | confirm_callback 回调注入 + Claude Code 风格列表选择 | RunTool 不依赖 TUI，通过回调接收确认结果。TUI 端用 ↑↓ 列表选择（避免 ModalScreen Enter 泄漏），工具保持纯逻辑 |
| 8 | 结果体量控制 | 各工具内部截断 | 每个工具最清楚自己的数据结构和合理的截断点 |
| 9 | 工具结果注入历史 | Agent 层处理 | 注入格式依赖当前协议，Agent 持有 provider 引用 |
| 10 | 工具子包 vs 平铺 | tools/ 子包 | 9 个文件需命名空间隔离，且为未来扩展留空间 |
| 11 | tool_use 产出时机 | 全部收齐后一次性产出 | 保证 Agent 拿到完整列表，可以并行执行 |
| 12 | 第二轮请求中仍带 tools | 每轮请求都发送 tools 定义 | 保留模型在第二轮继续调用工具的可能性 |
| 13 | System Prompt 更新 | 扩展 SYSTEM_PROMPT 常量 | 不改配置、不引入外部模板 |
