# SHARED_PROGRESS.md — 跨 Agent 共享工作进度

> 本文件用于向其他 Agent / 新会话同步 CodePilot 项目的**当前工作状态**。
> 触发词：用户说「**更新共享工作进度**」时，把当前工作内容压缩后写入本文件。
> 本文件保持精简——是「进度快照」，不是完整文档；细节各归各的 state 文档。

---

## 最近一次更新：2026-10-17

### state16（开发完成 + 真机验收通过）
**state16：Hook 系统**（`docs/state16-hook-system/`）
- **状态：四文档获批后开发完成，482 测试绿（原 391 + 91 新增），state16 文件 ruff 全清；真机验收通过。**
- 目标：在 Agent 生命周期关键节点挂自动化动作（事件+条件+动作三要素），让格式化/拦截/上下文注入从人工变机器。承接 state15/14「动态能力」方向。
- **架构**：新包 `codepilot/hooks/`（model/loader/engine/actions/injector）。8 事件（session_start/end、round_start、user_message、tool_pre/tool_post、compact、stop）；条件复用权限匹配器（**共享 match_text 升级四语法**：精确/`!`反向/`/regex/`正则/glob，权限规则与 Hook 条件共用）；四动作（command/http/prompt/subagent占位）；执行控制（once 会话内/async 后台/timeout，拦截事件禁 async）；两级 `.hooks.yaml`（项目>用户）叠加；F10 集中校验 fail-fast；Hook 失败只记日志不中断主流程。
- **关键裁决**：
  1. 拦截 = tool_pre 上 command 退出码非 0，stderr 作拒绝原因回灌模型（不引第五种动作）；异常/超时 fail-open。
  2. 提示词注入走 volatile 尾块（复用 memory/skill 注入缝，去重钉住，`/clear` 复位）。
  3. 条件四语法升级共享 `permissions/rules.match_text`——**顺带修 `parse_rule` 冒号定界 bug**（pattern 内含冒号如正则 `/^C:/` 会被 partition 截断，验收场景6暴露）。
  4. 工具调用渲染方案 A：每工具一行（结果内嵌 ✅/✕）直接写进可滚动 `#chat`，移除 `#tools` 瞬时面板/ToolRow。
  5. **滚动健康修复（ChatLog）**：Textual 8.2.8 RichLog 的 `scroll_end(immediate=False)` 延迟落地会在流式写入时把用户上滚位置拽回底部——自实现「仅位于底部才跟随」（write 前判断，immediate=True 同步跟随）。
- **真机验收**：场景2 拦截✓（模型读配置改用 write 绕开）、场景3 注入+/clear✓（headless 铁证复位）、场景4 异步超时✓（日志自证不阻塞）、场景5 fail-fast✓（启动即报错）、场景6 权限新语法✓（含 parse_rule 修复）。场景1 自动格式化**降级为设计讨论 ADR-003**（「每次 edit 后自动格式化」是 AI 助手语境反模式：模型无反馈通道/对抗有意写入/异步竞态/风格噪音）。
- 文件：`codepilot/hooks/*`、`permissions/rules.py`（匹配器+parse_rule）、`prompt/builder.py`、`agent.py`、`chat.py`、`context/manager.py`、`ui/app.py`、`ui/widgets/chat_log.py`、`main.py`、6 个测试文件（tests/hooks/* + test_streaming_scroll.py）。
- 遗留：`[hook] failed` TUI 系统行可见性弱（可选 UX 优化）；`.hooks.yaml` 已 gitignore（运行时配置）；ADR-003 记录反模式。
- 新上手：读本文 + `docs/state16-hook-system/{spec,plan,task,checklist}.md` 即可恢复。下一步 state17 范围待定。

### state15（开发完成 + 真机验收通过）
**state15：Skill 系统**（`docs/state15-skill-system/`）
- **状态：四文档获批后开发完成，391 测试绿，state15 文件 ruff 全清；真机验收通过。**
- 目标：把可复用 AI 操作封装成带元信息的 Markdown 能力包，两阶段加载 + 共享/独立双执行 + 工具白名单收窄，解决「反复输入同样提示词」。承接 state14 明确的后续（动态命令注入）。
- **架构**：新包 `codepilot/skills/`（model/loader/resolver/manager/tool + data 内置样板）。三级存放 `.skills`（项目>用户>内置 `skills/data/`，随包 package-data）。两阶段注入复用 `prompt/builder` volatile 尾块（`skill_blocks`，env 后最显眼位）；白名单复用 `agent._build_tools` 交集（∩plan 只读，系统 `load_skill` 恒可见为逃生门）；命令动态注册复用 state14 `CommandRegistry.register(replace=True)`。
- **关键裁决**：
  1. **目录 `.skills` 三级**（项目/用户/内置），同名高者覆盖；内置打包走 `pyproject package-data`。
  2. **白名单硬收窄**：可见工具 = 全量 ∩（白名单∪系统）∩（plan 只读）；`tools` 留空=该 skill 不约束；多激活取并集。
  3. **历史量仅 frontmatter 定死**；独立模式模型覆盖仅 isolated 生效。
  4. **撞名统一**：Skill 命令用 `replace=True` 胜出；state14 `/review` PROMPT 命令移除、载荷并入内置 review skill（`/review` 现由 review skill 提供，isolated 独立对话）。
  5. **/clear 联动**：清空对话时 `deactivate_all` 复位激活+白名单，但已注册 skill 命令保留（可快速重触发）。
- 内置三样板：commit(shared) / review(isolated,目录型) / test(shared)。
- 文件：`codepilot/skills/*`、`prompt/{builder,sections}.py`、`agent.py`、`tools/base.py`、`commands/registry.py`、`commands/builtins.py`、`ui/app.py`、`main.py`、`pyproject.toml`、6 个新测试。
- **真机验收（用户实机确认）**：
  - 自主判断：新对话让模型给 commands 包写文档，第一步自调 `load_skill` 切入 mkdocs ✓，且 load_skill 成功确认行已修到可见（不再折叠成 N lines）✓
  - `--resume` 恢复后不再报 `messages.tool_calls.function` 400（restore 按协议重建工具调用形态）✓
  - `/reload` 会话内热重载：新/改 skill 立即可用，核心命令不被覆盖 ✓
  - `/review` isolated 独立对话（plan_mode 参数根因修复后走通）✓
  - `/code-review` 裸敲自动注入审查要求（auto_prompt 复刻旧 /review 即时感）+ 真机确认 ✓
- **真机修复链**（对本阶段质量很关键）：empty-messages → 补触发消息+文件日志(暴露真因) → 子 Agent 误传 plan_mode(真根因) → load_skill 确认行被 UI 折叠(可见性) → resume 400(tool_calls 请求形态)。

### state14（开发+验收全通过，已提交）
**state14：命令注册与分发机制**（`docs/state14-command-registry/`；提交 e6a0f3e 代码 + 跟随 docs）
- **状态：全部完成并提交流，338 测试绿（原 308 + 30 新增）。**
- 目标：把斜杠命令从硬编码 if-链泛化为「注册中心+解析器+分发器+界面控制接口」，让命令绕过 Agent 直接执行（响应快/省 token/行为确定）。
- **架构**：`codepilot/commands.py` → `commands/` 子包（registry/parser/dispatcher/builtins(18命令)/completer + context 协议）。`ui/app.py` `_submit` 收窄为分流器 + `_CommandContextImpl`（DIP）→ 命令不绑定 Textual。命令 `handler(ctx, params) -> str|None`（返回消息则转发 AI，None 本地）。`CommandKind` 三态：LOCAL/UI/PROMPT。
- **关键点**：
  1. 别名冲突启动期 fail-fast（register + assert_no_conflicts 双保险），而非运行时炸。
  2. 界面控制接口 `CommandContext` 是命令触达 UI 唯一通道，换渲染框架另实现即可复用。
  3. 模式标记 `[DEFAULT]/[PLAN]`（build 轴→DEFAULT）；`/plan` 只读轴、`/do` 恢复全工具，均支持带消息转发。
  4. Tab 补全：单命中直接补、多命中 `ModalScreen`+`ListView` 弹菜单；候选纯来自注册表、隐藏命令不出现。
  5. 未命中斜杠 → `/help` 引导**不送 AI**（方案 A）。
- **真机发现并修复一个 bug**：第一版补全屏用 `Static` 渲染候选在 Textual 8.2.8 触发 `'str has no render_strips'` 崩溃 → 改用 `ListView` 版，并加 headless `test_completion_screen.py`（加载真实 theme.tcss）做回归防线。
- **验收**：checklist 27/27 全勾（自动化 338 测 + 用户真机 19 清单 + 3 硬场景：/review PROMPT转发、/clear 清屏留上下文、/plan 只读 /do 恢复+转发；/continue 撑爆 18 轮续跑也验证过）。旧命令（exit/plan/do/mode/edits/compact/continue/memory/forget）全部迁移无回归。
- 18 条命令：LOCAL(help/exit/memory/session/status) · UI(plan/do/mode/auto/manual/strict/edits/compact/continue/forget/clear/permission) · PROMPT(review)。
- 遗留：仓库 30 处 ruff 为**既有基线**（test_tools_read 等旧文件），非本次引入，未动。
- 新上手：读本文 + `docs/state14-command-registry/{spec,plan,task,checklist}.md` 即可恢复。下一步 state N 具体范围待定。

### state13（开发完成，端到端真机验证通过，剩一项 TUI 观感终验）
**state13：项目指令 + 自动记忆 + 注入**（`docs/state13-memory/`）
- **状态：T1-T8 全部实现，308 测试绿。端到端真机链路全跑通**（自动记忆沉淀 +1 / `/forget` 删1再删0幂等 / 注入可见 / 边界无副作用）；只剩 checklist 场景1「模型按 .rules 指令风格回复」的 TUI 观感项待用户终验。
- **指令目录改名 `.mewcode → .rules` 已完成并提交**（避开机器 `.codepilot/` 前缀混淆）：`.rules/general.md` 手写版本已入 git（收录三条项目约定——尾附验收清单 / 中文 Conventional Commit / 4空格缩进）；`resolver.py` 已同步。
- 新会话上手：读本文 + `docs/state13-memory/{spec,plan,task,checklist}.md` 即可完整恢复上下文。
- 验收证据：`screenshot/改名后memory.png`（`.rules` 改名后 `/memory` 分组渲染实拍）已随本提交入 git。

### state12（已验收通过）
**state12：会话恢复韧性**（`docs/state12-session-restore/spec.md`）
- **状态：T1-T9 + 两项 UX 收尾全部完成，274 测试绿（264 既有 + 10 新增），真机验收通过（用户实机确认：还原见对话、可回滚、--new 空白、无崩溃）**。
- 目标：让「中断→重启接着用」真正成立。写回(工具调用持久化)→恢复(dangling截断/token压一次/时间提醒)→清理(30天过期回收)。
- **已落地**：
  1. ID `YYYYMMDD-HHMMSS-xxxx`（本地时间+4位hex防同秒）→ `store.new_session_path`；列表按文件名降序。
  2. JSONL 新增 `assistant`(带tool_calls) + `tool_result` 行；`chat.py` 在工具结果回灌处补写 → 跨重启工具上下文可还原。
  3. `session/restore.py` 新增 `restore_chat` 编排：dangling 截断(无对应结果的tool_call从该行截)+ gap_hours 时间提醒(最前插role=user)；旧文件(仅meta/message/mode)向后兼容。
  4. `store.summarize` 扫 JSONL 现算 id/title/message_count/last_ts，不维护 meta 文件。
  5. `store.cleanup_expired(max_age_days=30)` 只删过期、幂等。
  6. `context/manager.compact_if_over()` 单发压缩(复用 _do_compact)。
  7. `main.py --resume/--new`(裸启动默认新会话;auto_resume 默认 False,`-c`/`--resume`/开 auto_resume 才还原) + 启动过期清理 + `seed_msgs` 回灌 ChatManager；app.on_mount attach 后 token 超限压一次。
- **设计取舍记录**：token「先压一次」落在 `compact_if_over`(app.on_mount attach 后)，而非 restore_chat 内——restore 保纯可单测，压缩留在 context 层(chat 挂载后才可压)。dangling/时间提醒在 restore。
- **历史回放 UX（用户要求，对齐 Claude Code）**：`app.on_mount` 调 `_replay_history` 把恢复的上段对话整段画回 RichLog、可上滚；样式同新消息；`--new`/无历史时不画。让「只有记忆看不到对话」的缺口补上——恢复即见对话。
- **可观测性（真机验收时补）**：`restore_chat` 打 `restore <file>: N msgs (dangling_cut/M)`、`_pick_session` 打 `新开会话`/`过期清理删 N`——恢复可从 logs/codepilot.log 自证，非「感觉没问题」。
- 文件：`session/store.py`,`session/restore.py`(新),`chat.py`,`context/manager.py`,`main.py`,`ui/app.py`,`config.py`,`config.example.yaml`,`tests/test_restore.py`(新)。
- 阶段六验收通过（用户实机确认）：`-c`/`--resume` 还原见对话可回滚、`--new` 空白、无崩溃；恢复日志已自证。下一阶段 state13(指令+自动记忆)见下。

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