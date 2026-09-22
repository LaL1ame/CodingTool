# CodePilot 终端对齐 Claude Code Spec

## 背景

当前 Codepilot 的 TUI 是一个 361 行单文件 `codepilot/app.py`:无主题层(内联 markup)、工具调用是顶部一条每轮清空的 `ToolRow` 竖条、状态塞在顶部 Header `sub_title`、流式文本落在一个独立 4 行 `#stream` 小框、`PermissionManager.set_mode()` 是死代码、token 只记单轮不累计、无会话历史与 resume。

用户希望把这套终端体验对齐 Claude Code 的交互范式:左栏工具进度、底部状态栏、就地流式、统一的模式语言、脚本化的权限确认、`-c/-r` 会话续接。**对齐的是交互范式与视觉签名,保留 Codepilot 自身产品语义**(中文文案、4 选项权限语义、6 工具名、自身防御体系)。

## 目标

- 交互范式对齐:左 gutter 工具进度、底部状态栏、就地流式、报错块。
- 模式语言对齐:采用 Claude Code 的**正交模式模型**,而非单态循环。
- 模式可运行时切换:`set_mode()` 从死代码接通为会话内可用。
- 状态透明:底部状态栏展示模式/工具/放行策略/模型/累计 token/耗时。
- 会话续接:`-c/--continue`、`-r/--resume` 找回历史对话。
- 代码结构可维护:按低耦合拆层 + tcss 主题。

## 正交模式模型(核心)

Claude Code 的「模式」是**多个正交开关**的组合,不是一条循环。对齐后 Codepilot 的语言如下:

| 开关 | 取值 | Claude 语义 | CodePilot 已有机制 |
|---|---|---|---|
| **工具轴 Tool axis** | `plan` / `build` | plan=只读, Shift+Tab 切换 | `Agent._plan_mode` + `registry.list_read_only()` |
| **放行策略 Release policy** | `manual` / `auto` / `strict` | 未命中规则的兜底 | `PermissionMode.DEFAULT/PERMISSIVE/STRICT`(`fallback_for()`) |
| **自动编辑 Accept Edits** | `on` / `off` | 是否自动应用 edit/write,**仅文件编辑** | 为 edit/write 注入会话/规则放行 |

要点(修正早期误解):
- **Accept Edits 与 Auto 模式是两回事**:accept-edits 开 ≠ run 命令也自动放行;auto 开 ≠ edit 一定自动应用。两者互相独立,由两条开关分别控制。
- Shift+Tab 只在 **Plan ⇄ Build** 之间切换(Claude Code 实际行为),不是单态循环。
- `Agent._plan_mode`(工具轴)与 `PermissionManager.mode`(放行策略)仍然是**两个独立状态**,由 modes.py 统一持有并向外暴露,不再混为一谈。

## 功能需求

- F1(UI 主题):集中式 `.tcss` 主题 + 调色板常量,Claude 风格深底,每工具一个强调色;移除散落内联 markup。
- F2(底部状态栏):新增 status_bar,展示 `[Plan|Build](着色) · [manual|auto|strict] 徽标 · [AE] 标记 · 模型 · 累计 token · 耗时`;Header 精简。
- F3(累计 token):修掉仅单轮显示的问题,累计会话总 token(字符串存,无累计上限);不显示 cost(无定价模型)。
- F4(信号 gutter):工具调用追加进滚动会话、跨轮保留;每工具按图标/强调色渲染,带进度点与 done/error 态。
- F5(就地流式):助理文本就地流式进会话区,替换独立 `#stream` 小框。
- F6(报错块):工具错误/系统错误加粗 + 简短提示,替代平铺 `✕`。
- F7(模式状态机):`modes.py` 持有 工具轴 + 放行策略 + accept-edits 三态,供 TUI 与后端共用;接通 `set_mode()`。
- F8(切换命令面):`Shift+Tab`=Plan⇄Build;`/mode manual|auto|strict`;`/auto` `/manual` `/strict` 快捷别名;`edits on|off` 或键位切自动编辑;保留 `/plan` `/do`。
- F9(权限确认页对齐):保留 4 语义选项(本次/会话/永久/拒绝),重排版为 Claude Code 式:顶部工具路径+命令、高亮动作区、底部快捷键提示(含 Shift+Tab 模式入口)。
- F10(自动放行呈现):默认打开,自动放行的工具**正常进 gutter、不弹权限框**,开关在状态栏持续可见。
- F11(会话 resume):会话 JSONL 存用户级 `~/.codepilot/projects/<编码cwd>/*.jsonl`;`-c/--continue` 续最近、`-r/--resume` 列表选择并恢复消息历史 + 模式快照。
- F12(项目级目录):项目内 `.codepilot/` 放轻量 index + settings,历史不落此、不入 git。

## 非功能需求

- N1:模式切换与权限确认**不阻塞事件消息泵**(异步 worker 现有模式)。
- N2:自动放行的判定延迟可忽略,复用现有权限判定,不新增策略引擎。
- N3:兼容现有 6 工具与 5 层防御判定,不改变权限后端语义。
- N4:会话历史与 resume 不引入第三方存储,用 JSONL 自研。
- N5:模块低耦合:TUI 组件间只经 modes.py/回调接口通信,不互相操作内部实现。

## 不做的事(边界)

- 不逐像素复刻英文标签与精确排版语言(pixel-level clone 排除,见 Q1)。
- 不实现 cost 显示(无定价模型)。
- 不改变 5 层权限判定的后端逻辑(仅消费其 Verdict)。
- 不加网络/web 工具(不在本 state 范围)。
- 不做多会话加密 / 审计日志 / 云同步。

## 验收标准

- AC1:`Shift+Tab` 在 plan ⇄ build 间切换,状态栏着色随之变化;plan 下模型只见 read/glob/grep。
- AC2:`/mode auto` 后未命中规则的操作自动放行且不弹框;`/manual` 恢复询问;`/strict` 未命中即拒。
- AC3:accept-edits on 时 edit/write 自动应用、run 仍问;off 时 edit/write 也问。
- AC4:底部状态栏正确显示 模式/策略/[AE]/模型/累计 token/耗时,累计 token 随会话增长。
- AC5:工具调用作为 gutter 行跨轮保留在滚动会话中,完成后变 done 态(绿),失败变 error 态(红)。
- AC6:流式输出就地出现在会话区,不依赖独立小框。
- AC7:自动放行的工具出现在 gutter 但不弹权限屏,开关在状态栏可见。
- AC8:`-c` 续最近会话;`-r` 列出历史、选一条后恢复消息 + 模式快照(plan/manual/AE)。
- AC9:会话 JSONL 落在 `~/.codepilot/projects/<cwd编码>/`,重克隆/换目录不丢;`.codepilot/` 不污染 git。
- AC10:全量单测通过 + 新增 modes.py 状态机、累计 token、会话序列化单测覆盖。
- AC11:分阶段 Conventional Commits;每阶段可独立验收。