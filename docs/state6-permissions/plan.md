# CodePilot 权限系统 Plan

## 架构概览

在 **agent 唯一执行口**（`_execute_one`）上叠加一个横向判定的 **PermissionManager（策略引擎）**。任何工具执行前先过判定：按 黑名单 → 沙箱 → 规则 → 模式兜底 → 人机回路 五层依次裁决，输出「允许/拒绝」。拒绝返回错误结果，循环继续。

五层职责单一、路径清晰：
- **blacklist**：硬拦截危险命令（不可配置）。
- **sandbox**：文件工具路径防逃逸（解符号链 + 前缀判断）。
- **rules**：用户/项目/本地三级规则合并 + 匹配（精确/glob）。
- **mode**：三档兜底。
- **human**：ask 时交回用户，支持 本次/会话/永久。

run 工具内置 confirm **迁移**进 human 层，统一交给 PermissionManager。

## 核心数据结构

### Verdict / PermissionMode / HumanChoice
```python
class Verdict(Enum):  ALLOW = auto(); ASK = auto(); DENY = auto()

class PermissionMode(Enum):  STRICT = auto(); DEFAULT = auto(); PERMISSIVE = auto()

class HumanChoice(Enum):  ALLOW_THIS = auto(); ALLOW_SESSION = auto(); ALLOW_FOREVER = auto(); DENY = auto()
```

### Decision（判定结果）
```python
@dataclass
class Decision:
    allow: bool
    reason: str = ""   # 拒绝时的理由（回给模型）；允许时通常为空
```

### Rule
```python
@dataclass
class Rule:
    tool: str          # "run" / "write" / "read" ...
    pattern: str       # 匹配 subject 的 glob 串
    verdict: Verdict   # allow / ask / deny
    # source 层级不入判定（F6）；仅用于决定永久放行写哪个文件
```
规则字符串语法 `Tool(pattern)`：`run(git *)` → tool="run", pattern="git *"。

### PermissionManager
```python
class PermissionManager:
    def __init__(self, workspace: Path, mode=PermissionMode.DEFAULT,
                 rules: list[Rule] = (), blacklist: list[str] = BLACKLIST,
                 subject_map: dict[str, str] = DEFAULT_SUBJECT_MAP,
                 human_callback: Callable[[str, str], Awaitable[HumanChoice]] | None = None,
                 local_rules_path: Path | None = None) -> None:
        ...

    async def check(self, tool_name: str, arguments: dict) -> Decision:
        """五层裁决。返回 allow(True/False) + reason。"""

    def set_mode(self, mode: PermissionMode) -> None: ...
```

### Subject 映射（工具 → 规则匹配对象）
```python
DEFAULT_SUBJECT_MAP = {
    "read": "file_path", "write": "file_path", "edit": "file_path",
    "run": "command", "glob": "pattern", "grep": "pattern",
}
# subject_for(tool, arguments) -> arguments.get(map[tool])
```

## 模块设计

### codepilot/permissions/rules.py
**职责：** Rule 数据类、Verdict 枚举、规则解析与匹配。
```
parse_rule(str) -> Rule            # "run(git *)" → Rule(run, "git *", …)
verdict_from_text(str) -> Verdict  # allow/ask/deny
match_text(pattern, subject) -> bool  # fnmatch：无通配时若相等则精确命中
merge(rule_lists) -> list[Rule]    # 只是并集合并（判定与层级无关）
```
**依赖：** 无。

### codepilot/permissions/blacklist.py
**职责：** 硬层危险命令正则。
```
BLACKLIST_RE: list[re.Pattern]   # rm -rf / 、shutdown、pwsh -EncodedCommand … 预置
hit(command) -> str | None       # 返回命中的 pattern 描述，失败 None
```
**依赖：** 无。

### codepilot/permissions/sandbox.py
**职责：** 文件路径防逃逸（复用并集中化现 validate_path 思路）。
```
check_escape(file_path: str, workspace: Path) -> str | None
    # resolve() 解符号链后，若不是 workspace 子树 → 返回越界说明；否则 None
```
**依赖：** 无。

### codepilot/permissions/mode.py
**职责：** 三档兜底。
```
@dataclass fallback:  STRICT→Verdict.DENY, DEFAULT→Verdict.ASK, PERMISSIVE→Verdict.ALLOW
fallback_for(mode) -> Verdict
```
**依赖：** rules.Verdict。

### codepilot/permissions/loader.py
**职责：** 从三级 YAML 读规则并解析。
```
load_rules_from_files(paths: list[Path]) -> list[Rule]
   # 逐个文件 safe_load → 每项调 parse_rule；文件缺失/空 → 忽略
```
**依赖：** rules。

### codepilot/permissions/policy.py（PermissionManager）
**职责：** 五层裁决编排 + 会话放行记忆 + 人机回路。
```
async check(tool_name, arguments) -> Decision:
  subject = subject_map.get(tool_name); s = arguments.get(subject) if subject else None
  1 黑名单:  tool=run 且 blacklist.hit(s)             → DENY("黑名单命中…")
  2 沙箱:     subject==file_path 且 sandbox.check_escape → DENY("路径越界…")
  3 规则:     match(rules, tool_name, s) → deny? ask? allow?
  4 兜底:     无命中 → fallback_for(mode)
  5 human:    若 ASK 且 human_callback → choice
              ALLOW_THIS → 仅此轮; ALLOW_SESSION → 记 session_cache; ALLOW_FOREVER → 写 local 文件
              DENY → DENY("用户拒绝")
  session_cache: {(tool, subject): True} 命中则直接 ALLOW（不重复问）
```
**依赖：** rules/blacklist/sandbox/mode/loader。

### codepilot/agent.py
**职责：** 注入 PermissionManager；`_execute_one` 执行前判定。
```
async _execute_one(tc):
  if pm:  d = await pm.check(tc.name, tc.arguments)
         if not d.allow: return ToolResult(call_id, name, error=f"权限拒绝: {d.reason}")
  # 移除旧的 run confirm_callback 注入（已被 human 层吸收）
  ... 执行 tool.execute(**kwargs)
```

### codepilot/tools/run.py
**职责：** 移除内置确认逻辑（迁移到权限 human 层）；仅保留纯执行。

### codepilot/app.py
**职责：** 通用权限确认屏 `PermissionScreen`（4 项：允许本次/拒绝/本会话/永久允许），映射 HumanChoice；`ask_permission(tool, subject) -> HumanChoice`。

### codepilot/config.py / main.py
- config 加 `permissions:` 段：`mode`、`user_rules`/`project_rules`/`local_rules` 路径。
- main 构造 PermissionManager（读三级文件、取 mode）、构建 app 后注入 human_callback、把 pm 挂到 agent。

## 模块交互（判定数据流）

```
agent._execute_one
  └─> PermissionManager.check(tool_name, arguments)
        ├─ blacklist.hit        ─硬 deny→
        ├─ sandbox.check_escape ─硬 deny→
        ├─ rules.match          ─ deny/ask/allow →
        ├─ mode.fallback_for    ─ 无命中兜底  →
        └─ human_callback       ─ ask→choice (本次→过 / 会话→cache / 永久→写local / 拒→deny→)
        └─ 5 层无 deny → Decision(allow=True)
  └─> tool.execute(**kwargs)     # 通过才执行
```

## 文件组织
```
codepilot/permissions/
├── __init__.py     — 导出 PermissionManager/Decision/Verdict/PermissionMode
├── rules.py        — Rule, Verdict, parse/merge/match
├── blacklist.py    — BLACKLIST_RE, hit
├── sandbox.py      — check_escape
├── mode.py         — PermissionMode, fallback_for
├── loader.py       — load_rules_from_files
└── policy.py       — PermissionManager
codepilot/agent.py  — 注入 pm + 执行前判定
codepilot/tools/run.py — 移除内置确认
codepilot/app.py    — PermissionScreen + ask_permission
codepilot/config.py — permissions 段
codepilot/main.py   — 组装 pm + 接线
config.example.yaml — permissions 示例
tests/test_permissions_policy.py  — 五层/优先级/人机
tests/test_permissions_rules.py    — 解析/匹配/合并
tests/test_permissions_sandbox.py  — 逃逸
tests/test_run.py (改) / tests/test_agent.py (改) — run 迁移后行为
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 入口 | agent._execute_one 执行前单点判定 | 唯一执行口，横向切面，五层一处叠加 |
| 黑名单作用域 | 仅 run 的 command 串 | 危险命令只经 run；文件工具走沙箱 |
| 沙箱 | resolve() 解符号链 + is_relative_to | 复用/集中现有 validate_path 思路，防软链逃逸 |
| 规则 subject | tool→param 映射表 | 无改各工具，subject 取参集中管理 |
| 匹配 | fnmatch（glob + 精确兼容） | 标准库，覆盖精确与通配 |
| 优先级 | deny > ask > allow；无命中走模式兜底 | 与 spec 一致，硬 deny 恒赢 |
| 模式 | 只当无命中兜底 | 用户已确认；permissive 不覆盖显式 deny |
| run 迁移 | 内置 confirm 移除，纳入 human 层 | 统一入口，默认档仍=ask 行为兼容 |
| 拒绝语义 | 返回错误结果、循环继续 | 模型读错误可调整，不终止 |
| 永久放行 | 写本地级 YAML | 用户已确认落点；local 文件可 gitignore |