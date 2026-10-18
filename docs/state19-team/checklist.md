# Team Lead 长期小组 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。
> 标注「单测」的项由自动化覆盖；「真机」项需要 TUI 实际运行。

## 实现完整性

### 小组与持久化

- [ ] 可创建两个小组，列出、查看、切换当前小组；输出区分当前组。
  （验证：`tests/teams/test_manager.py` + 真机 `/team list`、`/team use`）
- [ ] 重启后小组、成员记录、任务板、邮箱仍可加载；未终止成员显示 offline。
  （验证：临时目录重载测试 + 真机重启）
- [ ] 当前无小组时，Lead 团队工具返回明确错误，不自动创建小组。
  （验证：`tests/teams/test_tools_lead.py`）
- [ ] 小组目录包含 `team.json`、`registry.json`、`tasks.json`、成员元数据、
  Lead/成员邮箱与锁文件。
  （验证：创建后检查临时/真实用户目录）
- [ ] 损坏的任务/成员/邮箱记录被隔离并报告，不导致整个小组不可加载。
  （验证：`tests/teams/test_storage.py`、`tests/teams/test_mailbox.py`）

### 成员派生与运行后端

- [ ] 派生成员后花名册出现该成员，记录角色、worktree 绝对路径、分支、后端、
  是否需审批；目录与分支实际存在。
  （验证：`tests/teams/test_manager.py` + 真机检查）
- [ ] 显式指定未实现后端时派生失败并说明原因；自动模式记录实际后端；
  无静默降级。
  （验证：`tests/teams/test_settings.py`、`test_manager.py`）
- [ ] 终止成员后协程停止、拒绝后续消息；worktree 与分支保留。
  （验证：`tests/teams/test_manager.py` + 真机 `git worktree list`）
- [ ] 进程重启后的 offline 同名成员可重新派生（全新上下文）；运行中/terminated
  同名成员被拒绝。
  （验证：`tests/teams/test_manager.py`）
- [ ] 成员状态 spawning/running/idle/blocked/offline/terminated/failed 可从
  元数据、工具输出与日志观察。
  （验证：状态机测试 + 真机 `/team show`）

### 工具面隔离

- [ ] Lead 会话可见 `team_*` 编排工具，不可见 `task_*`/`send_message`/`broadcast`。
  （验证：`tests/teams/test_visibility.py`）
- [ ] 成员会话可见任务 CRUD、领取、点对点/广播工具，不可见 `team_*`。
  （验证：`tests/teams/test_visibility.py`、`test_tools_member.py`）
- [ ] 普通子 Agent 与未组队主入口看不到成员协作工具；Fork 不继承团队工具。
  （验证：`tests/teams/test_visibility.py`）
- [ ] 成员系统提示包含小组、成员名、角色、worktree 路径、分支与消息消费约定。
  （验证：`tests/teams/test_prompts.py` + 真机成员轨迹）

### 共享任务板

- [ ] Lead 可创建带依赖任务、更新字段、指派成员、列出任务。
  （验证：`tests/teams/test_tools_lead.py`）
- [ ] 成员可创建、查看、更新、删除任务并领取未指派任务。
  （验证：`tests/teams/test_tools_member.py`）
- [ ] 依赖未全部完成、任务已被指派、任务不存在时，领取被拒绝并说明具体原因。
  （验证：`tests/teams/test_tasks.py`）
- [ ] 两个并发领取只有一个成功；列表/读取反映最终状态。
  （验证：`tests/teams/test_tasks.py` 并发用例）
- [ ] 删除被依赖的任务被拒绝；删除执行中的任务被拒绝。
  （验证：`tests/teams/test_tasks.py`）
- [ ] 任务完成后记录结果摘要，Lead 可读取。
  （验证：`tests/teams/test_runtime.py` + 真机）

### 邮箱与消息协议

- [ ] 点对点消息包含发件人、收件人、类型、正文、自动时间戳、默认未读、
  自动摘要。
  （验证：`tests/teams/test_mailbox.py`）
- [ ] 广播为每个目标成员生成独立副本，已读状态互不影响。
  （验证：`tests/teams/test_mailbox.py`、`test_manager.py`）
- [ ] 邮箱锁被占用时重试；过期锁可被接管；失败不产生半写消息。
  （验证：`tests/teams/test_locks.py`、`test_mailbox.py`）
- [ ] 未知成员、terminated 成员、跨组投递被拒绝。
  （验证：`tests/teams/test_manager.py`、`test_registry.py`）
- [ ] 普通消息在任务边界消费；运行中的任务不被普通消息打断。
  （验证：`tests/teams/test_runtime.py` + 真机观察）
- [ ] 五类消息 `text`/`broadcast`/`plan_request`/`plan_decision`/`status`
  可写入、读回并保留结构字段。
  （验证：`tests/teams/test_mailbox.py`）
- [ ] 非法类型或缺失必填字段的消息被明确拒绝，不当作普通文本处理。
  （验证：`tests/teams/test_mailbox.py`）

### 审批闭环

- [ ] 需审批成员在收到/领取任务后先发计划；批准前不执行任务工作或写文件。
  （验证：`tests/teams/test_approval.py`）
- [ ] 批准后任务进入 in_progress 并执行；驳回后任务回 pending、清 assignee、
  成员 idle。
  （验证：`tests/teams/test_approval.py`）
- [ ] 等待超时后成员 blocked 并发送一次 status；任务保持未开始；Lead 之后发送的
  decision 仍可生效。
  （验证：`tests/teams/test_approval.py` + 真机短超时配置）
- [ ] 非审批成员不进入等待，收到任务后直接执行。
  （验证：`tests/teams/test_runtime.py`、`test_approval.py`）
- [ ] 审批 decision 只被对应任务消费；其他未读消息保持未读。
  （验证：`tests/teams/test_mailbox.py`、`test_approval.py`）

### 成员生命周期

- [ ] 成员完成任务且无更多指派时标记 idle 并发送 status 通知 Lead。
  （验证：`tests/teams/test_runtime.py` + 真机）
- [ ] Lead 向 idle 成员发消息可唤醒，成员在同一进程内继续使用原有上下文。
  （验证：`tests/teams/test_runtime.py` 上下文连续性用例 + 真机）
- [ ] 成员异常标记 failed 并通知 Lead；任务保持 in_progress。
  （验证：`tests/teams/test_runtime.py`）
- [ ] 终止成员标记 terminated，拒绝后续消息，目录/分支保留。
  （验证：`tests/teams/test_manager.py`）
- [ ] 进程重启后未终止成员为 offline；不自动恢复上下文。
  （验证：`tests/teams/test_manager.py` + 真机重启）
- [ ] 退出时取消全部成员协程并释放 worktree 占用锁，目录仍在。
  （验证：`tests/teams/test_manager.py`、真机退出后 `git worktree list`）

### Lead 编排

- [ ] Lead 能拆出带依赖任务、派生成员、指派/允许领取、发送消息、终止成员。
  （验证：`tests/teams/test_end_to_end.py`）
- [ ] 全部完成或无法继续时，Lead 报告成员结果、任务状态、worktree 路径与分支。
  （验证：端到端测试 + 真机）
- [ ] 本阶段不执行 merge/rebase/cherry-pick；成员改动保留在各自 worktree。
  （验证：代码审查 + 真机 `git status`/`git branch`）

## 集成

- [ ] base registry 不含任何团队工具；lead registry 只多出 Lead 工具；
  成员 registry 只多出成员工具。
  （验证：`tests/teams/test_visibility.py`）
- [ ] 主 Agent 使用 lead registry，SubAgentManager 使用 base registry。
  （验证：`tests/teams/test_wiring.py`）
- [ ] 成员 task 指派后对应运行时被唤醒；消息到达目标成员后同样被唤醒。
  （验证：`tests/teams/test_manager.py`）
- [ ] 发给 Lead 的消息产生事件，App 在流式/非流式下都能安全回灌主对话。
  （验证：`tests/test_team_app.py`）
- [ ] `/team list|use|show|create` 可触达 TeamManager；未启用时安全降级。
  （验证：`tests/test_commands_builtins.py`）
- [ ] main 启动正常加载当前组；退出调用 `TeamManager.shutdown()`。
  （验证：`tests/teams/test_wiring.py` + 真机退出日志）
- [ ] WorktreeManager.release 不影响目录/分支，释放后同名可再次派生。
  （验证：`tests/worktree/test_manager.py`）
- [ ] ToolRegistry.clone 共享工具实例且互不影响注册表。
  （验证：`tests/test_tools_registry.py`）

## 验证与测试

- [ ] 项目无语法错误，模块可导入。
  （验证：`python -c "import codepilot.teams, codepilot.main; print('ok')"`）
- [ ] 全量单测通过，数量不少于基线 730。
  （验证：`python -m pytest -q`）
- [ ] 新代码无 lint 告警；`ruff check .` 保持 27 条既有基线不新增。
  （验证：`ruff check .`）
- [ ] 代码中无 `os.chdir`；团队成员运行时使用显式绝对路径。
  （验证：`grep -rn "os.chdir" codepilot/`）
- [ ] 团队领域模块（model/naming/settings/locks/storage/tasks/registry/mailbox/prompts）
  不 import UI/Textual；runtime 不 import UI。
  （验证：代码审查 + import 测试）
- [ ] 至少一个临时目录端到端测试覆盖建组→任务→派生→审批→完成→报告。
  （验证：`tests/teams/test_end_to_end.py`）
- [ ] 成员 registry 不含 `agent` 与 `load_skill`；MCP 工具按现有装配保留。
  （验证：`tests/teams/test_visibility.py`）

## 端到端场景（真机 / TUI）

> 具体可粘贴的验收提示词在阶段五交付；此处定义场景与可观测预期。
> 真机验收前建议在 `config.yaml` 配置较短的 `team.approval_timeout_seconds`
> 以便测试超时路径，验收后恢复默认。

- [ ] **场景 1：建组与两名成员并行。** 用户给出目标，Lead 创建小组、建任务、
  派生两个成员；两名成员各自在独立 worktree 中工作，互不覆盖；主工作区文件不变。
  （真机：`/team show`、`git worktree list`、成员结论）
- [ ] **场景 2：任务依赖与抢单。** Lead 创建 A、B 两个任务，B 依赖 A；成员尝试领取 B
  → 被拒绝并列出未完成依赖；A 完成后 B 可领取且只有一个成员成功。
  （真机：任务板输出、成员回复）
- [ ] **场景 3：审批批准。** 需审批成员领取任务 → 先提交计划 → Lead 收到通知并批准
  → 成员开始执行；批准前无文件改动。
  （真机：Lead 收件箱、成员状态、git diff）
- [ ] **场景 4：审批驳回。** 同上但 Lead 驳回 → 任务回到 pending、assignee 清空、
  成员 idle；无文件改动。
  （真机：任务板、成员状态）
- [ ] **场景 5：审批超时。** 配置短超时后成员提交计划且 Lead 不回复 → 成员 blocked 并
  发送一次 status；任务未开始；随后 Lead 补发 decision 仍可继续。
  （真机：日志、任务板、成员状态）
- [ ] **场景 6：点对点与广播。** 成员 A 给成员 B 发消息、给全组广播；B 在任务边界
  读到消息；消息已读状态与摘要正确；Lead 收到广播副本。
  （真机：邮箱文件、成员回复、`/team show`）
- [ ] **场景 7：空闲唤醒上下文连续。** 成员完成任务后 idle；Lead 再发新指令 →
  成员在同一上下文继续，无需重新派生（能引用上一任务结论）。
  （真机：成员回复内容、日志无新 spawn）
- [ ] **场景 8：终止与 worktree 保留。** Lead 终止运行中/空闲成员 → 状态 terminated、
  后续消息被拒绝；worktree 与分支仍在，改动未丢。
  （真机：`git worktree list`、`/team show`、再次发送消息的拒绝结果）
- [ ] **场景 9：重启后成员离线。** 退出并重启 CodePilot → 小组、任务、邮箱仍在；
  未终止成员显示 offline；重新派生同名成员使用全新上下文。
  （真机：重启后 `/team show`、成员回答是否记得旧上下文）
- [ ] **场景 10：工具面隔离。** Lead 工具列表有 `team_*` 无成员工具；成员轨迹中
  有任务/消息工具无 `team_*`；普通子 Agent（agent 工具委派）两者都无。
  （真机：成员轨迹、`/agents` 或提示检查）

## Spec AC 覆盖对照

| Spec AC | 对应 checklist 位置 |
|---------|---------------------|
| AC1 小组生命周期 | 小组与持久化 1–5；场景 9 |
| AC2 成员派生与后端 | 成员派生与运行后端 1–5；场景 1、8 |
| AC3 工具面隔离 | 工具面隔离 1–4；场景 10 |
| AC4 共享任务板 | 共享任务板 1–6；场景 2 |
| AC5 邮箱与注册表 | 邮箱与消息协议 1–7；场景 6 |
| AC6 结构化协议消息 | 邮箱与消息协议 6–7 |
| AC7 审批闭环 | 审批闭环 1–5；场景 3–5 |
| AC8 空闲/唤醒/终止 | 成员生命周期 1–6；场景 7–9 |
| AC9 Lead 端到端编排 | Lead 编排 1–3；场景 1–3 |
| AC10 并发与路径安全 | 共享任务板 4；邮箱锁 3；小组与持久化 5 |
| AC11 可观测性与故障隔离 | 成员生命周期 3；小组与持久化 5；审批超时 3 |
| AC12 回归、架构与跨平台 | 验证与测试 1–7 |
