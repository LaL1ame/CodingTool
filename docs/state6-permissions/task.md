# CodePilot 权限系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `codepilot/permissions/__init__.py` | 导出公开符号 |
| 新建 | `codepilot/permissions/rules.py` | Rule/Verdict + 解析/合并/匹配 |
| 新建 | `codepilot/permissions/blacklist.py` | 危险命令正则 + hit |
| 新建 | `codepilot/permissions/sandbox.py` | 路径防逃逸 check_escape |
| 新建 | `codepilot/permissions/mode.py` | PermissionMode + fallback_for |
| 新建 | `codepilot/permissions/loader.py` | 三级 YAML 读规则 |
| 新建 | `codepilot/permissions/policy.py` | PermissionManager 五层裁决 |
| 修改 | `codepilot/agent.py` | 注入 pm + 执行前判定，移除 confirm 注入 |
| 修改 | `codepilot/tools/run.py` | 移除内置确认逻辑 |
| 修改 | `codepilot/app.py` | PermissionScreen + ask_permission |
| 修改 | `codepilot/config.py` | permissions 段 |
| 修改 | `codepilot/main.py` | 组装 pm + 接线 |
| 修改 | `config.example.yaml` | permissions 示例 |
| 新建 | `tests/test_permissions_rules.py` | 解析/匹配/合并 |
| 新建 | `tests/test_permissions_policy.py` | 五层/优先级/人机/拒绝循环 |
| 新建 | `tests/test_permissions_sandbox.py` | 逃逸 |
| 修改 | `tests/test_run.py` `tests/test_agent.py` | run 迁移后同步 |

## T1: rules 模块

**文件：** `codepilot/permissions/rules.py`
**依赖：** 无
**步骤：**
1. `Verdict` 枚举：ALLOW/ASK/DENY
2. `Rule` dataclass（tool, pattern, verdict）
3. `parse_rule(text)`：解析 `Tool(pattern)`；`verdict_from_text` 支持 allow/ask/deny
4. `match_text(pattern, subject)`：fnmatch（无通配时按精确）
5. `merge(rule_lists)`：并集
**验证：** `python -c "from codepilot.permissions.rules import parse_rule, Rule"` 导入成功

## T2: rules 单测

**文件：** `tests/test_permissions_rules.py`
**依赖：** T1
**步骤：** 覆盖 parse（`run(git *)`）、verdict 解析、glob 命中、精确命中、未命中、merge
**验证：** `pytest tests/test_permissions_rules.py -q` 通过

## T3: mode 模块

**文件：** `codepilot/permissions/mode.py`
**依赖：** T1（Verdict）
**步骤：** `PermissionMode`(STRICT/DEFAULT/PERMISSIVE) + `fallback_for(mode)->Verdict`
**验证：** `pytest` 内联断言三档兜底为 deny/ask/allow

## T4: blacklist 模块

**文件：** `codepilot/permissions/blacklist.py`
**依赖：** 无
**步骤：**
1. 预置 `BLACKLIST_RE` 若干危险命令正则（`rm -rf /`、`shutdown`、`format`、`pwsh/PS -EncodedCommand` 等）
2. `hit(command) -> str | None`
**验证：** 内联断言高危串命中、普通 `git status` 不命中

## T5: sandbox 模块

**文件：** `codepilot/permissions/sandbox.py` + `tests/test_permissions_sandbox.py`
**依赖：** 无
**步骤：**
1. `check_escape(file_path, workspace) -> str | None`：resolve() 解符号链 + is_relative_to
2. 测试：`../` 越界、符号链接指向界外 → 命中；目录内 → None
**验证：** `pytest tests/test_permissions_sandbox.py -q` 通过

## T6: loader 模块

**文件：** `codepilot/permissions/loader.py`
**依赖：** T1
**步骤：** `load_rules_from_files(paths)` 逐个 safe_load→parse_rule；文件缺失/空忽略
**验证：** 用 tmp YAML 单测解析出规则

## T7: policy（PermissionManager）

**文件：** `codepilot/permissions/policy.py` + `codepilot/permissions/__init__.py`
**依赖：** T3,T4,T5,T6
**步骤：**
1. `Decision(allow, reason)` dataclass
2. `PermissionManager.__init__`：workspace/mode/rules/blacklist/subject_map/human_callback/local_rules_path
3. `DEFAULT_SUBJECT_MAP`
4. `async check(tool, arguments) -> Decision`：黑名单(run)→沙箱(file_path)→规则匹配→模式兜底→human(ask)
5. 会话放行 `session_cache`（(tool, subject)）；永久放行写 local YAML
6. `set_mode()`
7. `__init__.py` 导出 PermissionManager/Decision/Verdict/PermissionMode
**验证：** `python -c "from codepilot.permissions import PermissionManager"` 导入成功

## T8: policy 单测（五层/优先级/人机/会话/永久写存储）

**文件：** `tests/test_permissions_policy.py`
**依赖：** T7
**步骤：** 覆盖：黑名单硬拦不可被 allow 规则放开；沙箱越界 deny；deny>ask>allow；无命中三档兜底；permissive 不覆盖显式 deny；ask→human ALLOW_THIS/SESSION/FOREVER（永久写 local 且 session 内不再问）；DENY；拒绝后循环继续
**验证：** `pytest tests/test_permissions_policy.py -q` 通过

## T9: agent 接入判定

**文件：** `codepilot/agent.py`
**依赖：** T7
**步骤：**
1. `Agent.__init__` 增 `permission: PermissionManager | None = None`
2. `_execute_one` 执行前：`d = await permission.check(tc.name, tc.arguments)`；拒绝→返回 ToolResult(error="权限拒绝: reason")
3. 移除 `run` 的 `confirm_callback` 注入
**验证：** 更新 `tests/test_agent.py` 拒绝场景：covered的 Tool 被拒→tool_result.error 含「权限拒绝」且循环继续

## T10: run 迁移

**文件：** `codepilot/tools/run.py` + `tests/test_run.py`
**依赖：** T9
**步骤：**
1. run.execute 移除 confirm 逻辑，仅纯执行；确认由权限 human 层掌管
2. 同步 `tests/test_run.py`（去掉 confirm_callback 或改由权限层 mock）
**验证：** `pytest tests/test_run.py -q` 通过

## T11: TUI 权限确认屏

**文件：** `codepilot/app.py`
**依赖：** T10
**步骤：**
1. 新增 `PermissionScreen`（4 项：允许本次/拒绝/本会话/永久允许）映射 HumanChoice
2. `ask_permission(tool, subject) -> HumanChoice`（push_screen + wait_for_dismiss）
**验证：** `python -c "import codepilot.app"` 导入 + 静态检查

## T12: config + main 接线

**文件：** `codepilot/config.py` + `codepilot/main.py` + `config.example.yaml`
**依赖：** T11
**步骤：**
1. config 加 `permissions:` 段（mode, user/project/local_rules 路径）与 `load_permissions_config`
2. main 构造 PermissionManager（读三级文件 + mode）→ 构建 app 后注入 `app.ask_permission` 为 human_callback → 挂到 agent
3. config.example.yaml 补示例；.gitignore 加 `*.local.yaml`
**验证：** `python -c "import codepilot.main"` 导入成功

## T13: 全量回归

**文件：** 全仓
**依赖：** 全部
**步骤：** `bash ci/run-tests.sh` 全部通过
**验证：** ALL CHECKS PASSED

## 执行顺序

```
T1 → T2
   ↘ T3 → T4 → T5 → T6 ┐（T3-6 可与 T2 并行推进）
                        ↓
                        T7 → T8 → T9 → T10 → T11 → T12 → T13
```

核心链：T1（规则模型）→ T7（策略引擎）→ T9（agent 接入）→ T11（TUI）→ T12（接线）。T3-6 是 T7 的四个叶子模块，可并行。