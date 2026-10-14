# state16 Hook 系统 Checklist

> 每一项通过运行代码或观察行为验证，聚焦系统行为。

## 实现完整性

- [ ] 共享匹配器四语法生效：精确、`!`反向、`/regex/`正则、glob 各自命中/不命中正确；`!` 可包裹其余三种（验证：`tests/hooks/test_match.py`）。
- [ ] 权限既有规则行为不变：无 `!`/`/.../` 形态的 pattern 走原路径（验证：`test_match.py` 回归用例 + `tests/test_permissions_policy.py` 全绿）。
- [ ] 规则模型完整（HookRule/Condition/CondItem/HookAction，frozen，8 事件常量）（验证：`tests/hooks/test_loader.py` 解析断言）。
- [ ] 两级加载：用户级前、项目级后合并；文件缺失/空忽略（验证：`test_loader.py` 两级用例）。
- [ ] F10 集中校验全集逐项报错：未知事件/动作、all/any 混用、条件空、正则非法、command 缺 cmd、http 缺 url、prompt 缺 text、tool_pre 配 async、非 tool 事件条件字段不存在（验证：`test_loader.py` 校验用例逐条触发 HookError）。
- [ ] 四动作执行器：command 捕获退出码/stdout/stderr/超时并传 env；http 按方法/headers/body 发请求；prompt 落 injector；subagent 记日志跳过（验证：`tests/hooks/test_actions.py`）。

## 集成

- [ ] 条件语义：all 全满足才触发、any 任一满足即触发、省略 if 无条件触发（验证：`tests/hooks/test_engine.py`）。
- [ ] 执行控制：once 会话内二次跳过；async 不阻塞主流程且完成有 notify；timeout 到期按失败处理；多命中按声明顺序执行（验证：`test_engine.py`）。
- [ ] prompt 注入：内容出现在 volatile 尾块（env 后、plan/memory 前）、每轮重建可见、同 key 去重、`clear()` 复位（验证：`tests/hooks/test_injection.py`）。
- [ ] tool_pre 拦截链路：非零退出 → 工具未执行、`Hook 拦截: <stderr>` 作为工具结果回灌；零退出 → 正常执行；hook 命令异常/超时 → 记日志放行；权限拒绝的工具不触发 tool_pre（验证：`tests/hooks/test_intercept.py`）。
- [ ] 发射点齐全：round_start 每轮、stop 四出口（done/limit/stall/unknown_tool）、user_message 非 resume 时触发（验证：`test_intercept.py` 发射断言）。
- [ ] Hook 失败不中断主流程：动作抛异常时 agent 循环继续（验证：`test_engine.py`/`test_intercept.py` 故障注入用例）。
- [ ] 零 Hook 短路：不配 `.hooks.yaml` 时 agent/builder/chat 行为与现状一致（验证：`tests/test_agent*.py`、`tests/test_prompt*.py`、`tests/test_chat.py` 全绿）。

## 验证与测试

- [ ] 项目无语法错误、模块可导入（验证：`python -c "import codepilot.hooks, codepilot.ui.app, codepilot.main"`）。
- [ ] 全量单测通过（验证：`pytest -q` —— 既有 391 + state16 新增全绿）。
- [ ] lint 无回归（验证：`ruff check codepilot/hooks tests/hooks` 零告警；`ruff check .` 仅既有基线）。

## 端到端场景（阶段六真机 / TUI）

- [ ] 场景 1（写后自动格式化）：项目 `.hooks.yaml` 配 tool_post + write/edit + command `ruff format {file_path}` → 让模型改一个文件，观察格式化自动发生、`[hook]` 系统行出现（验证：真机观察文件被格式化 + TUI 系统行）。
- [ ] 场景 2（可编程拦截）：配 tool_pre + command 脚本（如参数含某关键词 exit 1 并 echo 原因到 stderr）→ 触发对应工具调用，观察工具未执行、模型收到拒绝原因并调整策略（验证：真机观察模型后续轮行为）。
- [ ] 场景 3（会话注入）：配 session_start + prompt 注入 → 新会话问模型可见该注入内容；`/clear` 后再问不可见（验证：真机两轮问答对比）。
- [ ] 场景 4（异步 + 超时）：配 async command（sleep 超过 timeout）→ 主流程不阻塞、超时后日志 + TUI 系统行报失败（验证：真机观察响应不卡顿 + logs/codepilot.log）。
- [ ] 场景 5（fail-fast）：配一条非法规则（如 tool_pre + async）→ 启动即报明确错误退出（验证：真机启动观察 stderr）。
- [ ] 场景 6（权限规则升级）：权限规则文件里用 `!`/`/regex/` 新语法 → 判定符合预期；既有规则不变（验证：真机或 `test_permissions_policy.py` 扩展用例）。

## 验收口径

- [ ] spec AC1–AC10 均有对应可观测条目（AC1↔两级加载+缺文件 · AC2↔发射点齐全+场景3 · AC3↔四语法+权限回归 · AC4↔拦截链路+场景2 · AC5↔注入+场景3 · AC6↔http+场景4 · AC7↔subagent 占位 · AC8↔once/async/timeout+场景4 · AC9↔F10 校验+场景5 · AC10↔零 Hook 短路+全量回归）。
- [ ] 人工真机清单（阶段五交付后对照）逐项登记实际结果，与预期不符如实记录为差异。
