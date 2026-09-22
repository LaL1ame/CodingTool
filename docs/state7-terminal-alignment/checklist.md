# CodePilot 终端对齐 Claude Code 验收 Checklist

## 手动验收(TUI 端到端)

- [ ] 启动:底部状态栏出现,显示 模型 + 累计 token(0)+ 耗时 + `plan/build` 徽标 + `manual/auto/strict` + `[AE]`。
- [ ] SSHift+Tab:plan ⇄ build 切换,状态栏着色变化;plan 下工具 gutter 只见 read/glob/grep,模型回答只读。
- [ ] `/mode auto` → 未命中规则操作(auto run)自动放行、不弹框,状态栏变 auto(橙)。
- [ ] `/manual` 恢复询问;`/strict` 未命中即拒绝(返回权限拒绝错误、Agent 继续)。
- [ ] `/edits on` → edit/write 自动应用、run 仍问;`/edits off` → edit/write 也问。
- [ ] 工具调用作为 gutter 行跨轮保留;完成 ✅,失败 ✕ 加粗 + 提示。
- [ ] 流式输出就地出现在会话区,光标处滚出,无独立 4 行小框。
- [ ] 触发一次权限 ask → 权限屏展示工具路径+命令 → Shift+Tab 提示可见 → 选「永久放行」→ 后续相同调用自动放行。
- [ ] `python codepilot -c` 续最近会话;`-r` 列表选择 → 恢复消息 + 模式快照 + 工具历史。
- [ ] `~/.codepilot/projects/<cwd编码>/` 出现会话 JSONL;`.codepilot/` 未污染 git(git status 干净)。

## 自动化测试

- [x] `pytest tests/ -q` 全量绿(170)。
- [x] modes.py:工具轴循环、release→PermissionMode 映射、snapshot/restore 往返一致。
- [x] session/store:JSONL 写入→读取往返、cwd 编码稳定、continue 取最新、resume 列表、坏行容错。
- [x] accept-edits:write/edit 自动放行(标 auto)、run 仍问、off 时照常。
- [ ] status_bar:累计 token 单调递增不随单轮覆盖(逻辑在 App,T3 已实现待人工确认)。
- [x] 纯结构迁移阶段旧测试无回归。

## 提交规范样例(Conventional Commits)

- `refactor(ui): 拆分 app.py 为 ui/ 分层 + 引入 theme.tcss 主题`
- `feat(modes): 正交模式状态机(plan/build × manual/auto/strict × accept-edits)`
- `feat(ui): 底部状态栏 + 累计 token 计数`
- `feat(ui): 就地流式 + 持久工具调用 gutter`
- `refactor(ui): 权限确认页对齐 Claude Code 排版`
- `feat(session): 会话 JSONL 历史 + -c/-r 续接`
- `chore: 删除遗留 verify_strict.py, 收紧 settings.local.json`