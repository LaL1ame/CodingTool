# SHARED_PROGRESS.md — 跨 Agent 共享工作进度

> 本文件用于向其他 Agent / 新会话同步 CodePilot 项目的**当前工作状态**。
> 触发词：用户说「**更新共享工作进度**」时，把当前工作内容压缩后写入本文件。
> 本文件保持精简——是「进度快照」，不是完整文档；细节各归各的 state 文档。

---

## 最近一次更新：2026-10-12

### state12（开发完成，待真机验收）
**state12：会话恢复韧性**（`docs/state12-session-restore/spec.md`）
- **状态：T1-T9 全部完成，274 测试绿（264 既有 + 10 新增），待人工真机验收（阶段六）**。
- 目标：让「中断→重启接着用」真正成立。写回(工具调用持久化)→恢复(dangling截断/token压一次/时间提醒)→清理(30天过期回收)。
- **已落地**：
  1. ID `YYYYMMDD-HHMMSS-xxxx`（本地时间+4位hex防同秒）→ `store.new_session_path`；列表按文件名降序。
  2. JSONL 新增 `assistant`(带tool_calls) + `tool_result` 行；`chat.py` 在工具结果回灌处补写 → 跨重启工具上下文可还原。
  3. `session/restore.py` 新增 `restore_chat` 编排：dangling 截断(无对应结果的tool_call从该行截)+ gap_hours 时间提醒(最前插role=user)；旧文件(仅meta/message/mode)向后兼容。
  4. `store.summarize` 扫 JSONL 现算 id/title/message_count/last_ts，不维护 meta 文件。
  5. `store.cleanup_expired(max_age_days=30)` 只删过期、幂等。
  6. `context/manager.compact_if_over()` 单发压缩(复用 _do_compact)。
  7. `main.py --resume(默认auto_resume)/--new` + 启动过期清理 + `seed_msgs` 回灌 ChatManager；app.on_mount attach 后 token 超限压一次。
- **设计取舍记录**：token「先压一次」落在 `compact_if_over`(app.on_mount attach 后)，而非 restore_chat 内——restore 保纯可单测，压缩留在 context 层(chat 挂载后才可压)。dangling/时间提醒在 restore。
- 文件：`session/store.py`,`session/restore.py`(新),`chat.py`,`context/manager.py`,`main.py`,`ui/app.py`,`config.py`,`config.example.yaml`,`tests/test_restore.py`(新)。
- 待做：**真机验收**——带工具多轮 → 中断 → 重启 `--resume` → 上下文完整还原/dangling截断/无崩溃。state13(指令+自动记忆)见下。

### state13（规划 outline，未开始）
**state13：项目指令 + 自动记忆 + 注入**（下一阶段，spec 待编）
- 三层指令文件(`.mewcode/` 项目根 / `.mewcode/` 子目录 / `~/.mewcode/`)手写 Markdown，按优先级拼接 + `@include`(限深/visited 防环/防逃逸)。
- 四类自动笔记(用户偏好/纠正反馈/项目知识/参考资料)：每轮 Agent Loop 自然停(done,无工具调用)后**异步**调 LLM 更新，去重交 LLM；用户级 `~/.codepilot/memory/` 与项目级分开存；索引 ≤200 行/25KB 注入 prompt。
- 记忆注入点：`prompt/builder.py` 分层 system prompt 加 volatile 记忆块；大小经 context estimator 控。
- 明确不做：向量库/RAG/团队记忆同步。

### state11（已验收通过）
**state11：TUI 多行输入**（`docs/state11-multiline-input/`）
- **已验收完成**：264 测试绿。
- 起因：真机验证暴露 Textual 单行 `Input` 无法粘多行 prompt。
- **定稿交互**：**Enter 发送 / Ctrl+J 换行**；输入框随内容自动长高(40%屏高内)、启动自动聚焦；多行可粘贴。
- **换行键定稿过程（真机排查）**：先 B(Ctrl+Enter 发送)→翻转 A(Enter 发送)→`Ctrl/Shift/Alt+Enter` 换行在本机 Windows 终端**全被折叠成 enter(等于发送)**→最终 `ctrl+j`(LF 控制符)独立送达、可靠换行。移除多余换行键，`_NEWLINE_KEYS={"ctrl+j"}`。
- **关键发现**：`TextArea._on_key` 内部把 enter 直映射 `"\n"`，**声明式 BINDINGS 拦不住** → 子类 `PromptInput` 覆写 `_on_key`。
- **顺带修功能性 bug**：启动焦点落 RichLog、按键不达输入框 → `on_mount` 加 `prompt.focus()`。
- 组件：`codepilot/ui/widgets/prompt_input.py`；`on_prompt_input_submitted`→`_submit` 统一入口。

### state10（已验收通过）
**state10：Agent 停止判定增强**（`docs/state10-agent-stop-policy/`）
- **处于：阶段六验收通过（264 测试绿）**。核心 A/B 真机验证完成；验收中发现并修复 state9 层1 契约缺陷（见下）。
- **真机验收结论**：
  - A：读全部 55 个 py，read 全量回灌、无预览截断，正常 done。
  - B：`max_rounds=3` 达上限 → `paused(limit)`（非 error）→ `/continue` 续接 → 模型基于已读上下文直接给出最终结论、不重读 → done。
  - 两层分工实测正确：read（自分页）豁免层1；grep（非分页大输出）层1存盘可重读。
- 遗留（可选）：场景2「真机诱导空转」未单独专测（合约已由单测 TestStallDetection 覆盖）。

**验收修复：read 豁免层1存盘**（2026-10-12）
- 现象：验收「读所有 py 并总结」时，read 输出总被截成预览，模型无法完整读取。
- 根因：config.yaml 残留 state9 验收用的 `tool_threshold_n: 250`（正文注释本就写明「正式可删」），把每个真实 read 结果都压到 200 字符预览；即使回默认 `1000`，核心源文件(agent/config/protocols 等 >1000 tok) 仍会中招。
- 更深的契约缺口：层1存盘对 `read` 是「预览 → 按路径重读 → 再存盘」**死循环**（重读 offload 文件又 > 阈值再存盘）。
- 修复（已批准方案）：`read`（自带 offset/limit 分页 + READ_LIMIT 行内截断 = 自身上下文边界机制）豁免层1存盘；层1只面向模型无法分页的不透明大输出(run/grep 大块)；文件读多后的历史老化交给层2摘要。
- 落地：`codepilot/context/manager.py` 增加 `_SELF_PAGING_TOOLS={"read"}`；回归测试改 `test_prep_tool_result_offloads_large` 用 grep、新增 `test_prep_tool_result_exempts_read`。`config.yaml`(gitignored) 删除 `context:` 残留段回默认。
- 真机验证：A) 读 tools/*.py+commands 全量回灌、正常 done ✓；B) max_rounds=2 达上限→`paused(limit)`→同 `_msgs` resume→DONE 续跑完成 ✓。
- 目标：Agent 停止判定从「数轮数 + 全当 error」改为「进展感知 + 区别分类 + 可续接」。
- 方案（参考 Claude Code 机制对齐）：
  - F1: max_rounds 默认 10→18（state9 保证不爆窗口）。
  - F2: 达上限 = `paused(reason=limit)` 可续接检查点（非 error），同一持久 `_msgs` 续跑。
  - F3: 空转「软守卫」`_StallDetector`——lex 级(名+参数hash)重复 + 无文本进展 → stall_hint（提示换策略，仍跑）→ 持续才 paused(stall)。
  - F4: 停止 reason 结构化 done/limit/stall/unknown_tool/cancelled/error。
  - F5: /continue 命令续接。
- 8 任务全实现：T1 Delta 字段→T2 AgentConfig→T3 StallDetector→T4 停止判定重构→T5 /continue→T6 续接→T7 UI→T8 测试。
- **测试设计要点**：limit / unknown_tool / plan-injection 三测试各自 `stall_enabled=False` 隔离机制（三个停止阀正交）；stall 语义专测放 test_agent_resume.py::TestStallDetection。
- 提交：7 个独立 Conventional Commit（docs→T1→…→T8）。
- 待做：人工验收（阶段六），验 /continue 续接、空转软提示、达上限 paused。

### 前一 state（已完成开发）
**state9：上下文管理 + Token 优化**（`docs/state9-context-management/`）
- 开发完成（11 任务全实现，256 测试绿），等待人工验收（阶段六）。
- 两层压缩：层1 工具结果存盘(offload) + 层2 LLM 摘要(compactor)；TUI 上下文指示器。
- 前置 F0：工具结果跨消息轮持久（进程内；resume 恢复留后续）。
- 验收提示词清单已交付（用 thousands.py 分片读 /compact /熔断等）。
- 目标：给 CodePilot 装上两层压缩（层1 工具结果存盘 + 层2 LLM 结构化摘要），在有限 token 预算内长时间干活不溢出；TUI 实时显示上下文使用量。
- 关键裁决（已定）：
  - 前置改造 F0——工具结果**跨轮持久**（此前只在单次 agent.run ctx 内存活）。
  - 窗口上限 = config 显式配置（`context:` 段，如 `window: 200000`）。
  - 层1 阈值 = **token 量纲**。
  - 新增 F7：TUI 状态栏实时显示上下文使用量。
  - token 估算：不引精确 tokenizer，锚定上次 `usage.input_tokens` + 对增量按字符估算（code `chars/4`）。
- 新子包 `codepilot/context/`：estimator / budget / offload / compactor / manager 五模块。
- **task.md 执行顺序**：T1(工具结果跨轮持久，最高优先) → T2估算 → T3配置 → T4状态机 → T5层1存盘 → T6层2摘要 → T7 provider独立完成方法(可在T2后并行) → T8编排 → T9接入agent → T10 UI → T11 main装配。
- 新增共享文件：`SHARED_PROGRESS.md`（本文件，跨 agent 工作进度同步）。

### 已完成里程碑（历史）
- works 若干 state：权限系统、模式状态机、会话 JSONL resume、MCP 客户端（211 测试绿）。
- MCP 现状：功能完成，唯一遗留——真实 npx/node server 的 Windows spawn 兼容未打通（非协议缺陷，独立后续）。

---

## 存档规范（如何用本文件）
- 每个功能 state 有自己的完整文档（`docs/state<N>-<topic>/spec+plan+task+checklist.md`）。
- 本文件只放「下一步做什么、当前踩到哪些决策、接缝在哪」——供新 agent 快速接手上手。
- 用户说「更新共享工作进度」时，调用方把对话里的工作内容压缩进这里。