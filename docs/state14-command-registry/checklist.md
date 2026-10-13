# state14 命令注册与分发机制 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [x] 注册中心 `register` 拒绝重复名称/别名，抛 `CommandConflictError` 且报错信息含撞的 token 与双方命令名（验证：`tests/test_commands_registry.py`——两次 register 同名、register 撞已有 alias）。
- [x] `assert_no_conflicts` 全量两两校验，能检出非直接注册撞口（别名互撞/同名别名错位）（验证：构造两个 spec 别名互撞 → 调用抛错）。
- [x] `resolve` 小写不敏感，名称与别名等价命中（验证：`/CLEAR` 命中 clear、`/?` 命中 help、未命中返回 None）。
- [x] `parse_command` 四态正确：空输入→blank、非斜杠→message、斜杠命中→command、斜杠未命中或裸 `/`→unknown（验证：`tests/test_commands_parser.py`）。
- [x] 三态命令类型齐全：LOCAL（/help/status/session/exit/memory）、UI（/clear/plan/do/mode/edits/compact/continue/forget/permission/auto|manual|strict）、PROMPT（/review）（验证：`build_registry().all()` 逐条取 kind 断言）。
- [x] 补全候选来源于注册表 name+alias、排除 hidden、去重、排序（验证：`tests/test_commands_completer.py`——`/p` 只含 plan/permission、隐藏命令不出现）。

## 集成

- [x] `_submit` 分流器四态：command 走本地分发、message 走 `_start_ai`、unknown 显示 `/help` 引导且**不送 AI**、blank 早返回（验证：`tests/test_commands_dispatcher.py` + 真机观察）。
- [x] handler 返回 str → 转发给 AI；返回 None → 本地处理完；协程 handler 返回 awaitable（验证：`tests/test_commands_dispatcher.py` 三分支断言）。
- [x] `plan/do 带消息` 正确切轴（PLAN/BUILD）并落回对话（验证：`tests/test_commands_builtins.py` 假 ctx 断言 set_axis 被调且返回 params）。
- [x] `/review` 返回 `REVIEW_PROMPT`，交由分发器转发 AI（验证：builtins 单测断言返回值 == REVIEW_PROMPT）。
- [x] `CommandContextImpl` 实现全部 12 个协议方法，命令不 import App/Textual（验证：`tests/test_commands_context.py`——假 ctx 可驱动全部命令，换实现即复用）。
- [x] 状态栏模式标记：build→`DEFAULT`、plan→`PLAN`（验证：`_refresh_status` 传入 StatusBar 的 axis_label；真机 `/plan` 后看状态栏）。

## 验证与测试

- [x] 项目无语法错误、模块可导入（验证：`python -c "import codepilot.commands"` + `python -c "import codepilot.ui.app"`）。
- [x] 无遗留旧 `parse_command` 签名调用（验证：`grep -rn "from codepilot.commands import\\|commands.py" codepilot/ tests/` 无 `commands.py` 引用）。
- [x] 全量单测通过（验证：`pytest -q` —— 既有 308 + state14 新增全绿，实测 338 passed）。
- [x] lint 无回归（验证：`ruff check .` —— state14 全部文件无告警；仓库 30 处为既有基线，非本次引入）。

## 端到端场景（阶段六真机 / TUI）

- [x] 场景 1（/help 引导）：TUI 输入 `/help` → 按 LOCAL/UI/PROMPT 分组列出命令名+用法（观察输出）。
- [x] 场景 2（模式切换联动标记）：真机确认——`/plan`→`[PLAN]`、`/do`→`[DEFAULT]`；`/plan` 计划模式只读（无 write/edit）、`/do` 恢复全工具（③ 工具轴门槛）。
- [x] 场景 3（带消息转发）：真机确认——`/do 消息` 先切 `[DEFAULT]` 再进对话、消息发给 AI（③）。
- [x] 场景 4（/clear 清屏）：真机确认——清屏后问"刚才记住的约定"能答出（② 上下文仍留）。
- [x] 场景 5（Tab 补全）：真机确认——`/` 弹菜单（ListView）、`/p` 候选；修复后无崩溃。
- [x] 场景 6（未知命令引导）：真机实测——`/notacmd` → `/help` 引导、不送 AI。
- [x] 场景 7（/status /permission /session）：真机实测——三个查询命令各显示对应状态/权限/会话信息。
- [x] 场景 8（/review 送 AI）：真机确认——`/review` 不进对话但进入流式、模型就未提交改动给审查意见（① PROMPT 转发）。
- [x] 场景 9（/memory /forget 迁移）：真机实测——`/memory` 分组显示、`/forget` 报删除数，与 state13 一致无回归。
- [x] 场景 10（/exit、/mode、/edits、/compact、/continue 迁移）：真机实测——各命令迁移后行为与 state 前一致（含 /continue 续接 limit 暂停）。

## 验收口径

- [x] spec AC1–AC7 全部有对应可观测条目（AC1↔registry、AC2↔parser、AC3↔builtins 类型、AC4↔context 假实现、AC5↔分流器、AC6↔completer/Tab、AC7↔全集解析+标记）。