# SHARED_PROGRESS.md — 跨 Agent 共享工作进度

> 本文件用于向其他 Agent / 新会话同步 CodePilot 项目的**当前工作状态**。
> 触发词：用户说「**更新共享工作进度**」时，把当前工作内容压缩后写入本文件。
> 本文件保持精简——是「进度快照」，不是完整文档；细节各归各的 state 文档。

---

## 最近一次更新：2026-10-19

### state19（开发完成 + 真机验收通过）
**state19：Team Lead 长期小组**（`docs/state19-team/`）
- **状态：四文档获批后开发完成，905 测试绿（state18 基线 730 + 新增 175），新/改文件 ruff 全清；真机场景 1–10 + 汇总不合并全过（2026-10-19 收口）。**
- 目标：主 Agent 升级为 Team Lead，创建长期小组（组/成员花名册/持久化），派生成员并行干活；成员之间通过共享任务板（带依赖）和邮箱直接协作，不经过 Lead 中转；需审批成员先交计划由 Lead 结构化批准/驳回。state20 再做独立终端后端、跨进程上下文恢复、git 合并与 coordinator 双锁。
- **架构**：新包 `codepilot/teams/`（model/naming/settings/locks/storage/tasks/registry/mailbox/manager/runtime/prompts/tools_lead/tools_member）。四层单向依赖：领域存储层（无 UI）→ 成员运行时（复用 Agent 类，长期存活 + 内存上下文续接）→ Lead 编排层（TeamManager + 13 个 Lead 工具）→ 装配/界面（main/App/命令）。工具面隔离：base registry（普通子 Agent 用）+ lead registry（主 Agent 多出 team_*）+ 成员 registry（只多 task_*/send_message/broadcast，排除 agent/load_skill）。
- **成员隔离**：每成员一个独立 worktree（`.codepilot/worktrees/team/<team>/<member>`，分支 `codepilot/team/<team>/<member>`）；进程内 RLock + 占用表；`WorktreeManager.release` 保留目录仅释放锁。
- **协作**：任务板 CRUD + 领取门控（依赖全 done 才可领，原子，Lead 指派或成员抢单）；邮箱 JSONL + 名称注册表两段式投递 + 锁文件（O_EXCL 重试/过期接管）；五类消息 text/broadcast/plan_request/plan_decision/status；审批每任务一次，超时转 blocked 继续等待；消息在任务边界消费；成员 idle 后 Lead 发消息内存唤醒；重启后团队持久、成员 offline。
- **真机暴露并修复**（4 个，均有回归测试）：
  1. `daf3c82` 锁重入超时（update 在 team.lock 内再 acquire 非重入锁）→ 校验移锁外。
  2. `2e3f28b` 团队事件回灌把 text 插进 tool_result 前 → 协议 400（protocol-trace R1）；Agent.run 入口补齐 dangling tool_use（R2/R3）。
  3. `f37fd98` 空转守卫结果感知（同参数结果变=合法轮询不拦）+ Lead 待审批计划注入 volatile 块（A+C）。
  4. `33924be` team_inbox 误标 side_effect=True 被副作用空转拦截（发两次才执行）→ 改只读。
- **已知边界/backlog（未修，按影响排序）**：
  1. Lead 行为漂移：信任不足时用 run/grep/read 翻团队存储/源码（场景9 撞 18 轮）。→ 提示硬约束「团队状态只用 team_* 读，禁止 read/run 访问 ~/.config/codepilot/teams」。
  2. 时序竞态：Lead 查收件箱时成员计划未到 → 过早下结论「计划没提交」（事件晚于 Lead 结论）。
  3. state20 候选：独立终端后端、跨进程上下文恢复、git 合并/冲突回滚、coordinator 双锁。
  4. 跨进程 worktree 锁；Skill/Hook 共享（state17 延续）。
- 文件：`codepilot/teams/*`（14 模块）、`agent.py`（结果感知 stall + _SideStall + sanitize）、`ui/app.py`（_append_context_notice + 团队事件回灌）、`main.py`（base/lead registry 装配 + shutdown）、`commands/builtins.py`（/team）、`mcp/manager.py`（extra_registries）、`tools/registry.py`（clone）、`worktree/manager.py`（release）、15 个测试文件。
- 验收记录与可粘贴提示词清单见 `docs/state19-team/checklist.md` 底部。
- 新上手：读本文 + `docs/state19-team/{spec,plan,task,checklist}.md` + `codepilot/teams/` 即可恢复。

### state18（开发完成 + 真机验收通过）
**state18：Worktree 工作目录隔离**（`docs/state18-worktree/`）
- **状态：四文档获批后开发完成，730 测试绿（基线 584 + 146 新增），新/改文件 ruff 全清；真机场景 1–10 通过，TTL 清理自动化覆盖（2026-10-18 收口）。**
- 目标：主 Agent 与子 Agent、或两个子 Agent 并行改文件时互不覆盖。用 Git linked worktree 给每个隔离任务独立工作目录；本阶段不做合并策略、跨目录同步与多 Agent 编排。
- **架构**：新包 `codepilot/worktree/`（model/naming/settings/git/meta/initializer/manager）。隔离子 Agent 由 `subagents/isolation.py` 装配：独立 ToolRegistry（read/write/edit/glob/grep/run 重新绑定 worktree，agent/load_skill/MCP 共享）、独立 env/permission/context offload/memory；系统工具共享；**无 chdir**。
- **生命周期**：目录固定 `.codepilot/worktrees/<name>`，分支 `codepilot/<name>` 基于主工作区当前 HEAD。目录已存在时只做文件系统三检（目录/`.git` 文件/元数据）快速恢复，不调 Git；同名分支存在则挂接；进程内占用锁，同名并发任务被拒绝。无未提交改动且相对 base 无新提交 → 自动删目录+分支；有变更 → 保留并把路径/分支写进摘要。
- **关键裁决**：角色 frontmatter `isolation: worktree`；`agent` 工具新增 `worktree` 参数，缺省 `<role>-<task_id>`；名称小写分段制（≤64、≤3 段、字符集 `[a-z0-9._-]`、拒绝 `.`/`..`/保留设备名）；fail-closed，绝不回退主工作区；要求从仓库根启动。
- **环境初始化**：默认复制 `config.yaml`/`.hooks.yaml`/`codepilot-permissions.local.yaml`；软链 `.venv`/`venv`/`node_modules`；项目根 `.worktree.yaml` 覆盖 copy/symlink/include；include 只复制 `git check-ignore` 命中的文件；hooks 默认共享，必要时 per-worktree 修正。
- **管理命令**：`/worktree list`、`/worktree rm <name> [--force]`；`--force` 删目录与未提交改动但**保留分支**。清理：24h TTL，启动 + 每小时，三层过滤（路径包含 → 受管理元数据/分支前缀 → 未占用/clean/无新提交）。
- **扫描隔离**：项目指令、角色、Skill 的递归发现剪枝 `.codepilot`；主 Agent 的 glob/grep 跳过 worktree 根；显式 read 仍可访问。
- 文件：`codepilot/worktree/*`、`codepilot/subagents/isolation.py`、`subagents/{model,loader,runner,manager,tool}.py`、`ui/app.py`（前台 agent 摘要保留 Worktree 行）、`commands/builtins.py`、`main.py`（装配 + 后台清理）、`memory/resolver.py`、`skills/resolver.py`、`tools/{base,glob_tool,grep_tool}.py`、10 个测试文件。
- **真机验收记录**：场景 1–10 全过（详见 checklist 底部的「验收记录」）；场景 9 首轮因验收提示词把 `/agents` 误当角色名册导致模型撞 18 轮上限，修正提示词后通过，扫描隔离本身无缺陷。
- 已知边界：跨进程占用锁缺失；Skill/Hook 仍共享（state17 已知问题延续，Hook command 仍在进程 cwd 执行）；`manager.cancel` 仍只改状态；glob/grep 先遍历后过滤（worktree 大时有成本）；合并/跨目录同步/多 Agent 编排不在本阶段。
- 核心架构裁决已同步 `docs/decisions.md` **ADR-004**（仓内固定根 + 每 Agent 独立工具实例 + fail-closed）。
- 新上手：读本文 + `docs/state18-worktree/{spec,plan,task,checklist}.md` + `codepilot/worktree/` 即可恢复。

### state17（开发完成 + 真机验收通过）
**state17：SubAgent 系统**（`docs/state17-subagent/`）
- **状态：开发完成 + 真机验收全部通过（场景 1–6，2026-10-18 收口），584 测试绿，state17 文件 ruff 全清。**
- 目标：主 Agent 用统一 `agent` 工具把子任务委派给独立子 Agent（干净上下文/受限工具/独立权限），定义式按角色、Fork 式继承历史，结果回灌主对话，解决上下文污染。承接 state16 `subagent` Hook 动作占位。
- **架构**：新包 `codepilot/subagents/`（model/loader/runner/manager/tool + data 内置样板）。子 Agent **复用现有 Agent 类**（N6）：共享 provider/registry/hooks/fs，独立 permission/context_manager/msgs。角色 = Markdown+YAML frontmatter，四级 `[插件<内置<用户<项目]` 同名覆盖。多层防线落在 `Agent._build_tools` 新增 `tool_filter` 注入点：白名单∪系统 → 黑名单 → agent 禁（can_delegate∧depth<max_depth）→ 后台/plan 只读。深度用 contextvar；后台并发上限 4。
- **关键裁决**：独立体系 Skill 不动；结果回灌模型+系统行；执行中仅状态行+完成摘要；前台超时转后台；`/bg` 手动切；后台完成摘要流式中先入队、收尾回灌（防插进 tool_call/tool_result 破坏协议）；Fork 强制后台+继承父工具集快照。
- 文件：`codepilot/subagents/*`、`agent.py`（tool_filter + visible_tool_names）、`ui/app.py`（回灌接线 + pending 队列）、`main.py`（装配 + AgentTool 注册）、`commands/builtins.py`（/agents /bg）、`pyproject.toml`、7 个测试文件。
- 命令/快捷键：`/agents` 任务列表、`/bg <task_id>` 手动切后台；前台委派时输入框禁用，**Ctrl+B** 是 TUI 内切后台的可达入口。
- **真机验收修正（场景1）**：模型不知道有哪些角色 → 不调 agent 工具而自己用 read/glob 干活。补「角色名册」注入（system prompt volatile 块 + agent 工具 description + role enum 动态生成）。
- **真机验收修正（场景2）**：Fork 子 Agent HTTP 400（`status=failed chars=0`）——fork 发起时父 `_msgs` 末尾是 `assistant(tool_use=agent)` 且结果未回灌，直接继承会缺 `tool_result`。修复：`_fork_messages` 裁掉尾部 dangling tool_calls + trigger 并入末条 user；失败任务现在日志记 `task.error`。
- **真机验收修正（场景4/Ctrl+B）**：手动切后台功能可用，但占位工具结果让模型误判成「超时」并长篇解释。修复：占位结果明确「用户按 Ctrl+B 手动切换，非超时」+ 要求「只回复一句」；architect 示例 max_rounds 12→18（全仓审查不够）。
- **F11 观察视图增量（方案 B）**：`runner` 返回 `RunResult(summary, usage, entries)`，结束时把协议原生 msgs 扁平化为 `TranscriptEntry` 快照（Fork 只存自有部分，不持有父历史深拷贝）；`SubAgentTask.entries` 统一承载成功/失败轨迹。TUI：输入框上方提示行 + **F3** 任务列表（ListView 原生 ↑↓、Enter 打开轨迹、Esc 返回/关闭，0.5s 原地刷新保选中）；轨迹屏用 RichLog + Text 对象（markup 不注入）。后台完成摘要**直接写聊天区**（escaped），同时照旧排队进 `_msgs`。顺带修复流式结束输入框焦点丢失（Textual disabled→blur 不自动恢复）。
- 文件增量：`subagents/transcript.py`、`ui/screens/subagent_screen.py`、`tests/subagents/test_transcript.py`、`tests/test_subagent_screen.py`、`tests/test_subagent_app.py`。
- **真机修正（观察视图）**：① 完成后再看轨迹耗时持续增长（711.1s）→ 终态记录 `finished_at`，耗时固定；② 恢复会话后 Anthropic 报 tool_use 缺结果 400 → `restore._build_messages` 把同轮多个 `tool_result` 合并进同一条 user 消息（OpenAI 保持每条一个 `role=tool`）。
- 已知问题（未修，按影响排序）：
  1. **Anthropic 流式 usage 解析不全**：`protocols.py` 只在 `message_stop` 取 usage；Anthropic 兼容端点（含当前 DeepSeek `/anthropic`）把 input/cache 放在 `message_start`、output 放在 `message_delta` → token/cache 计数可能静默为 0（影响 AC4/AC5 可观测性）。
  2. **子 Agent 可 `load_skill`**：`tool_filter` 保留 `system_exempt` 工具，`load_skill` 会改共享 `SkillManager` 状态，违反 F5「运行时状态隔离」。
  3. **`manager.cancel()` 只改状态**：未真正 `task.cancel()`，后台协程继续跑，完成后可能覆盖为 done（当前 UI 未暴露取消入口）。
  4. `can_delegate` 默认 `max_depth=1` 下不生效，需配 2 才启用多级委派；插件级角色源仅预留空目录。
- 新上手：读本文 + `docs/state17-subagent/{spec,plan,task,checklist}.md` 即可恢复。

### 下一步（待用户确认，勿提前开工）
- **阶段目标：待定**（用户将在新会话指定）。
- state19 明确不做、可作为后续候选（对应「两阶段切分」的 state20）：
  1. 独立终端窗格后端 + 窗格唤醒（成员强隔离；环境优先级自动选择，不静默降级）。
  2. 成员上下文跨进程从磁盘恢复（state19 只做进程内内存续接）。
  3. Lead 收口：git 合并/冲突解决/回滚上报；coordinator 双锁（配置能力开关 + 环境变量）与写文件工具剥夺。
- state19 backlog（不阻塞验收，见 state19 区块）：
  1. Lead 行为漂移（翻文件）→ 提示硬约束。
  2. 时序竞态（事件晚于 Lead 结论）→ 待审批提示刷新。
- 既有已知问题候选（跨 state 通用）：
  1. Anthropic 流式 usage 解析（`message_start` input/cache + `message_delta` output）。
  2. 子 Agent 禁用/隔离 `load_skill`。
  3. `manager.cancel` 真正取消 asyncio task。
  4. 插件级角色源。
- 入口文件：`SHARED_PROGRESS.md` → `docs/state19-team/` → `codepilot/teams/`（装配入口 `codepilot/main.py`；工具面 `teams/tools_lead.py`/`tools_member.py`）。
- 注意事项：新功能仍走 design-first（spec→plan→task→checklist 获批再写码）；`config.yaml`/`.hooks.yaml`/`.agents` 等为 gitignore 运行时配置，勿提交；全量测试基线 **905**，`ruff check .` 既有 27 告警不得新增；验收遗留的 worktree 与 `codepilot/*` 分支已清理（2026-10-19）。

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