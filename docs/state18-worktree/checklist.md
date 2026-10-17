# Worktree 工作目录隔离 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。
> 标注「单测」的项已由自动化覆盖；「真机」项需要 TUI / 多任务实际运行。

## 实现完整性

### 角色与命名

- [ ] 角色 frontmatter 可声明 `isolation: worktree`；缺省角色无隔离；非法值在启动加载期报错。
  （验证：`tests/subagents/test_loader.py` + 启动一个含非法 isolation 的角色，观察进程报错退出）
- [ ] 合法名称可创建：单段、多段嵌套、总长 64、每段 32。
  （验证：`tests/worktree/test_naming.py`）
- [ ] 非法名称全部拒绝：`.`、`..`、空段、`a//b`、`/abs`、`a/../b`、反斜杠、超长、非法字符、
  大写、段首符号、段尾点/空格、Windows 保留名。
  （验证：`tests/worktree/test_naming.py` 表驱动用例）
- [ ] 省略 `worktree` 参数时自动生成 `<role>-<task_id>`，且通过同一套校验。
  （验证：`tests/worktree/test_naming.py` + `tests/subagents/test_manager.py`）

### 生命周期

- [ ] 首次创建得到目录、`codepilot/<name>` 分支、元数据；分支基于创建时 `HEAD`。
  （验证：`tests/worktree/test_manager.py`，用临时 Git 仓库断言 `rev-parse`）
- [ ] 目录已存在且三检通过时复用，且**不调用任何 Git 命令**。
  （验证：`tests/worktree/test_manager.py`，fake Git 的 `run` 一旦被调用即失败）
- [ ] 目录已存在但三检失败（普通目录/缺 `.git`/缺元数据）时报错且不接管。
  （验证：`tests/worktree/test_manager.py`）
- [ ] 同名分支存在、目录不存在时挂接已有分支；被其他 worktree 占用时报错。
  （验证：`tests/worktree/test_manager.py`）
- [ ] 同名 Worktree 被运行中任务占用时，第二个委派被拒绝且错误含占用者 task_id。
  （验证：`tests/worktree/test_manager.py`）
- [ ] 无未提交改动且相对 base 无新提交时，任务结束自动删除目录、分支与元数据。
  （验证：`tests/worktree/test_manager.py`）
- [ ] 有未提交改动或相对 base 有新提交时，任务结束保留目录与分支，摘要含路径、分支、原因。
  （验证：`tests/worktree/test_manager.py` + `tests/subagents/test_manager.py`）
- [ ] 删除命令对有变更目录默认拒绝；`--force` 删除目录与未提交改动但保留分支。
  （验证：`tests/worktree/test_manager.py`）

### 隔离运行时

- [ ] 隔离子 Agent 的 read/write/edit/glob/grep/run 全部绑定 worktree 绝对路径。
  （验证：`tests/subagents/test_isolation.py` 断言工具实例的 workspace）
- [ ] agent、load_skill、MCP 等系统工具与主会话共享同一实例。
  （验证：`tests/subagents/test_isolation.py` 断言 `is` 相同）
- [ ] 隔离子 Agent 的 `EnvContext.cwd`、项目指令、记忆索引、context offload 目录、
  permission sandbox 均以 worktree 为根。
  （验证：`tests/subagents/test_isolation.py`）
- [ ] 系统提示包含隔离路径说明块，明确工作目录、分支与「改动留在此目录」。
  （验证：`tests/subagents/test_isolation.py` + 真机查看子 Agent 轨迹首轮 system 内容）
- [ ] 子 Agent 运行期间进程 cwd 不发生变化。
  （验证：`tests/subagents/test_isolation.py` 在运行前后断言 `Path.cwd()` 相同；源码中无 `os.chdir`）
- [ ] 非隔离角色、Fork 式委派的行为与 state17 完全一致。
  （验证：state17 现有 `tests/subagents/test_runner.py` / `test_manager.py` 断言不改动即通过）

### 环境初始化

- [ ] `config.yaml`、`.hooks.yaml`、`codepilot-permissions.local.yaml` 被复制进 worktree。
  （验证：`tests/worktree/test_initializer.py`）
- [ ] `.venv` / `venv` / `node_modules` 在存在时成为目录链接。
  （验证：`tests/worktree/test_initializer.py`，断言目标可读到源内文件）
- [ ] `include` 规则只复制被 Git 忽略的匹配文件；匹配但被追踪的文件不复制。
  （验证：`tests/worktree/test_initializer.py`，用 `git check-ignore` 构造两种文件）
- [ ] 目标已存在时不覆盖；源不存在时跳过不失败。
  （验证：`tests/worktree/test_initializer.py`）
- [ ] 规则逃逸主仓或 IO 失败时创建失败，且不残留半成品目录/分支。
  （验证：`tests/worktree/test_initializer.py` + `tests/worktree/test_manager.py`）
- [ ] `.githooks/` 存在时 worktree 能读到 hooks；`core.hooksPath` 为相对路径时被修正为绝对路径。
  （验证：`tests/worktree/test_initializer.py` 读取 `git config --worktree --get core.hooksPath`）

### 清理与命令

- [ ] 启动后立即执行一次过期清理，之后按配置周期运行。
  （验证：`tests/worktree/test_manager.py` 短周期任务；真机启动日志含清理报告）
- [ ] 24h TTL：过期且 clean、无新提交、未占用的目录被删除；dirty / 陌生 / 占用 / 非前缀分支全部跳过并记录原因。
  （验证：`tests/worktree/test_manager.py`）
- [ ] `/worktree list` 显示名称、分支、路径、占用、dirty、ahead。
  （验证：`tests/test_commands_builtins.py` + 真机执行）
- [ ] `/worktree rm <name>` 与 `/worktree rm <name> --force` 行为符合保留/删除规则。
  （验证：`tests/test_commands_builtins.py` + 真机执行）
- [ ] WorktreeManager 未启用时，`/worktree` 命令与 `worktree` 参数均安全降级/明确报错。
  （验证：`tests/test_commands_builtins.py`）

### 扫描隔离

- [ ] 主 Agent 的项目指令、角色、Skill 递归发现均不进入 `.codepilot/`。
  （验证：`tests/test_memory_resolver.py`、`tests/test_skills_resolver.py`、`tests/subagents/test_loader.py`）
- [ ] 主 Agent 的 `glob` / `grep` 结果不包含 `.codepilot/worktrees/` 下内容。
  （验证：`tests/test_glob_worktree.py`、`tests/test_grep_worktree.py`）
- [ ] 把 `.rules`、角色、Skill 放进某个 worktree 后，主 Agent 的注入与发现不受影响。
  （验证：端到端场景 8）

### 失败与安全

- [ ] 非 Git 仓库、无有效 HEAD、Git 命令失败、名称非法、同名占用 → 委派返回可读错误，
  绝不退回主工作区。
  （验证：`tests/worktree/test_manager.py` + `tests/subagents/test_manager.py`）
- [ ] 所有 Git 调用使用参数列表与显式 cwd，无 shell 拼接。
  （验证：`tests/worktree/test_git.py` 命令失败信息；代码审查 `GitRunner`）
- [ ] 清理只删除 worktree 根内、通过三层过滤的路径。
  （验证：`tests/worktree/test_manager.py` 构造根外/陌生路径，断言不被删除）

## 集成

- [ ] `agent` 工具 schema 含 `worktree`；参数透传到委派中心。
  （验证：`tests/subagents/test_tool.py`）
- [ ] 委派中心在隔离任务前后正确调用 enter/leave；leave 结果进入任务摘要。
  （验证：`tests/subagents/test_manager.py`）
- [ ] `main` 启动时加载 `.worktree.yaml`、构造 WorktreeManager、注入 SubAgentManager 与 App。
  （验证：`python -c "import codepilot.main"` + 启动日志）
- [ ] App 的 `_CommandContextImpl` 可触达 WorktreeManager；未启用时返回 None。
  （验证：`tests/test_commands_builtins.py`）
- [ ] 后台清理任务在 App 退出时被取消，进程无悬挂任务。
  （验证：真机退出后进程正常结束；`main` 代码路径审查）

## 验证与测试

- [ ] 项目无语法错误，模块可导入。
  （验证：`python -c "import codepilot.worktree, codepilot.subagents, codepilot.ui.app, codepilot.main"`）
- [ ] 全量单测通过，数量不少于基线 584。
  （验证：`python -m pytest -q`）
- [ ] 新代码无 lint 告警；全仓保持 27 条既有基线不新增。
  （验证：`ruff check codepilot/worktree codepilot/subagents tests/worktree tests/subagents`；`ruff check .`）
- [ ] 未隔离路径的现有测试无需修改断言即可通过。
  （验证：`git diff tests/` 中不出现对既有断言的弱化/删除）

## 端到端场景（真机 / TUI）

- [ ] **场景 1：无变更自动清理。** 定义一个 `isolation: worktree` 的只读/无写入角色并委派
  → 子 Agent 在 worktree 中运行；完成后目录与分支被清理，摘要注明已清理，主工作区无变化。
  （真机：`/worktree list` 前后对比、`git status`）
- [ ] **场景 2：并行改同一文件不互相覆盖。** 两个隔离任务使用不同 worktree 名修改同一文件
  → 两份改动各自留在自己的目录，互不覆盖；主工作区文件未变。
  （真机：两个 `agent` 委派 + `read`/`git diff` 对比）
- [ ] **场景 3：有变更保留。** 隔离任务完成一次 `write`/`edit` 后结束
  → 目录保留，任务摘要含绝对路径与 `codepilot/<name>` 分支，`/worktree list` 显示 dirty。
  （真机：观察摘要与命令输出）
- [ ] **场景 4：快速恢复。** 对同名 worktree 再次委派
  → 复用已有目录，不重建分支；上次改动仍在；日志显示 reused。
  （真机：同一名称两次委派 + 日志）
- [ ] **场景 5：并发同名拒绝。** 两个任务同时使用同一个 `worktree` 名
  → 第二个任务失败，错误含占用者 task_id；主对话不中断。
  （真机：后台发起两个同名委派）
- [ ] **场景 6：环境初始化。** 在项目根放一个被忽略但列入 `include` 的文件
  → 新 worktree 内可读到该文件；`config.yaml` 被复制；`.venv`（若存在）可执行。
  （真机：子 Agent `read`/`run` 验证）
- [ ] **场景 7：删除保护。** 对有改动的 worktree 执行 `/worktree rm`
  → 拒绝并说明原因；`/worktree rm <name> --force` 后目录消失、分支仍在。
  （真机：命令输出 + `git branch --list 'codepilot/*'`）
- [ ] **场景 8：扫描隔离。** 在某 worktree 内放置 `.rules/`、`.agents/`、`.skills/`
  → 主 Agent 的 `/memory`、角色名册、Skill 列表不受影响；`glob **/*.md`/`grep` 不返回 worktree 内容。
  （真机：主对话执行命令与搜索）
- [ ] **场景 9：失败不回退。** 在非 Git 目录（或非法名称）委派隔离角色
  → 返回明确错误；主工作区文件零变化；主 Agent 可继续执行后续任务。
  （真机：临时目录启动或传入 `../x` 名称）
- [ ] **场景 10：过期清理。** 手工把一个 clean 的 worktree 元数据 `created_at` 改到 25 小时前
  → 下一次启动/清理周期删除它；dirty 的同类目录被跳过并记录原因。
  （真机：日志 + `/worktree list`）

## Spec AC 覆盖对照

| Spec AC | 对应 checklist 位置 |
|---------|---------------------|
| AC1 角色声明 | 角色与命名 1 |
| AC2 名称校验 | 角色与命名 2–4 |
| AC3 创建/恢复 | 生命周期 1–4 |
| AC4 进入/上下文 | 隔离运行时 1–5 |
| AC5 环境初始化 | 环境初始化 1–6 |
| AC6 退出/保留 | 生命周期 6–7 |
| AC7 删除保护 | 生命周期 8；清理与命令 4 |
| AC8 过期清理 | 清理与命令 1–2；场景 10 |
| AC9 管理命令 | 清理与命令 3–5 |
| AC10 扫描隔离 | 扫描隔离 1–3；场景 8 |
| AC11 失败边界 | 失败与安全 1；场景 9 |
| AC12 回归 | 验证与测试 1–4 |
