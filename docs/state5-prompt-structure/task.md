# System Prompt 结构化 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `codepilot/prompt/__init__.py` | 导出公开符号 |
| 新建 | `codepilot/prompt/sections.py` | 7 固定模块 + plan 全文/精简 + 环境块 + 标签 |
| 新建 | `codepilot/prompt/builder.py` | Section/EnvContext/SystemPrompt + build_system_prompt + 频率规则 |
| 新建 | `tests/test_plan_prompt.py` | builder 组装/优先级/频率/标签测试 |
| 修改 | `codepilot/config.py` | AgentConfig.prompt_refresh_every |
| 修改 | `codepilot/protocols.py` | SystemPrompt 序列化 + Usage cache 字段 + tools cache_control |
| 修改 | `codepilot/providers.py` | stream 增加 system 透传 |
| 修改 | `codepilot/agent.py` | 每轮重建 system + plan 注入 + cache 日志 |
| 修改 | `codepilot/chat.py` | 移除 role:system 注入 |
| 修改 | `codepilot/main.py` | EnvContext + 刷新率注入 |
| 修改 | `tests/test_protocols.py` `tests/test_agent.py` `tests/test_chat.py` | 同步断言 |

## T1: config 加刷新率

**文件：** `codepilot/config.py`
**依赖：** 无
**步骤：**
1. `AgentConfig` 增加字段 `prompt_refresh_every: int = 3`
2. `load_agent_config` 读 `entry.get("prompt_refresh_every", 3)`
3. `config.example.yaml` 的 `agent:` 段补一行注释说明
**验证：** `tests/test_config.py` 增加断言（默认 3、解析 5），`pytest tests/test_config.py -q` 通过

## T2: 内容定义

**文件：** `codepilot/prompt/sections.py`
**依赖：** 无
**步骤：**
1. 定义 `FIXED_SECTIONS: list[Section]`，7 个模块（identity/system_constraints/task_mode/action_execution/tool_usage/tone/output_style），priority 0..6，内容迁移自现有 SYSTEM_PROMPT 并扩充（含「编辑前必先读」「优先专用工具」等关键规则于 tool_usage）
2. 定义 `PLAN_FULL`（完整 plan 说明）与 `PLAN_CONCISE`（精简），作为后续 `_prompt_tag` 内容
3. 定义 `format_env(env: EnvContext) -> str`（工作目录、日期）
4. 定义标签常量 `SESSION_TAG = "session_instruction"`
**验证：** `python -c "from codepilot.prompt.sections import FIXED_SECTIONS; assert len(FIXED_SECTIONS)==7"` 导入成功且数量正确

## T3: 拼装器

**文件：** `codepilot/prompt/builder.py` + `codepilot/prompt/__init__.py`
**依赖：** T2
**步骤：**
1. 定义 `Section`/`EnvContext`/`SystemPrompt(stable: list[str], volatile: list[str])` dataclass
2. 实现 `_prompt_tag(text) -> str` 包 `<session_instruction>…</session_instruction>`
3. 实现频率规则 `_plan_blocks(plan_mode, round_num, refresh_every) -> list[str]`（首轮全文 / round%N==0 精简 / 其他空）
4. 实现 `build_system_prompt(env, *, plan_mode, round_num, refresh_every) -> SystemPrompt`（stable=按 priority 排的固定模块；volatile=[env] + plan 块）
5. `__init__.py` 导出 Section/EnvContext/SystemPrompt/build_system_prompt
**验证：** `python -c "from codepilot.prompt import build_system_prompt, SystemPrompt"` 导入成功

## T4: builder 单测

**文件：** `tests/test_plan_prompt.py`
**依赖：** T3
**步骤：**
1. 稳定前缀 priority 排序正确
2. 同输入 stable 逐 token 不变（缓存前提）
3. env 在 volatile 且含 cwd/date
4. plan 频率：round1 全文、round4(refresh=3) 精简、round2 无
5. 注入块带 `<session_instruction>` 标签
6. plan_mode=False 时 volatile 恒不含 plan 块
**验证：** `pytest tests/test_plan_prompt.py -q` 全绿

## T5: 协议序列化 + cache 字段

**文件：** `codepilot/protocols.py`
**依赖：** T3（SystemPrompt 类型）
**步骤：**
1. `Usage` 增加可选字段 `cache_read: int | None`、`cache_creation: int | None`
2. `AnthropicProtocol.stream`: 新增 `system: SystemPrompt | None` 参数；若提供，body["system"] = stable 块（**末位挂** `cache_control={type:ephemeral}`）+ volatile 尾块（不挂）
3. anthropic tools 数组若存在，末位补 `cache_control`
4. `message_stop` 解析 `cache_read_input_tokens`/`cache_creation_input_tokens` → Usage
5. `OpenAIProtocol.stream`: 新增 `system` 参数；合并为 `"\n\n".join(stable+volatile)`，在 body 前插 `{"role":"system","content": …}`
6. 更新 `tests/test_protocols.py`：新增 anthropic system-block 序列化（断点在末位 stable）、tools cache_control、cache 字段解析、openai 合并字符串断言
**验证：** `pytest tests/test_protocols.py -q` 通过

## T6: provider 透传

**文件：** `codepilot/providers.py`
**依赖：** T5
**步骤：**
1. `BaseProvider.stream(messages, tools=None, system: SystemPrompt|None = None)` 改签名
2. AnthropicProvider/OpenAIProvider 把 system 透传给底层 protocol.stream
**验证：** 静态检查 + `pytest tests/ -q` 无新失败（providers 无独立测试，随集成覆盖）

## T7: agent 每轮重建 + 注入 + 日志

**文件：** `codepilot/agent.py`
**依赖：** T1, T3, T6
**步骤：**
1. `__init__` 接收 `env: EnvContext`、`refresh_every: int`；注入 `self._env`
2. 删除 `system_prompt()` 与 `SYSTEM_PROMPT`/`PLAN_MODE_SUFFIX` 常量
3. `run()` 每轮开头 `sp = build_system_prompt(self._env, plan_mode=self._plan_mode, round_num=round_num, refresh_every=...)`，传 `provider.stream(messages, tools=tools, system=sp)`
4. `set_plan_mode()` 保留
5. usage 日志补记 cache_read/cache_creation
6. 更新 `tests/test_agent.py`：断言 provider 收到按轮生成的 system（用 spy），plan 注入频率正确
**验证：** `pytest tests/test_agent.py -q` 通过

## T8: chat 抽离 system

**文件：** `codepilot/chat.py`
**依赖：** T7
**步骤：**
1. `build_context()` 移除 `{"role":"system",...}`，只返回 user/assistant 历史
2. 更新 `tests/test_chat.py`：断言 context 首条不再是 system
**验证：** `pytest tests/test_chat.py -q` 通过

## T9: main 接线

**文件：** `codepilot/main.py`
**依赖：** T1, T7
**步骤：**
1. 构造 `EnvContext(cwd=Path.cwd(), date=<当前日期字符串>)`
2. `load_agent_config` 取 `prompt_refresh_every` 传给 Agent
**验证：** `python -c "import codepilot.main"` 导入成功

## T10: 全量回归

**文件：** 全仓
**依赖：** 全部
**步骤：**
1. `bash ci/run-tests.sh` 全量通过
2. 手工核对：`codepilot/prompt/` 三个模块导入、日志出现 cache 字段（如有 API）
**验证：** ALL CHECKS PASSED

## 执行顺序

```
T1 ─┬─────────────┐
    ├──→ T2 → T3 → T4
    │         │
    └─────────┴────→ T5 → T6 → T7 → T8 → T9 → T10
```

T1 与 T2 可并行；T3 后 T4 与 T5 可并行；T10 为最终门禁。