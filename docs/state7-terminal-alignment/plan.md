# CodePilot 终端对齐 Claude Code Plan

## 目标架构

把单文件平铺 TUI 重构为分层结构,引入主题层与状态机,实现 Claude Code 对齐特性。

```
codepilot/
├── app.py                      # → 转化为薄编排入口(或删除,逻辑迁入 ui/app.py + main.py 直连)
├── main.py                     # 入口;新增 -c/--continue、-r/--resume arg 解析
├── modes.py  (新增)            # 正交模式状态机:工具轴 + 放行策略 + accept-edits
├── session/ (新增)
│   ├── store.py                # JSONL 读写 ~/.codepilot/projects/<cwd编码>/*.jsonl
│   └── models.py               # 会话文件模型(消息+工具调用+模式快照)
└── ui/      (新增,从 app.py 拆出)
    ├── theme.tcss              # 主题 + 调色板 + 布局
    ├── palette.py              # 每工具/每模式强调色常量(供 markup 与 tcss 共享)
    ├── app.py                  # CodePilotApp 编排(原 app.py 逻辑迁入)
    ├── widgets/
    │   ├── tool_gutter.py      # F4 工具调用行(图标/强调色/进度/done/error)
    │   └── status_bar.py       # F2 底部状态栏
    └── screens/
        └── permission_screen.py# F9 权限确认页(重排版)
```

## 核心设计决策

### 模式状态机 modes.py

```python
class ToolAxis(Enum): PLAN = "plan"; BUILD = "build"      # 读 / 全工具
class ReleasePolicy(Enum): MANUAL = "manual"; AUTO = "auto"; STRICT = "strict"
# 对齐 PermissionMode: manual=DEFAULT(ASK), auto=PERMISSIVE(ALLOW), strict=STRICT(DENY)

class ModeState:                 # 单例/组合对象,由 App 持有并注入
    tool_axis: ToolAxis = BUILD
    release: ReleasePolicy = MANUAL    # 启动取 config.permissions.mode
    accept_edits: bool = False

    def cycle_tool_axis(self) -> None            # Shift+Tab 绑定
    def set_release(self, p: ReleasePolicy)      # 接通 PermissionManager.set_mode
    def toggle_accept_edits(self) -> None
    def snapshot(self) -> dict                   # 供会话 resume 序列化
    def restore(self, snap: dict) -> None
```

- 后端接线:`ModeState.set_release()` 内调用 `PermissionManager.set_mode(映射后的 PermissionMode)`;`ReleasePolicy` 与 `PermissionMode` 一映射,`modes.py` 不 import 后端 enum,由 App 做翻译(降低耦合,DIP)。
- 工具轴:`Agent.set_plan_mode(tool_axis == PLAN)` 复用现有 `list_read_only()`。
- accept-edits:由 App 在 `_execute` 前检查 —— 对 write/edit `PermissionManager.check` 结果 **auto_apply**(若 release 允许或 accept_edits on),run 始终走正常确认。

### 状态栏 status_bar.py

- 新 `StatusBar(Static)` dock 到底部,render:
  `[Plan|Build](按轴着色) · [manual|auto|strict](按策略着色) · [AE]|· 模型名 · ↑N tokens · ⏱M.Ms`
- 数据来源:注入 `ModeState` + `Usage累计器` + `ProviderConfig.name/model` + 定时器耗时。
- Header `sub_title` 精简为模型名,不再承担状态职责。

### 工具 gutter tool_gutter.py

- 把当前 ToolRow 从"每轮清空顶部竖条"改为"持久追加进滚动会话"。每工具一行:
  `{icon} {name}({params})` + 侧栏进度点 `●`(pending)→`✅`(done)/`✕`(error,加粗+提示)。
- 图标/强调色表(palette.py):read📄cyan / write✍️green / edit✏️yellow / glob🔎magenta / grep🔍cyan / run🛠️orange。
- plan 模式只出现只读三工具(read/glob/grep)。

### 就地流式

- 删除 `#stream` 独立小框;流式开始前在会话区挂一个可更新的 StreamStatic,delta 就地 `update`,done 时定格为消息行并入滚动。
- 用 Textual Worker 保持非阻塞(沿用现有 `_process_chat` worker 模式)。

### 权限确认页 permission_screen.py

- 保留 4 选项语义(本次/会话/永久/拒绝)与键盘导航(↑↓/Enter/数字/Esc)。
- 重排版:顶部显式「工具 + 命令」路径行(高亮),主体动作区,底部 hint 含 `Shift+Tab 切模式` 与放行策略提示。
- 视觉走 tcss 主题(替换硬编码 cyan heavy 边框)。

### 会话 resume

- 每条对话落盘为 JSONL,`~/.codepilot/projects/<sha1(cwd)>/<UTC时间戳>.jsonl`:
  - 光消息行:{role, content, timestamp(s), duration_s}
  - 工具调用行:{kind="tool", tool, params, output_short, ok}
  - 快照行:{kind="mode", snapshot: ModeState.snapshot()}
- `-c/--continue`:加载该 cwd 最新一条,`-r/--resume`:列出选择。
- 恢复:灌回 `ChatManager.history` + `ModeState.restore(snapshot)`,gutter 据工具行还原"跑过哪些工具"。
- 项目内 `.codepilot/session-index.json` 记录 cwd→最近会话指针(轻量,避免全量扫)。
- `.codepilot/` 加入 `.gitignore`。

## 实现顺序(依赖序)

1. **主题层 + 结构拆分**(先有地基):建 `ui/theme.tcss`、`palette.py`,拆出 `ui/app.py`/`widgets`/`screens`,旧 app.py 逻辑迁入且行为不变。
2. **modes.py 状态机**:新增三态模式,接通 `set_mode()`,加 `Shift+Tab` 与 `/mode` `/auto` 等命令面。
3. **底部状态栏 + 累计 token**:status_bar widget + Usage 累计器。
4. **就地流式 + 持久 gutter**:替换 #stream,工具行持久化。
5. **权限确认页对齐**:重排版权限屏。
6. **会话 resume**:session/store 与 -c/-r。
7. **收尾**:删 verify_strict.py、收紧 settings.local.json、全量测试、分阶段提交。

## 风险与对策

- 单文件拆分会动核心交互路径 → 阶段 1 先纯结构迁移、行为不变,测试守护后再加特性。
- 正交模式易与既有 plan/do 混淆 → modes.py 统一持有、状态栏唯一显示源,`/plan` `/do` 保留为工具轴别名。
- 就地流式在 RichLog 上原地 update 的滚动位置 → 用独立 StreamStatic 承接,避免 RichLog 每 delta roll 到底。