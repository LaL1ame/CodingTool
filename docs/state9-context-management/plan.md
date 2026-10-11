# CodePilot 上下文管理 + Token 优化 Plan

> 状态：plan.md 阶段｜基于已批准 spec.md
> 子包 `codepilot/context/`，与 `tools/`、`permissions/`、`session/`、`mcp/` 平级。

## 架构概览

新增子包 `codepilot/context/`，内部按职责拆 5 个模块 + 1 个配置段；复用现有 `agent`/`chat`/`protocol` 但不改其核心契约。

```
estimator.py（纯估算，N1）──► budget.py（状态机）──► offload.py（层1）/
   budget.py 同时驱动 compactor.py（层2）
                    ▲ 汇总协调
                  manager.py（ContextManager 编排）
                    │
        chat.py / agent.py / ui/app.py / config.py（接线，改动小）
```

**职责边界**：估算只管算数；budget 只管状态与触发判断（不碰内容）；offload/compactor 只管「怎么压」（执行动作，不碰预算判断）；manager 只做编排与数据流；UI/config/chat/agent 只做接线。各层通过明确接口通信，无环依赖。

### 关键架构事实（F6 同构）
权限门控同理：层1/层2 的触发判断由 `ContextManager.pre_request()` 单点汇聚在 `send_message` 内、`agent.run` 前，agent 循环核心不被渗透——agent 只管执行工具，压缩在编排层。

## 模块设计

### config.py 追加（N3）
```python
@dataclass
class ContextConfig:
    window: int = 200_000            # context.window
    auto_margin: int = 13_000        # 自动触发安全余量
    manual_margin: int = 3_000
    tool_threshold_n: int = 1000     # F1 单工具结果阈值（token）
    tool_batch_threshold_m: int = 4000  # F2 同消息合计阈值
    compact_keep_tokens: int = 10_000   # F3.2 尾部保留区（token）
    compact_keep_min_msgs: int = 5       # F3.2 尾部至少保留条数

def load_context_config(path) -> ContextConfig:
    """读可选 context: 段；缺省用默认值。"""
```

### context/estimator.py（N1，纯） 
```python
@dataclass(frozen=True)
class Estimate:
    tokens: int; source: str   # "usage" | "chars" | "words"

def estimate_text(text: str, kind: str = "code") -> int:
    """code→chars//4，prose→words×1.3。"""

class TokenEstimator:
    def __init__(self, last_input_tokens: int | None = None): ...
    def anchor(self, input_tokens: int) -> None:   # 每轮 message_stop 重锚
    def add(self, text: str, kind: str = "code") -> int:  # 增量估算，返回增量
    def current(self) -> int:                      # 锚点 + 增量
```

### context/budget.py（F3/F6）
```python
class ContextBudget:
    def __init__(self, config: ContextConfig, estimator: TokenEstimator): ...
    @property occupied(self) -> int
    @property allows_auto_compact(self) -> bool
    @property manual_allowed(self) -> bool
    @property is_openckety(self) -> bool           # 熔断态
    async def should_compact(self) -> bool          # 自动触发判断
    def register_summarize_failure(self) -> None    # 连续失败计数（F6.2）
    def summarize_succeeded(self) -> None
    def reset_circuit(self) -> None                  # 解除熔断（F6.3）
    def record_offload(self, saved: int) -> None     # 层1回收：occupied -= saved
```

### context/offload.py（F1/F2）
```python
class ToolResultOffloader:
    def __init__(self, dir: Path): ...
    async def offload(self, result) -> OffloadedResult:      # 单结果>N
    async def offload_oversized(self, results) -> list:      # 合计>M 从大到小
    async def reopen_text(self, path: Path, max_chars: int) -> str:  # 供模型重读
```

### context/compactor.py（F3/F4/F5）
```python
class Compactor:
    async def compact(self, *, overview, recent, provider, model) -> CompactResult:
        # 独立非流式调用；失败抛异常（budget 计熔断）
    @staticmethod build_summary_prompt(overview, recent) -> str   # 含禁工具指令
    @staticmethod build_boundary_message() -> str                  # F5
```

### context/manager.py（编排入口）
```python
class ContextManager:
    def __init__(self, config, offloader, compactor, provider, chat): ...
    async pre_request(self):                        # send_message 前：层1批 + 层2判断
    async prep_tool_result(self, result) -> object: # agent 每轮层1单结果
    async compact_manual(self):                     # F6.1
    def current_usage_text(self, *, manual=False) -> str:  # F7
    def on_usage_anchor(self, input_tokens) -> None:       # 每轮 usage 重锚
    def reset_circuit(self) -> None
```

## 模块交互

### 一次对话完整数据流
```
UI on_input_submitted → ChatManager.send_message(text)
  │ ctx = build_context()          ← F0 已含持久化工具结果
  │ await manager.pre_request()    ← 层1批(本消息) + 层2判断(累积历史)
  │   [F2] offload_oversized → record_offload
  │   [F3] budget.should_compact? → compactor.compact → 替早期/保留近期/边界消息/重锚
  ▼
agent.run(ctx)  每轮：
  │ delta.usage.input_tokens → manager.on_usage_anchor()   # 锚点
  │ ToolResult → manager.prep_tool_result()                 # [F1] 单结果>N 存盘
  │   → 替身(预览+路径)回灌进 ctx
  │ _append_assistant / _append_tool_results
  ▼
TUI 状态栏 current_usage_text（F7）
```

### 摘要独立调用（F4）
- 走 provider 新增**非流式纯文本完成**方法，不走主 stream（避免混工具/缓存日志/usage 计主对话）。
- 草稿在内部临时变量，用完即弃，不入任何历史；正式摘要才写入。

### 压缩作用到历史
- 层1：`agent._append_tool_results` 前 `prep_tool_result` 返回替身回灌，对话留预览+路径。
- 层2：`send_message` 的 `pre_request` 阶段改 `chat.history`（早期→摘要块，保留近期原文，追边界消息）。

### 依赖方向（无环）
```
config.py ← context/* ← chat.py/agent.py ← ui/app.py
context 不 import agent/chat，只通过注入的 chat.history 交互；
agent 通过注入的 manager 触发层1，不反向依赖。
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 工具结果持久化 | 并入 `ChatManager.history`，`build_context()` 序列化时包含 | F0；层1/层2 有作用对象 |
| 压缩入口 | `ContextManager.pre_request()` 在 `send_message` 内、`run` 前 | 单点干预，不动 agent 核心 |
| 层1介入时机 | `agent._append_tool_results` 前 `prep_tool_result` 替身拦截 | 即时拦截，不改 agent 契约 |
| 摘要调用 | provider 新增独立非流式纯文本完成 | F4.3：禁工具/草稿不入历史/usage 不累计 |
| token 估算 | 纯模块，`chars//4`+`words×1.3`，锚定 usage 只对增量 | N1；不引 tokenizer |
| 配置 | `context:` 段全阈值可配 | N3 |
| 熔断 | Budget 计数连续 3 次失败→熔断；手动解除 | F6.2/6.3 |
| TUI 显示 | `current_usage_text()` 与估算同口径；压缩后触发刷新 | F7 |
| 层1存盘位置 | 会话目录 `context/` 子目录（随 JSONL 走） | 与历史同生命周期 |
| 降级 | 存盘失败→留预览；摘要失败→熔断不崩 | N5 |

## 文件组织
```
codepilot/
├── config.py                  (M) ContextConfig + load_context_config
├── context/
│   ├── __init__.py            导出 ContextManager, ContextConfig
│   ├── estimator.py           TokenEstimator + estimate_text
│   ├── budget.py              ContextBudget + ContextConfig（配置模型）
│   ├── offload.py             ToolResultOffloader
│   ├── compactor.py           Compactor（摘要+边界消息）
│   └── manager.py             ContextManager（编排）
├── chat.py                    (M) build_context 纳入工具结果（F0）
├── agent.py                   (M) prep_tool_result 拦截层1
├── providers.py/protocols.py  (M) 新增非流式纯文本完成方法
├── ui/app.py  ui/widgets/status_bar.py  (M) F7
└── tests/
    └── context/
        ├── test_estimator.py  test_budget.py  test_offload.py
        ├── test_compactor.py  test_manager.py（含 F0 跨轮持久回归）
```

## 实施顺序提示
F0（工具结果跨轮持久）为 T1，是层1/层2 的前提——部署到 task.md 的第一个任务。