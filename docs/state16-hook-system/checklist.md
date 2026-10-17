# state16 Hook 系统 Checklist

> 每一项通过运行代码或观察行为验证，聚焦系统行为。

## 实现完整性

- [x] 共享匹配器四语法生效：精确、`!`反向、`/regex/`正则、glob 各自命中/不命中正确；`!` 可包裹其余三种（验证：`tests/hooks/test_match.py`）。
- [x] 权限既有规则行为不变：无 `!`/`/.../` 形态的 pattern 走原路径（验证：`test_match.py` 回归用例 + `tests/test_permissions_policy.py` 全绿）。
- [x] 规则模型完整（HookRule/Condition/CondItem/HookAction，frozen，8 事件常量）（验证：`tests/hooks/test_loader.py` 解析断言）。
- [x] 两级加载：用户级前、项目级后合并；文件缺失/空忽略（验证：`test_loader.py` 两级用例）。
- [x] F10 集中校验全集逐项报错：未知事件/动作、all/any 混用、条件空、正则非法、command 缺 cmd、http 缺 url、prompt 缺 text、tool_pre 配 async、非 tool 事件条件字段不存在（验证：`test_loader.py` 校验用例逐条触发 HookError）。
- [x] 四动作执行器：command 捕获退出码/stdout/stderr/超时并传 env；http 按方法/headers/body 发请求；prompt 落 injector；subagent 记日志跳过（验证：`tests/hooks/test_actions.py`）。

## 集成

- [x] 条件语义：all 全满足才触发、any 任一满足即触发、省略 if 无条件触发（验证：`tests/hooks/test_engine.py`）。
- [x] 执行控制：once 会话内二次跳过；async 不阻塞主流程且完成有 notify；timeout 到期按失败处理；多命中按声明顺序执行（验证：`test_engine.py`）。
- [x] prompt 注入：内容出现在 volatile 尾块（env 后、plan/memory 前）、每轮重建可见、同 key 去重、`clear()` 复位（验证：`tests/hooks/test_injection.py`）。
- [x] tool_pre 拦截链路：非零退出 → 工具未执行、`Hook 拦截: <stderr>` 作为工具结果回灌；零退出 → 正常执行；hook 命令异常/超时 → 记日志放行；权限拒绝的工具不触发 tool_pre（验证：`tests/hooks/test_intercept.py`）。
- [x] 发射点齐全：round_start 每轮、stop 四出口（done/limit/stall/unknown_tool）、user_message 非 resume 时触发（验证：`test_intercept.py` 发射断言）。
- [x] Hook 失败不中断主流程：动作抛异常时 agent 循环继续（验证：`test_engine.py`/`test_intercept.py` 故障注入用例）。
- [x] 零 Hook 短路：不配 `.hooks.yaml` 时 agent/builder/chat 行为与现状一致（验证：`tests/test_agent*.py`、`tests/test_prompt*.py`、`tests/test_chat.py` 全绿）。

## 验证与测试

- [x] 项目无语法错误、模块可导入（验证：`python -c "import codepilot.hooks, codepilot.ui.app, codepilot.main"`）。
- [x] 全量单测通过（验证：`pytest -q` —— 既有 391 + state16 新增全绿，收口时 482 全绿）。
- [x] lint 无回归（验证：`ruff check codepilot/hooks tests/hooks` 零告警；`ruff check .` 仅既有基线）。

## 端到端场景（阶段六真机 / TUI）

- [x] 场景 1（写后自动格式化）—— **降级为设计讨论（ADR-003）**：真机验证暴露「每次 edit 后自动格式化」是 AI 助手语境反模式（模型无反馈通道、对抗有意写入、异步竞态、风格噪音）。Hook 引擎行为本身正确（每次 edit 确实执行了 ruff），模式不可取，不做为必须达成的行为。
- [x] 场景 2（可编程拦截）—— **真机通过**：`run touch` 被拦两次（日志 `拦截: 测试拦截`），模型读配置后改用 `write` 工具绕开创建空文件，完整演示「被拦截→调整策略」。
- [x] 场景 3（会话注入 + /clear）—— **真机通过 + headless 铁证**：session_start 注入生效（回答带「—— Hook 注入」）；`/clear` 后 headless 端到端证明**下一轮 system prompt 无注入块**、injector 清空。真机第二次回答仍带后缀系**模型仿写历史**的提示词伪影，非 Hook bug（单测 + headless 双重证明复位链路正确）。
- [x] 场景 4（异步 + 超时）—— **真机通过**（日志自证）：run 命令 00:28:09 执行、主流程 00:28:10 即进下一轮（**不阻塞**）；后台 Hook `sleep(3)` 被 1s 超时截断（`命令超时 (1s)` + 进程树清理），故障隔离正常。唯一小项：`[hook] failed` TUI 系统行为暗灰小字，可见性偏弱（非必改）。
- [x] 场景 5（fail-fast）—— **真机通过**：非法配置（tool_pre + async）启动即 stderr 报 `Hook configuration error: ... 禁止 async（拦截必须同步出结果）` 并退出，不进入 TUI。
- [x] 场景 6（权限规则升级）—— **真机通过 + 验收发现并修复 bug**：`!` 反向（run 非 git → 规则拒绝）、正则（read `.py` → 规则拒绝）均生效。**验收暴露 `parse_rule` 冒号定界 bug**（`partition(":")` 截断含冒号正则 `/^C:/` → 权限文件启动即解析崩溃），已修为「取冒号后为合法结果的最后一个冒号」，新增 4 个回归测试。另发现 `read(/^C:/)` 会被 state6 路径沙箱（工作区外硬 deny）抢先拦截、无法干净演示正则规则，故演示改用工作区内 `.py` 正则。

## 验收口径

- [x] spec AC1–AC10 均有对应可观测条目（AC1↔两级加载+缺文件 · AC2↔发射点齐全+场景3 · AC3↔四语法+权限回归 · AC4↔拦截链路+场景2 · AC5↔注入+场景3 · AC6↔http+场景4 · AC7↔subagent 占位 · AC8↔once/async/timeout+场景4 · AC9↔F10 校验+场景5 · AC10↔零 Hook 短路+全量回归）。
- [x] 人工真机清单（阶段五交付后对照）逐项登记实际结果，与预期不符如实记录为差异（见「端到端场景」各条实际结论；差异如实记录：场景1 降级 ADR-003、场景3 仿写伪影、场景6 parse_rule bug）。

## 验收发现汇总（收口记录）

| # | 发现 | 处置 |
|---|------|------|
| 1 | 「每次 edit 后自动格式化」是 AI 助手语境反模式（场景1） | 记 ADR-003；不做为必须行为；未来格式化 hook 默认挂 stop 事件 |
| 2 | `parse_rule` 冒号定界 bug：pattern 内含冒号（正则/URL）导致解析崩溃（场景6） | 已修复 + 4 回归测试（commit `8092db7`） |
| 3 | `read(/^C:/)` 被路径沙箱抢先拦截，无法演示正则规则（场景6） | 演示改用工作区内正则；沙箱优先是正确设计 |
| 4 | `[hook] failed` TUI 系统行暗灰小字、可见性弱（场景4） | 记录为可选 UX 优化，未改 |
