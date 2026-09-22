# CodePilot 终端对齐 Claude Code Task

## T1 主题层 + 结构拆分(地基)

- [ ] 新建 `codepilot/ui/theme.tcss`:Claude 风格深色主题、每工具强调色、布局 dock。
- [ ] 新建 `codepilot/ui/palette.py`:TOOL_ICONS / TOOL_COLORS / MODE_COLORS(plan紫 manual蓝 auto橙 strict红 AE绿)常量。
- [ ] 拆出 `codepilot/ui/widgets/tool_gutter.py`(ToolRow + 持久追加)、`status_bar.py`、`screens/permission_screen.py`。
- [ ] 新建 `codepilot/ui/app.py`:原 app.py 编排逻辑迁入;`codepilot/main.py` 直连 `ui/app.CodePilotApp`。
- [ ] 纯结构迁移,**行为与现有测试等价**(全量单测绿)。

## T2 modes.py 正交状态机 + 切换命令面

- [ ] 新建 `codepilot/modes.py`:ToolAxis / ReleasePolicy / accept_edits + ModeState(cycle_tool_axis/set_release/toggle_accept_edits/snapshot/restore)。
- [ ] App 注入 ModeState;set_release 接通 `PermissionManager.set_mode`(`ReleasePolicy`↔`PermissionMode` 由 App 翻译)。
- [ ] `Shift+Tab` 绑定 cycle_tool_axis;`/mode` `/auto` `/manual` `/strict` `/edits on|off`;保留 `/plan` `/do` 为工具轴别名。
- [ ] mode 启动初值取 `config.permissions.mode`。
- [ ] 单测:modes.py 状态转换、snapshot/restore、release→PermissionMode 映射。

## T3 底部状态栏 + 累计 token

- [ ] status_bar dock 底部:Plan/Build(着色)· policy 徽标 · [AE] · 模型 · ↑N tokens · 耗时。
- [ ] Usage 累计器:会话级累计 input+output,修掉单轮覆盖问题。
- [ ] Header sub_title 精简为模型名。
- [ ] 单测/端到端:累计 token 随会话增长。

## T4 就地流式 + 持久工具 gutter

- [ ] 删 `#stream` 独立框;流式挂可更新 StreamStatic,delta 就地 update,done 定格成消息行。
- [ ] gutter 持久化:工具行追加进滚动会话跨轮保留,`●→✅/✕`,错误加粗 + 提示。
- [ ] plan 模式 gutter 只现只读工具。
- [ ] 端到端:流式就地出现、工具行不消失、错误可读。

## T5 权限确认页对齐

- [ ] permission_screen 重排版:工具路径+命令高亮、高亮动作区、底部 hint 含 Shift+Tab 模式入口。
- [ ] 走 tcss 主题(替换硬编码 heavy cyan 边框)。
- [ ] 保留 4 语义选项与键盘导航;确认 dismiss 返回 HumanChoice 不变。
- [ ] 端到端:触发 ask → 选一项 → 正确放行/拒绝。

## T6 会话 resume

- [ ] `codepilot/session/models.py`:会话文件模型(消息行/工具行/模式快照)。
- [ ] `codepilot/session/store.py`:JSONL 写读 `~/.codepilot/projects/<sha1(cwd)>/*.jsonl` + `.codepilot/session-index.json` 索引。
- [ ] `main.py` 加 `-c/--continue`、`-r/--resume` arg 解析。
- [ ] resume 恢复:灌回 ChatManager.history + ModeState.restore(snapshot) + gutter 还原。
- [ ] `.codepilot/` 加入 `.gitignore`。
- [ ] 单测:序列化/反序列化、cwd 编码唯一性、continue/resume 路径。

## T7 收尾 + 分阶段提交

- [ ] 删 `verify_strict.py`、收紧 `.claude/settings.local.json` 冗余放行。
- [ ] 全量 pytest 绿;补 T2-T6 新增单测。
- [ ] 逐阶段 Conventional Commits(feat/fix/test/docs),每提交讲解 why/how/naming。
- [ ] 更新 docs(state7 进度)、project-overview 记忆。