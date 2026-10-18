# Decisions(架构决策记录 · ADR)

项目里每一次"为什么这么设计"的关键结论,按时间倒序记录在此。
每条记录格式遵循轻量 ADR:上下文 → 实证 → 决策 → 后果。
**文档永远与代码保持一致**:代码改了,这里同步。

---

## ADR-004:Worktree 隔离采用「仓内固定根 + 每 Agent 独立工具实例 + fail-closed」(2026-10-18,state18)

### 背景
state17 的子 Agent 复用主工作区与共享 registry：主 Agent 与子 Agent、或两个并行子 Agent
同时改文件会互相覆盖。需要在同一 Git 仓库内提供文件系统级隔离，同时保持现有工具接口与
无 `chdir` 的架构。

### 实证
- `git worktree add` 可在被 gitignore 的 `.codepilot/worktrees/` 内创建并正常登记（真机探针验证）。
- 目录已存在时只读文件系统三检（目录、`.git` 文件、元数据）即可安全复用，不需要调用 Git。
- 真机场景 1–10 通过：并行 A/B 不覆盖、dirty 保留、clean 自动清理、同名拒绝、扫描隔离均成立。
- 场景 9 首轮失败是验收提示词把 `/agents`（任务列表）误当角色名册，导致模型撞轮次上限；
  扫描隔离探针本身全部通过。

### 决策
1. **固定仓内根**：`.codepilot/worktrees/<name>`（已 gitignore），分支 `codepilot/<name>`
   基于主工作区当前 HEAD；元数据独立存 `_meta/<sha1(name)>.json`，不写入工作树。
2. **每 Agent 独立运行时**：文件/命令工具重新实例化并绑定 worktree，系统工具（agent/
   load_skill/MCP）共享；env、项目指令、记忆、context offload、permission 全部按
   worktree 绝对路径重建；不使用 `chdir`。
3. **触发与命名**：角色 frontmatter `isolation: worktree`；`agent` 工具可传 `worktree`，
   缺省 `<role>-<task_id>`；名称小写分段制并通过 resolve 包含校验。
4. **生命周期**：无未提交改动且相对 base 无新提交 → 自动删目录+分支；有变更 → 保留并报告；
   `/worktree rm --force` 只删目录、分支保留；24h TTL + 启动/每小时清理，三层安全过滤。
5. **失败策略**：创建、初始化、名称、占用任一失败均 fail-closed，绝不回退主工作区。
6. **扫描隔离**：指令/角色/Skill 的递归发现剪枝 `.codepilot`；主 Agent 的 glob/grep
   跳过 worktree 根；显式 read 仍可访问。

### 后果
- 并行子 Agent 写文件互不覆盖；主会话不会被 worktree 内的规则/角色/Skill 污染。
- 合并策略、跨目录同步、多 Agent 编排明确不做，留给后续阶段。
- 已知边界：进程内锁不跨进程；Skill/Hook 仍共享；`manager.cancel` 仍只改状态；
  glob/grep 先遍历后过滤；要求从仓库根启动。
- 详细设计与验收见 `docs/state18-worktree/`。

---

## ADR-003:「每次 edit 后自动格式化」Hook 模式与 AI 助手心智冲突(2026-10-16,state16 真机验收发现)

### 背景
state16 验收场景 1(写后自动格式化)的真机测试:`.hooks.yaml` 配 `tool_post + write/edit + command "ruff format {file_path}" async`。模型对 `model.py` 写入 `foo=1`(无空格)后,Hook 异步把整文件按 ruff 风格重排(`foo = 1`、EVENTS 拆行、注释对齐等,26 行 diff)。模型读回文件发现字节与所写不符,**归因成「edit 工具层规范化 / 权限层拦截」**,烧了约 15 轮试图绕开,最终也没达成「保留 foo=1」。

### 实证
- Hook 引擎行为完全符合 spec:命令执行、异步、notify、故障隔离都正确;每次 edit 后 ruff 确实执行了(日志 `codepilot.hooks ... ok`)。
- 问题不在引擎,在**「AI 助手每改一次文件就被后台格式化」这个模式本身**:
  1. **模型没有反馈通道**——不知道 Hook 改动了文件,只能靠 read 看到字节变化,产生错误归因。
  2. **对抗有意写入**——测试者故意写 `foo=1`,Hook 改回 `foo = 1`,形成拉锯。
  3. **异步时序不可预期**——format 在不确定时机落地,与模型自己的 read 竞态。
  4. **风格噪音**——ruff 默认风格 ≠ 项目手写风格(紧凑元组),一次 edit 引入 26 行无关 diff。
- 附带观察:应用内模型对自己代码库运行时行为判断不可靠(本阶段两次误诊),验收须以日志/实证为准。

### 决策
- **不修改 Hook 引擎**——通用机制是对的,「拦截/注入/命令」三类动作语义不变。
- **「每次 edit 后自动格式化」是反模式(至少对 AI 助手语境)**:若要做格式化自动化,更合理时机是 **stop/round_end**(一轮干完再格式)或**显式命令**,而非在模型正在写文件时抢跑。
- 文档沉淀为示例反模式,写入 state16 验收记录与 ADR,供未来 skill/hook 样板设计参考。

### 后果
- `.hooks.yaml` 已 gitignore(运行时配置,同 `config.yaml`)。
- 验收场景 1 的「自动格式化」用例**降级为设计讨论**,不再作为 state16 必须达成的行为;该功能本身未被破坏,只是不建议以「每次 edit」方式使用。
- 未来若提供内置格式化 Hook 样板,默认挂 `stop` 事件而非 `tool_post`。

---

## ADR-002:Skill 命令撞名时 Skill 胜出并统一入口;`/review` 由 PROMPT 命令移交内置 review Skill(2026-10-14)

### 背景
state15（Skill 系统）让「加载/激活的 Skill 自动注册成斜杠短命令」。内置三样板含 `review`,其命令名与 state14 既有 `/review`(PROMPT 命令:把一段写死的审查提示词送 AI)撞名。

### 决策
- Skill 命令通过 `CommandRegistry.register(spec, replace=True)` 注册:撞名时**移除旧 spec、登记新版**,Skill 版本胜出。
- state14 的 `/review` PROMPT 命令**移除**,其预设文案并入内置 `review` Skill 正文(SOP 是严格超集:白名单+双模式+占位符);`REVIEW_PROMPT` 常量保留仅向后兼容。

### 理由
- 避免「两个半重叠的 review 概念」(旧 `/review` 与新 `/skill-review` 并存且行为微妙不同)加剧混乱。
- Skill 系统正是「预设提示词送 AI」这类散件的统一归宿——同类操作收敛到一个入口、一份代码,符合「重构优先于修补」。
- `replace=True` 使热更新与动态覆盖无损,`assert_no_conflicts` 启动严格校验语义不变(仅动态路径用 replace)。

### 后果
- 用户肌肉记忆的 `/review` 仍在,但行为升级为独立对话审查(不再把提示词注入当前对话)。
- 用户/项目级 `.skills` 若出现与核心命令同名的 Skill,将以 Skill 版本覆盖——需提醒避免用 `status/help/exit` 等核心名命名。

---

## ADR-001:放行模式循环用 `F2`,不用 `Shift+Tab` 或 `Ctrl+标点`(2026-09-28)

### 背景与需求
用户希望像 Claude Code 一样,用快捷键循环切换 **放行模式**(`manual / auto / strict`,并同步 `accept_edits`),而不是工具轴(Plan/Build)。最初以为 `Shift+Tab` 可行(Claude Code 同款),但实测不生效。

### 实证(全部来自按键日志 `on_key` 捕获,非猜测)
1. **`Tab` / `Shift+Tab` 是 Textual 的焦点导航键**:
   日志中按 `tab`,焦点在 `Input ⇄ RichLog` 之间跳转。文本控件(或焦点系统)在 App 层绑定**之前**消费该键,因此把 `shift+tab` 裸绑到 `App.BINDINGS` 永远不会触发 action。
2. **`Ctrl + 标点` 在本机(Windows 终端 + 中文输入法)被吞**:
   按下 `Ctrl+\``、`Ctrl+.`、`Ctrl+,`、`Ctrl+;`,日志无一例外都是 `key='ctrl+@'`(NUL)。非字母的 Ctrl 组合被终端统一改写为 NUL,不可作为绑定键。
3. **`ctrl+k` / `ctrl+n` / `f2` 原样到达**:
   三者在日志中均干净出现。

### 决策
- 绑定 **`F2` → `action_cycle_release`**(而非 `Shift+Tab` 或 `Ctrl+.`)。
- 新增 `ModeState.cycle_release()`,按 `RELEASE_CYCLE` 循环:
  ```
  MANUAL+AE off → AUTO+AE on → STRICT+AE off → (回到 MANUAL)
  ```
  每按一次,**放行策略与 accept_edits 一起**演进(对齐 Claude Code 的"整档模式"心智)。
- 保留 `/auto` `/manual` `/strict` 斜杠命令,它们只切放行策略;`F2` 循环是**多轴一起动**的快捷通道。

### 选择 `f2` 而非另两个干净键的原因
- `ctrl+k` 在 Textual `Input` 里可能被当作"删到行尾"等文本编辑快捷键,有冲突风险;
- `ctrl+n` 可能被当作"下一行/下一个";
- `f2` 是功能键,按 ESC 序列传输,不涉终端改写、不参与焦点导航、与文本输入无冲突。

### 后果与注意
- 快捷键手感**不同于真 Claude Code 的 `Shift+Tab`**,这是 Windows 终端 + 中文输入法环境下的最优解,非完全等价。
- 若日后要彻底对齐 `Shift+Tab`:需在 Input 层拦截该键并屏蔽焦点导航(`prevent_default` + `stop`),实现复杂、升级 Textual 有回归风险,暂不采用。
- `ToolAxis`(Plan/Build)的切换不再绑快捷键,改由 `/plan` `/do` 显式命令完成(`cycle_tool_axis` 已不再是键盘入口)。

### 涉及文件
- `codepilot/modes.py` — 新增 `RELEASE_CYCLE`、`ModeState.cycle_release()`
- `codepilot/ui/app.py` — 绑定 `f2` → `action_cycle_release`
- `codepilot/ui/screens/permission_screen.py` — 提示文案同步