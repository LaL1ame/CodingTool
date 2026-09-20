# System Prompt 结构化 Plan

## 架构概览

方案 A：**双层系统提示 + 单缓存断点**。新增一个 `prompt` 包负责任意侧提示的**生成**，协议层负责把生成结果**序列化**进各自请求体。

职责分离（SRP）：
- **生成**（prompt 包）：稳定模块 + 动态尾块，产出协议中立的 `SystemPrompt`。
- **传输**（agent 循环）：每轮重建 `SystemPrompt`（含轮次驱动的 plan-mode 注入），传给协议。
- **序列化**（protocols）：Anthropic → block 列表 + `cache_control` 断点；OpenAI → 合并字符串。
- **验证**（agent + protocols）：Anthropic 解析 cache 命中字段并记日志。

关键迁移：**system 从 messages 列表抽离**（chat.py），改由 Agent 每轮生成并经 `provider.stream(system=...)` 独立传入——这是实现「按轮注入」的前提。

## 核心数据结构

### Section
```python
@dataclass
class Section:
    name: str       # 标识，如 "identity"
    priority: int   # 越小越靠前
    content: str
```

### EnvContext
```python
@dataclass
class EnvContext:
    cwd: str    # 当前工作目录
    date: str   # 会话日期字符串
```

### SystemPrompt（协议中立产物）
```python
@dataclass
class SystemPrompt:
    stable: list[str]     # 稳定前缀块（缓存区）
    volatile: list[str]   # 动态尾块（断点后、不进缓存）
```

序列化约定：
- Anthropic：`system` = `[{text: b, cache_control?} for b in stable + volatile]`，`cache_control` 只挂在**最后一块 stable** 上（断点）；volatile 不挂。
- OpenAI：`system` 字符串 = `"\n\n".join(stable + volatile)`（无断点/标签语义，纯合并）。

### 频率注入规则
```
plan_mode 关闭            → volatile 恒为 []
plan_mode 开启 & round==1  → 注入【全文】块
plan_mode 开启 & round%N==0 → 注入【精简】块
plan_mode 开启 & 其他      → 不注入
```

## 模块设计

### codepilot/prompt/sections.py — 内容定义
**职责：** 各模块的文本内容、plan-mode 全文/精简文本、环境块格式、特殊标签常量。
```
FIXED_SECTIONS: list[Section]  # 7 个固定模块，按优先级 0..6
PLAN_FULL / PLAN_CONCISE       # plan-mode 两挡文本，含 <session_instruction> 标签
format_env(EnvContext) -> str  # 环境信息块
```
**依赖：** 无。

### codepilot/prompt/builder.py — 拼装
**职责：** 产出 `SystemPrompt`。
```
build_system_prompt(env, *, plan_mode, round_num, refresh_every) -> SystemPrompt
    stable   = [按 priority 排序的 FIXED_SECTIONS 内容]
    volatile = [env 块] + _plan_blocks(...)   # 频率规则在此
_prompt_tag(text) -> str  # 包 <session_instruction> 标签
```
**依赖：** sections.py。

### codepilot/protocols.py — 序列化 + 缓存解析
**职责：** 把 `SystemPrompt` 写进各自 body；解析 cache 字段。
```
AnthropicProtocol.stream(messages, system: SystemPrompt, tools, thinking)
    body["system"] = stable 块(末位挂 cache_control) + volatile 尾块
    tools 数组末位挂 cache_control（工具定义进缓存，F2）
    message_stop 解析 cache_read/cache_creation_input_tokens → Usage
OpenAIProtocol.stream(messages, system: SystemPrompt, tools)
    body 前插 {"role":"system","content": 合并字符串}
Usage 增加 cache_read/cache_creation 可选字段
```
**依赖：** prompt.builder（SystemPrompt 类型）。

### codepilot/providers.py — 透传
**职责：** `stream(messages, tools=None, system: SystemPrompt | None = None)` 透传给底层协议。

### codepilot/agent.py — 每轮重建 + 注入 + 日志
**职责：**
```
run():
  每轮 for_round 开始时:
    sp = build_system_prompt(env, plan_mode, round_num, refresh_every)
    yield provider.stream(messages, tools, system=sp)
  system_prompt() / PLAN_MODE_SUFFIX 相关逻辑移除，交由 builder
  usage 日志追加 cache_read/cache_creation
```
`set_plan_mode()` 保留（会话级开关），只影响 builder 输入。

### codepilot/chat.py — 抽离 system
**职责：** `build_context()` 不再注入 role:system 消息，只返回 user/assistant 历史。

### codepilot/config.py / main.py
- `AgentConfig` 增加 `prompt_refresh_every: int = 3`（F6 的 N）。
- `main.py` 构造 `EnvContext(cwd, now)`，刷新率从 agent_config 读取并注入 Agent。

## 模块交互

```
main ─Envc/refresh─> agent
agent 每轮: build_system_prompt(env, plan_mode, round_num)
              └─> prompt.builder ──> SystemPrompt
agent: provider.stream(messages, tools, system=SystemPrompt)
              └─> protocols: Anthropic 序列化 block+cache / OpenAI 合并
message_stop usage ─含 cache 字段─> agent 记日志
```

## 文件组织
```
codepilot/
├── prompt/
│   ├── __init__.py     — 导出 Section/SystemPrompt/EnvContext/build_system_prompt
│   ├── sections.py     — 7 固定模块 + plan 全文/精简 + 环境块 + 标签
│   └── builder.py      — Section/EnvContext/SystemPrompt + build_system_prompt + 频率规则
├── protocols.py        — 序列化 + Usage 扩展 cache 字段
├── providers.py        — stream 增加 system 透传
├── agent.py            — 每轮重建 + plan 注入 + cache 日志
├── chat.py             — 移除 role:system 注入
├── config.py           — AgentConfig.prompt_refresh_every
└── main.py             — EnvContext + 刷新率注入
tests/
├── test_plan_prompt.py — 新：builder 组装/优先级/频率/标签
├── test_protocols.py   — 改：Anthropic system 序列化 + cache 字段
├── test_agent.py       — 改：plan 按轮注入
└── test_chat.py        — 改：build_context 无 system
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 方案 | A 双层 + 单断点 | 最小完整覆盖 Anthropic 缓存语义；非方案 C 的多断点 |
| transport | system 抽离 messages，`provider.stream(system=...)` | 实现每轮重建的前提；Anthropic 原生 system 参数才支持块级 cache_control |
| 断点位置 | stable 末位块挂 cache_control | 前缀逐 token 稳定可命中；volatile 后置不动前缀 |
| tools 缓存 | 协议层在 tools 末位补 cache_control | 工具定义进缓存（F2），registry 不感知缓存（低耦合） |
| 动态注入形态 | system 尾块 + `<session_instruction>` 标签 | 天然非 user 消息、不污染历史、不动稳定前缀（AC4） |
| 频率规则 | 首轮全文 / round%N==0 精简 / 其他跳过，N 可配 | 控制注入频率（F6） |
| OpenAI | 合并字符串 + 无断点/标签 | 降级通道（F8/AC7），无 cache 能力 |
| env 来源 | EnvContext(cwd, now)，main 构造 | 与网络/协议解耦，便于测试 |
| cache 验证 | 仅日志，不动 UI | 范围收敛（F7），N5 保持 UI 稳定 |