# MewCode Plan

## 架构概览

整体采用分层架构，自上而下五层：

```
┌──────────────────────────────────────────┐
│               main.py（入口）              │
│         加载配置 → 选择 provider → 启动 TUI │
└──────────────────────────────────────────┘
                    │
┌──────────────────────────────────────────┐
│           TUI 层（mewcode/tui/）           │
│  Textual App — 界面布局、输入处理、        │
│  Markdown 渲染、流式更新、响应计时         │
└──────────────────────────────────────────┘
                    │ 依赖
┌──────────────────────────────────────────┐
│        对话管理层（mewcode/chat/）          │
│  ChatManager — 消息历史维护、               │
│  system prompt 管理、上下文组装             │
└──────────────────────────────────────────┘
                    │ 依赖
┌──────────────────────────────────────────┐
│       Provider 抽象层（mewcode/providers/） │
│  统一接口 + 工厂方法                        │
│  对接上层：屏蔽协议差异，暴露统一流式接口     │
│  对接下层：调用具体 Protocol 发送请求        │
└──────────────────────────────────────────┘
                    │ 依赖
┌──────────────────────────────────────────┐
│       协议层（mewcode/protocols/）          │
│  定义协议无关的请求/响应模型                 │
│  ├─ AnthropicProtocol（Messages API,       │
│  │    SSE 解析, thinking 过滤）             │
│  └─ OpenAIProtocol（Chat Completions API,  │
│       SSE 解析）                            │
│  负责：构造协议请求体、解析 SSE 事件流、      │
│        统一输出 Delta 增量                   │
└──────────────────────────────────────────┘
                    │ 使用
┌──────────────────────────────────────────┐
│         配置层（mewcode/config.py）         │
│  YAML 读取、模型校验、ProviderConfig 对象    │
└──────────────────────────────────────────┘
```

### 各层职责

**配置层**：负责 `config.yaml` 的读取、校验和解析。将 YAML 数据转换为 `ProviderConfig`
数据对象列表。校验失败时打印可读错误并退出。

**协议层**：封装各 LLM 协议的 HTTP 请求构造、SSE 事件流解析和增量数据统一。
每种协议（Anthropic Messages API / OpenAI Chat Completions API）各自实现，对外输出
统一的 `Delta` 对象（`text` / `thinking` / `done` / `error`）。
Anthropic 协议在此层完成 extended thinking 增量的**识别**（标记为 thinking 类型 Delta），
**丢弃动作**由对话管理层（ChatManager）执行。
这是唯一直接接触 HTTP 和 API 格式的层。

**Provider 抽象层**：定义统一的 `BaseProvider` 抽象类，暴露异步流式聊天接口。
Provider 不关心 HTTP 细节——它接收上层消息列表，委托协议层发送请求、解析响应，
再将 Delta 流逐级向上传递。

**对话管理层**：`ChatManager` 维护当前会话的消息列表，持有内置 system prompt，
提供 `add_user_message` / `add_assistant_message` 方法，负责在每次请求前
组装完整上下文（system + history + 新消息）传递给 Provider。

**TUI 层**：基于 Textual 构建完整终端界面。包含以下子组件：
- `MewCodeApp`：顶层 App，管理全局状态和事件
- `BannerWidget`：ASCII 猫咪 + 应用信息 + 工作目录
- `ChatArea`：对话区，滚动展示消息，支持 Markdown 渲染
- `InputBox`：底部输入框，支持多行编辑、流式锁定
- `StatusBar`：底部状态栏（provider 名 | 模型名 | 计时器）

**入口（main.py）**：编排启动流程：加载配置 → 多 provider 选择（如需要）→
实例化 ChatManager 和 Provider → 启动 Textual App。

---

## 核心数据结构

### ProviderConfig（配置层）

```python
@dataclass
class ProviderConfig:
    name: str           # 可读名称
    protocol: str       # "anthropic" | "openai"
    model: str          # 模型 ID
    api_key: str        # 认证密钥
    base_url: str       # API 端点地址
    thinking: bool      # 是否启用扩展思考
```

### Delta（协议层 → 上行通用载体）

```python
@dataclass
class Delta:
    text: str | None = None       # 正文增量文本
    thinking: str | None = None   # 思考增量文本（接收即丢弃）
    done: bool = False            # 本轮回复结束
    error: str | None = None      # 错误信息
```

### Message（对话管理）

```python
@dataclass
class Message:
    role: str           # "system" | "user" | "assistant"
    content: str        # 消息正文
    timestamp: float    # 创建时间戳
    duration: float | None  # 回复耗时（仅 assistant）
```

### ChatManager（对话管理层）

```python
class ChatManager:
    SYSTEM_PROMPT: str         # 内置系统提示词（常量）
    history: list[Message]     # 对话历史
    provider: BaseProvider     # 当前活动 provider

    async def send_message(content) -> AsyncIterator[Delta]
    def add_user_message(content) -> Message
    def add_assistant_message(content, duration) -> Message
    def build_context() -> list[dict]
```

### BaseProvider（Provider 抽象层）

```python
class BaseProvider(ABC):
    config: ProviderConfig

    @abstractmethod
    async def stream(messages) -> AsyncIterator[Delta]

    @staticmethod
    def create(config) -> "BaseProvider"   # 工厂方法
```

### BaseProtocol（协议层）

```python
class BaseProtocol(ABC):
    base_url: str
    api_key: str
    model: str

    @abstractmethod
    async def stream(messages, thinking=False) -> AsyncIterator[Delta]
```

---

## 数据流：一轮对话

用户输入 "用 Python 写一个快速排序" → Enter → 到 Markdown 渲染完成：

1. TUI 层：InputBox 触发 onSubmit → 锁定输入 → 追加用户消息到 ChatArea → 启动计时器 → 调用 ChatManager.send_message()
2. 对话管理层：追加用户消息到 history → 组装上下文（system + 全部历史）→ 调用 provider.stream()
3. Provider 层：委托 protocol.stream(messages, thinking=config.thinking)
4. 协议层：构造 HTTP POST（Anthropic 或 OpenAI 格式）→ 发送 → 逐行解析 SSE → yield Delta
5. Provider 层：透传 Delta 向上
6. 对话管理层：收集 text 增量 → 过滤 thinking → 透传 delta 给 TUI → done 后追加 assistant 消息到 history
7. TUI 层：text delta → 逐字追加到 ChatArea → done 后 Markdown 重新渲染 → 停止计时器 → 定格显示耗时 → 解锁输入

### Delta 统一模型

所有协议输出归一化为 `Delta(text|thinking|done|error)`。上层只关心这四种情况，
无需知道底层是 Anthropic 还是 OpenAI。

---

## 文件组织

```
mewcode/
├── __init__.py
├── main.py                    # 入口
├── config.py                  # 配置层
│
├── protocols/                 # 协议层
│   ├── __init__.py
│   ├── base.py                # BaseProtocol + Delta
│   ├── anthropic.py           # AnthropicProtocol
│   └── openai.py              # OpenAIProtocol
│
├── providers/                 # Provider 抽象层
│   ├── __init__.py
│   ├── base.py                # BaseProvider + 工厂方法
│   ├── anthropic.py           # AnthropicProvider
│   └── openai.py              # OpenAIProvider
│
├── chat/                      # 对话管理层
│   ├── __init__.py
│   ├── models.py              # Message
│   └── manager.py             # ChatManager
│
└── tui/                       # TUI 层
    ├── __init__.py
    ├── app.py                 # MewCodeApp
    └── widgets/
        ├── __init__.py
        ├── banner.py          # 横幅
        ├── chat_area.py       # 对话区
        ├── input_box.py       # 输入框
        ├── provider_select.py # Provider 选择屏幕
        └── status_bar.py      # 状态栏

config.yaml                    # 用户配置（不纳入版本控制）
config.example.yaml            # 示例配置
pyproject.toml                 # 项目元数据 + 依赖
```

---

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| TUI 框架 | Textual | 原生 async、Markdown 渲染（Rich 同族）、组件化布局 |
| HTTP 客户端 | httpx | async 原生支持、SSE 流式读取 |
| YAML 解析 | PyYAML | 成熟稳定 |
| Markdown 渲染 | Rich（Textual 内置） | 与 Textual 深度集成 |
| SDK 使用方式 | 不依赖官方 SDK，协议层直接调 HTTP | 协议层职责就是原始 HTTP/SSE 解析 |
| Python 版本 | >= 3.10 | `str \| None` 联合类型语法 |
| 异步模型 | asyncio | Python 原生异步 |
| 包管理 | pyproject.toml + pip | Python 生态标准 |
