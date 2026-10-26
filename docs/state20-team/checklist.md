# Team Lead 强隔离与收口 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。
> 标注「单测」的项由自动化覆盖；「真机」项需要 TUI / 实际运行。

## 实现完整性

### 跨进程上下文（A2）

- [ ] 成员运行产生对话后，其上下文落盘为 JSONL（`<team_root>/<team>/context/<member>.jsonl`）。
  （验证：`tests/teams/test_context.py`）
- [ ] 协议原生 msgs（assistant 带 tool_use、user 带 tool_result、纯文本）保存→恢复往返无损。
  （验证：`tests/teams/test_context.py` 往返用例）
- [ ] 重新派生同名成员时，有上下文则自动恢复历史；无/损坏则全新上下文、不崩溃。
  （验证：`tests/teams/test_manager.py`、`test_runtime.py`）
- [ ] 落盘是幂等增量（游标），重复调用不重复写；崩溃前尽量完整。
  （验证：`tests/teams/test_context.py` 幂等用例）

### git 合并（A3）

- [ ] 合并前成员 dirty 改动被 commit 到成员分支；无改动成员不产生新提交；提交失败只报该成员。
  （验证：`tests/teams/test_merge.py`）
- [ ] 合并到固定汇聚分支 `codepilot/team/<team>/merged`（不存在则创建），主工作区当前分支不变。
  （验证：`tests/teams/test_merge.py`）
- [ ] 无冲突合并成功，返回合入成员与结果摘要。
  （验证：`tests/teams/test_merge.py`）
- [ ] 有冲突整体回滚（abort），成员分支与主工作区原样，上报冲突文件清单。
  （验证：`tests/teams/test_merge.py` 冲突用例）
- [ ] 前提校验（成员分支存在、worktree 可用、汇聚分支名合法）不满足时明确报错、不执行。
  （验证：`tests/teams/test_merge.py`）
- [ ] `team_merge` 工具可用，缺省合当前组所有非 terminated 成员。
  （验证：`tests/teams/test_tools_lead.py`）

### coordinator（A4）

- [ ] 四种双锁组合中仅「配置开 + 环境变量开」进入 coordinator；其余保持 state19 行为。
  （验证：`tests/teams/test_coordinator.py` 四组合）
- [ ] `CoordinatorState.source` 正确记录实际组合（config+env / config / env / 空）。
  （验证：`tests/teams/test_coordinator.py`）
- [ ] coordinator 开启时 lead 工具面无 write/edit，保留 read/run/team_*；成员与普通子 Agent 不受影响。
  （验证：`tests/teams/test_coordinator.py`、`test_wiring.py`）
- [ ] coordinator 提示块注入 lead 上下文（纯编排者、文件修改交成员、用 run 执行 git 合并）。
  （验证：`tests/teams/test_coordinator.py`、`test_wiring.py`）
- [ ] 未开启 coordinator 时 lead registry 完整，行为与 state19 一致。
  （验证：`tests/teams/test_wiring.py`）

### GitRunner 扩展

- [ ] `add/commit/checkout_branch/merge/abort_merge/diff_names/branch_created` 可用且走参数列表。
  （验证：`tests/worktree/test_git.py`）
- [ ] `merge` 冲突检测：退出码非 0 视为冲突。
  （验证：`tests/worktree/test_git.py`）

## 集成

- [ ] `spawn_member` 恢复钩子在构造 Agent 前生效（有历史则初始 `_msgs` 为历史）。
  （验证：`tests/teams/test_manager.py`）
- [ ] `MemberRuntime._run_agent` 结束后增量落盘。
  （验证：`tests/teams/test_runtime.py`）
- [ ] `manager.merge_team` 门面经 `asyncio.to_thread` 调 `TeamMerger`。
  （验证：`tests/teams/test_manager.py`、`test_merge.py`）
- [ ] `main.py` 装配：coordinator 检测、lead registry 收窄、注入块。
  （验证：`tests/teams/test_wiring.py` + `python -c "import codepilot.main"`）
- [ ] 合并只动汇聚分支，主工作区当前分支永不被 checkout/merge 污染。
  （验证：`tests/teams/test_merge.py` 断言当前分支不变）

## 验证与测试

- [ ] 项目无语法错误，模块可导入。
  （验证：`python -c "import codepilot.teams.context, codepilot.teams.merge, codepilot.teams.coordinator, codepilot.main"`）
- [ ] 全量单测通过，数量不少于基线 918。
  （验证：`python -m pytest -q`）
- [ ] 新代码无 lint 告警；`ruff check .` 保持 27 条既有基线不新增。
  （验证：`ruff check .`）
- [ ] 代码中无 `os.chdir`；Git 调用使用参数列表。
  （验证：`grep -rn "os.chdir" codepilot/`）

## 端到端场景（真机 / 临时仓库）

> 自动化覆盖为主；真机可跑以下提示词验证可观测行为。

- [ ] **场景 A：成员上下文跨进程恢复。** 派生成员 → 让其执行任务产生对话 → 退出重启 →
  重新派生同名成员 → 它能回忆之前任务（而非从零）。
  （单测覆盖；真机：`/team show` 后重新派生，看成员是否记得旧任务）
- [ ] **场景 B：多成员合并到汇聚分支。** 两个成员各自改不同文件 → `team_merge` →
  汇聚分支 `codepilot/team/<team>/merged` 含两者改动，主工作区当前分支不变。
  （真机：`git log codepilot/team/<team>/merged`、`git branch`）
- [ ] **场景 C：冲突回滚。** 两个成员改同一文件不同处 → `team_merge` → 报告冲突文件、
  回滚，成员分支与主工作区原样。
  （真机：合并失败提示 + `git status` 干净 + 分支未变）
- [ ] **场景 D：coordinator 双锁。** 配置开 + 环境变量 `CODEPILOT_COORDINATOR=1` →
  Lead 工具面无 write/edit、有 run/team_*，成员照常干活；任一锁关 → 工具面完整。
  （真机：观察 Lead 可用工具 + 成员能写文件）

## Spec AC 覆盖对照

| Spec AC | 对应 checklist 位置 |
|---------|---------------------|
| AC1 跨进程恢复 | 跨进程上下文 1–4；场景 A |
| AC2 合并前落盘 | git 合并 1；场景 B |
| AC3 合并与回滚 | git 合并 2–5；场景 B/C |
| AC4 coordinator 双锁 | coordinator 1–4；场景 D |
| AC5 合并工具 | git 合并 6；场景 B |
| AC6 安全与回归 | 验证与测试 1–4 |
| AC7 可观测与跨平台 | 集成 4–5；验证与测试 1–4 |
