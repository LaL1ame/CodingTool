# System Prompt 结构化 Checklist

> 每一项通过运行代码或观察行为验证，聚焦系统行为。

## 实现完整性

- [ ] 7 个固定模块齐全且按优先级排列（验证：`pytest tests/test_plan_prompt.py` 断言 stable 块数=7、顺序=priority）
- [ ] 稳定前缀逐 token 稳定（验证：同一输入两次 `build_system_prompt()` 的 stable 完全相等）
- [ ] 环境信息进入 volatile 且含 cwd/date（验证：builder 单测）——AC3
- [ ] 关键规则双通道存在（验证：`tool_usage` 稳定块含「编辑前必先读」等规则，且对应工具描述也含）——AC5

## 缓存序列化（Anthropic 一等公民）

- [ ] Anthropic body 的 system 为块列表，`cache_control` 只挂在稳定前缀**末位块**，volatile 尾块不挂（验证：protocols 序列化单测断言断点位置）——AC2
- [ ] tools 数组末位挂 `cache_control`（验证：protocols 单测）
- [ ] `cache_read_input_tokens`/`cache_creation_input_tokens` 被解析进 Usage（验证：protocols 单测喂入含 cache 字段的 message_stop）——AC6
- [ ] OpenAI 协议合并为单个 system 字符串、无断点/标签（验证：protocols 单测断言合并结果）——AC7

## 运行时注入

- [ ] plan mode 尾块带 `<session_instruction>` 标签、不作为 user 消息（验证：builder 单测 + 序列化断言它在 system 块而非 user）——AC4
- [ ] plan 按轮注入频率正确：round1 全文、round%N 精简、其他跳过（验证：builder 单测用例）——AC4
- [ ] plan_mode=False 时 volatile 恒不含 plan 块（验证：builder 单测）——AC4

## 集成

- [ ] agent 每轮重建 system 并传给 provider（验证：test_agent 用 spy 断言 provider 收到按轮生成的 SystemPrompt）——F6 落地
- [ ] chat `build_context()` 不再返回 role:system 首条（验证：`pytest tests/test_chat.py`）——transport 重构完成
- [ ] 所有公开接口至少被一个真实调用方使用（验证：全量测试通过）

## 验证与测试

- [ ] 项目无语法错误，`codepilot.prompt` 三模块导入成功（验证：`python -c "import codepilot.prompt"`）
- [ ] 全量单元测试通过（验证：`bash ci/run-tests.sh` → ALL CHECKS PASSED）——AC8
- [ ] 新增模块不改主流程即可拼入（验证：向 `FIXED_SECTIONS` 加一项后 `build_system_prompt` 自动包含）——AC10

## 端到端场景

- [ ] **场景 1（真机缓存命中）**：以 Anthropic 协议启动，`MEWCODE_LOG=logs/codepilot.log` 连跑多轮任务 → 第 2 轮起日志出现 `cache_read` 字段（验证：读 logs/codepilot.log）——AC6/AC9 实锤
- [ ] **场景 2（plan 按轮注入）**：执行 `/plan <任务>` → round1 日志/system 含完整 plan 说明，后续精简或跳过（验证：日志 + 观察）——AC4/AC9
- [ ] **场景 3（OpenAI 降级）**：切 openai 协议跑通任务、不报错（验证：一次真机调用）——AC7
- [ ] **场景 4（人工对比）**：同一任务在「结构化前/后」跑一次，记录遵守率与成本定性对比（验证：人工记录到文档）——AC9