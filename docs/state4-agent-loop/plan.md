# CodePilot Agent Loop Plan

## 架构概览

在现有平层架构上，把 `agent.py` 从「单轮编排」升级为「循环编排器」，其余层通过**事件字段扩展**和**工具安全属性**协作。核心变化：

- **Agent（循环编排器）**：持有循环状态（轮次、取消标志、计划模式、连续未知工具计数），在 `run()` 内驱动多轮 ReAct 循环。
- **协议层（protocols.py）**：`Delta` 扩展 `usage`（Token 用量）、`round/max_rounds`（进度）、`cancelled`（取消）事件；两种协议在流结束时产出 `usage`。
- **工具层（tools/）**：`Tool` 基类新增 `side_effect` 属性；注册中心提供只读工具过滤。
- **对话层（chat.py）**：系统提示词文本改由 Agent 构造（计划模式需动态切换），`ChatManager` 通过 `agent.system_prompt()` 组装 system 消息并维护用户/助手历史。
- **TUI 层（app.py）**：消费新事件、新增 Esc 取消绑定、解析 `/plan` `/do` 命令。

分层职责不变，**无新增模块**、**无循环依赖**（依赖方向仍是 app → chat → agent → tools/protocols）。

## 核心数据结构

### Delta（扩展，protocols.py）

现有字段保留：`text / thinking / tool_calls / tool_result / done / error`。
新增字段：

```python
usage: Usage | None = None      # Token 用量事件
round: int | None = None        # 当前轮次（进度事件）
max_rounds: int | None = None   # 迭代上限（进度事件）
cancelled: bool = False         # 取消信号
```

### Usage（新增，protocols.py）

```python
@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
```

### Tool（扩展，tools/base.py）

新增属性（`write/edit/run` 覆盖为 `True`，`read/glob/grep` 用默认 `False`）：

```python
@property
def side_effect(self) -> bool:
    """是否有副作用（写文件/执行命令）。True → 串行执行。默认 False。"""
    return False
```

### Agent（重写，agent.py）

```python
class Agent:
    def __init__(self, provider, registry, workspace, *,
                 max_rounds=10, max_unknown=2, timeout=120.0,
                 confirm_callback=None) -> None
    def set_plan_mode(self, on: bool) -> None     # /plan /do 切换
    def cancel(self) -> None                      # Esc 取消
    async def run(self, messages) -> AsyncIterator[Delta]   # 循环入口
    def system_prompt(self) -> str                # 模式感知的提示词（供 ChatManager 组装）
    def _build_tools(self) -> list[dict] | None   # 计划模式下过滤只读
    async def _execute_batched(self, tool_calls) -> list[ToolResult]  # 安全性分批
    def _append_assistant(self, messages, text, tool_calls) -> None   # 回灌助手消息
    def _append_tool_results(self, messages, results) -> None         # 回灌工具结果
```

### AgentConfig（新增，config.py）

```python
@dataclass
class AgentConfig:
    max_rounds: int = 10
    max_unknown: int = 2
```

从 `config.yaml` 可选 `agent:` 段读取，缺省用默认值。

## 模块设计

### 模块 A：Agent（agent.py）— 循环编排器

**职责：** 驱动 ReAct 循环：构建请求上下文 → 逐轮流式收集（双路）→ 判定停止 → 执行工具（分批）→ 回灌 → 下一轮。持有计划模式、取消标志、连续未知工具计数。

**对外接口：** `run(messages)`（异步生成器，产出 `Delta`）、`set_plan_mode(on)`、`cancel()`。

**依赖：** `protocols`（Delta/Usage/ToolCall/ToolResult）、`providers.BaseProvider`、`tools.ToolRegistry`。

### 模块 B：协议层（protocols.py）— 事件与流式解析

**职责：** 定义 `Delta/Usage`；`AnthropicProtocol` / `OpenAIProtocol` 在流式解析基础上，流结束时产出 `usage` 事件（OpenAI 需在请求体加 `stream_options.include_usage`）。

**对外接口：** `stream(messages, tools)`（异步生成器，产出 `Delta`）。

**依赖：** 无（最底层）。

### 模块 C：工具层（tools/）— 工具接口与注册

**职责：** `Tool` 新增 `side_effect` 属性；`ToolRegistry` 新增 `list_read_only()` 返回只读工具子集（供计划模式过滤）。

**对外接口：** `Tool.side_effect`、`ToolRegistry.list_read_only()`。

**依赖：** 无（最底层）。

### 模块 D：对话层（chat.py）— 历史管理

**职责：** 维护用户/助手历史（`Message` 列表），`build_context()` 调用 `agent.system_prompt()` 组装 system 消息 + 历史；`send_message()` 透传事件、累积最终文本、结束时回写助手消息。

**对外接口：** `send_message(content)`（异步生成器）、`history`。

**依赖：** `agent.Agent`。

### 模块 E：TUI 层（app.py）— 事件消费与交互

**职责：** 消费 `Delta` 事件流渲染（文本、工具行、用量、进度、取消）；Esc 绑定取消；解析 `/plan` `/do` 命令切换模式。

**对外接口：** `CodePilotApp`、`confirm_command`（复用）。

**依赖：** `agent.Agent`、`chat.ChatManager`。

### 模块 F：入口与配置（main.py / config.py）

**职责：** 加载 `AgentConfig`（迭代上限、未知工具阈值），装配 Agent，注入确认回调。

**依赖：** 各层。

## 模块交互

```
用户输入 (app.py)
  ├─ /plan 或 /do ──→ agent.set_plan_mode() ──→ 更新状态栏，不调模型
  └─ 普通消息 ──→ chat.send_message(text)
                     ├─ history.append(user)
                     ├─ ctx = build_context()          # 纯历史，无 system
                     └─ agent.run(ctx)   # ctx 已含 system（由 chat 用 agent.system_prompt() 组装）
                              循环 for round in 1..max_rounds:
                                ① yield Delta(round, max_rounds)          ─┐
                                ② stream provider（双路：yield text 且累积）  ├─→ app.py 渲染
                                ③ yield Delta(usage)                      ─┘
                                ④ 判定：error → 停；cancelled → 停；
                                   无工具调用 → 回灌助手文本 → yield done → 停
                                ⑤ 有工具调用：
                                     yield Delta(tool_calls)              ─→ 建 ToolRow
                                     _append_assistant(回灌助手 tool_use)
                                     _execute_batched（只读并发/副作用串行）
                                     yield Delta(tool_result) ×N          ─→ 更新 ToolRow
                                     连续未知工具 ≥ 阈值 → yield error → 停
                                     _append_tool_results(回灌结果)
                                     cancelled → 停
                              达上限 → yield error("达到迭代上限") → 停
```

**取消链路（Esc）：** `app` Esc 绑定 → `agent.cancel()`（置 `_cancel_event` + 取消进行中的流式/工具任务）→ 循环在轮次边界与工具执行后检测到 → `yield Delta(cancelled=True)` → `app._process_chat` 打印「已取消」并恢复输入。

## 文件组织

```
codepilot/
├── protocols.py      # 改：Delta 扩展 + Usage + 两种协议产出 usage
├── providers.py      # 不变
├── agent.py          # 重写：ReAct 循环 + cancel + set_plan_mode + 分批执行 + 提示词
├── chat.py           # 改：移除 SYSTEM_PROMPT，build_context 不含 system，透传新事件
├── config.py         # 改：新增 AgentConfig + agent: 段解析
├── main.py           # 改：装配 AgentConfig 到 Agent
├── app.py            # 改：Esc 绑定、/plan /do 解析、新事件渲染
└── tools/
    ├── base.py       # 改：Tool 新增 side_effect 属性
    ├── registry.py   # 改：新增 list_read_only()
    ├── write.py      # 改：side_effect=True
    ├── edit.py       # 改：side_effect=True
    └── run.py        # 改：side_effect=True

tests/
├── test_agent.py     # 改/增：循环、停止条件、分批、计划模式、取消
├── test_protocols.py # 增：usage 事件、cancelled
├── test_chat.py      # 增：新事件透传、取消不写历史
├── test_config.py    # 增：agent 段解析
├── test_tools_*.py   # 增：side_effect 属性断言
└── conftest.py       # 增：mock provider 支持多轮流式脚本
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 循环放哪 | 改造 `agent.py`，不新增模块 | 循环本质是 Agent 编排职责的扩展，单一职责不变 |
| 事件模型 | 扩展 `Delta`，不引入独立事件类 | 与现有消费方（chat/app）兼容，最小改动、无回归 |
| 双路收集器 | 内联于循环（yield 同时 append），不建 `Collector` 类 | 逻辑极简（一个 list），独立类是过度设计（YAGNI） |
| 系统提示词 | 提示词**文本**由 `Agent.system_prompt()` 构造（模式感知），`ChatManager` 负责组装 context | 计划模式需动态改提示词；构造归 Agent（内聚），组装仍归 chat（避免 run() 签名语义变化与测试扰动） |
| 工具安全属性 | `Tool.side_effect` 布尔属性（默认 False） | 声明式、可扩展；新工具无需改 Agent 代码即自动归类 |
| 分批执行顺序 | 按原始顺序划分：副作用工具为「栅栏」，栅栏间的只读工具并发，副作用逐个串行 | 兼顾正确性（保留模型调用顺序语义）与并发（只读批内并发） |
| 取消机制 | `asyncio.Event` 标志 + 取消进行中任务，循环在安全点检测 | 协作式取消，干净退出、恢复终端；比强杀 worker 更可控 |
| 未知工具停止 | 连续 N 轮（默认 2）出现未知工具调用即停 | 单次误判不误杀，连续多次说明模型与工具集失配 |
| 迭代上限配置 | 新增 `AgentConfig`，从 `config.yaml` 可选 `agent:` 段读取 | 满足 AC15「可配置」，与 `ProviderConfig` 模式一致 |
| OpenAI 用量 | 请求体加 `stream_options.include_usage`，末块取 `usage` | OpenAI 流式默认不返回 usage，需显式开启 |
| `/plan` `/do` 归属 | app.py 拦截，不发给模型 | 与 `/exit` 同属元命令；chat/agent 层无需感知命令文本 |

## 风险与权衡

- **取消中断流式**：协作式取消依赖在 `cancel()` 中同时取消流式任务与工具任务；若模型流无法立即中断（底层 httpx），最坏延迟到当前 chunk 结束，可接受。
- **分批顺序语义**：模型同轮「先写后读同一文件」时，按栅栏规则读会先于写执行、读到旧内容。这是罕见场景，采用简单确定性规则，必要时后续章节再引入显式依赖排序。
- **多轮上下文不回写历史**：跨用户消息的 tool_use/tool_result 仍不入 `history`（沿用现状），循环仅在单条消息内保持上下文。会话级工具上下文持久化留待「会话持久化」章节。
