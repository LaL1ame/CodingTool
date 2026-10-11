# Decisions(架构决策记录 · ADR)

项目里每一次"为什么这么设计"的关键结论,按时间倒序记录在此。
每条记录格式遵循轻量 ADR:上下文 → 实证 → 决策 → 后果。
**文档永远与代码保持一致**:代码改了,这里同步。

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