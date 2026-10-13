# state15 Skill 系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `codepilot/skills/model.py` | `Skill` 数据类 + `SkillError` |
| 新建 | `codepilot/skills/loader.py` | frontmatter+Markdown 解析、占位符渲染 |
| 新建 | `codepilot/skills/resolver.py` | 三级发现 + 优先级覆盖 + 单文件隔离 + 白名单 fail-fast |
| 新建 | `codepilot/skills/manager.py` | `SkillManager`（known/激活/钉住/白名单并集/清空） |
| 新建 | `codepilot/skills/tool.py` | `LoadSkillTool`（系统级，不受白名单约束） |
| 新建 | `codepilot/skills/__init__.py` | 子包导出 + `BUILTIN_DIR` |
| 新建 | `codepilot/skills/data/{commit.md,review/SKILL.md,review/template.md,test.md}` | 内置三样板（F9，随包打包） |
| 修改 | `codepilot/tools/base.py` | `Tool.system_exempt` 属性（默认 False） |
| 修改 | `codepilot/tools/__init__.py` | 导出 `LoadSkillTool` |
| 修改 | `codepilot/prompt/sections.py` | `SKILL_TAG` + `format_skills()` |
| 修改 | `codepilot/prompt/builder.py` | `build_system_prompt(..., skill_blocks=())` |
| 修改 | `codepilot/agent.py` | `skill_injector`/`skill_whitelist` + `_build_tools` 交集 |
| 修改 | `codepilot/commands/registry.py` | `register(spec, *, replace=False)` |
| 修改 | `codepilot/commands/builtins.py` | 移除 `/review` PROMPT 命令（载荷并入 review skill） |
| 修改 | `codepilot/ui/app.py` | 装配 manager + 命令注册(+strip `/review` 载荷) + isolated 编排 + `/clear` 联动 |
| 修改 | `codepilot/main.py` | 装配 `SkillManager` + 注册 `LoadSkillTool` |
| 修改 | `pyproject.toml` | `package-data` 收录 `skills/data/**` |
| 新建 | `tests/test_skills_loader.py` | F1 |
| 新建 | `tests/test_skills_resolver.py` | F2/F3 |
| 新建 | `tests/test_skills_manager.py` | F4/F5/F7 |
| 新建 | `tests/test_skills_tool.py` | F4 |
| 新建 | `tests/test_skills_injection.py` | F5/F7 |
| 新建 | `tests/test_skills_commands.py` | F8 |
| 修改 | `tests/test_commands.py` | 适配 `/review` 移除 |

## 执行顺序

```
T1 → T3 → T4 → T5
  ↘ T2（独立，T6 依赖）→ T6
T3 → T7 → T8            （T8 依赖 T3/T7）
T5/T8/T9 → T11
T9 → T10（data+打包，可与 T9/T11 并行）→ T11 → T12
```

---

## T1: `skills/model.py`

**文件：** `codepilot/skills/model.py`
**依赖：** 无
**步骤：**
1. 定义 `SkillError(Exception)`。
2. 定义 `Skill`（frozen dataclass）：`name, description, body, tools: frozenset[str], mode: str, bring_history: int = 0, model: str | None = None, path: Path | None = None, params_desc: str = ""`。
3. `mode` 用 `Literal["shared","isolated"]`；`tools` 存 frozenset。

**验证：** `python -c "from codepilot.skills.model import Skill, SkillError"` 成功。

---

## T2: `tools/base.py` 加 system 豁免 + 导出

**文件：** `codepilot/tools/base.py`、`codepilot/tools/__init__.py`
**依赖：** 无（独立于 skills 包）
**步骤：**
1. `Tool` 增 property `system_exempt -> bool`，默认 `False`。
2. `__init__.py` 暂不导 `LoadSkillTool`（T6 后有），此步只加 base 属性。

**验证：** `python -c "from codepilot.tools.base import Tool; assert Tool.system_exempt is not None"`（读默认 False）。

---

## T3: `skills/loader.py`

**文件：** `codepilot/skills/loader.py`、`tests/test_skills_loader.py`
**依赖：** T1
**步骤：**
1. `parse_skill_file(path) -> Skill`：读文本 → 解析首个 `---\n...\n---` 块为 YAML，余下为 `body`。
2. frontmatter 必填校验：缺 `name`/`description`/`mode` 或不合法 `mode` → 抛 `SkillError`。
3. `tools` 读取：frontmatter 的 `tools` 可以是 YAML list 或逗号串，统一 → frozenset。
4. 其它字段 `bring_history`（int）、`model`（str|None）、`params_desc`（str）按缺省兜底。
5. `render_body(body, params) -> str`：把正文 `{input}` 占位替换为 `params`（去首尾空白）；无 `{input}` 时正文原样返回。
6. `tests/test_skills_loader.py`：写临时 `.md`（frontmatter+正文）断言解析字段；断言缺 `name`/`mode` 非法抛 `SkillError`；断言 `{input}` 替换。

**验证：** `python -m pytest tests/test_skills_loader.py -v` 绿。

---

## T4: `skills/resolver.py`

**文件：** `codepilot/skills/resolver.py`、`tests/test_skills_resolver.py`
**依赖：** T3
**步骤：**
1. `discover_skill_dirs(workspace, *, user_root, builtin_root) -> list[Path]`：返回 `[builtin_root, user_root, *项目递归 .skills 目录(深度升序)]`，镜像 `memory/resolver.py`（剪枝 `.git/node_modules/__pycache__/.venv`）。
2. `load_all(workspace, *, user_root, builtin_root, check_tools=None) -> dict[str, Skill]`：按目录序加载，同名 dict 赋值**后覆盖前**（项目胜出）；目录型取目录内 `SKILL.md` 为入口，其余资源文件仅随目录定位不参与覆盖。
3. 单文件解析失败（`SkillError`/OSError）捕获记日志**跳过该文件**，不阻断整体。
4. 加载后对每个 skill 的 `tools` 逐名调 `check_tools(name)`，返回 False → 抛 `SkillError`（启动 fail-fast）。
5. `tests/test_skills_resolver.py`：临时三级目录各放同名 skill，断言项目胜出；断言目录型 `SKILL.md` 被识别；断言单个坏文件被跳过、其余仍返回；断言 `check_tools` 报不存在工具抛 `SkillError`。

**验证：** `python -m pytest tests/test_skills_resolver.py -v` 绿。

---

## T5: `skills/manager.py`

**文件：** `codepilot/skills/manager.py`、`tests/test_skills_manager.py`
**依赖：** T4
**步骤：**
1. `SkillManager`：构造收 `known: dict[str, Skill]`（由调用方用 `load_all` 得到）、`on_register_command`, `on_unregister_command`（可选回调）。
2. `phase1_block() -> str`：按 name 排序，逐条 `名字 — 说明`，包成一段（供阶段一）。
3. `load_skill(name, params) -> tuple[Skill, str]`：取 known；`render_body`；记住激活（内部 active 集含渲染后正文）；触发 `on_register_command(name, handler)`；返回 `(skill, rendered)`。
4. `active_blocks() -> list[str]`：已激活的渲染正文列表。
5. `active_whitelist() -> set[str] | None`：激活 skill 各 `tools` 的**并集**；无激活 → None。
6. `deactivate_all()`：清空激活集与白名单（保留 known 与已注册命令）。
7. `command_handler(name)`：返回 CLI 触发用的 handler（见 planner：shared → 转发；isolated → 由 App 覆写，此处只给占位/标记）。
8. `tests/test_skills_manager.py`：伪造 known，断言 phase1 文本、激活后 active_blocks 含渲染正文、active_whitelist 并集、deactivate_all 复位、命令回调被触发。

**验证：** `python -m pytest tests/test_skills_manager.py -v` 绿。

---

## T6: `skills/tool.py` + `tools/__init__.py` 导出

**文件：** `codepilot/skills/tool.py`、`codepilot/tools/__init__.py`、`tests/test_skills_tool.py`
**依赖：** T2、T5
**步骤：**
1. `LoadSkillTool(manager)`：`name="load_skill"`、`description` 说明「加载/激活一个 Skill，返回其完整 SOP 指令并启用该 skill 的工具白名单」。
2. `parameters` JSON Schema：`name`(required string) + `params`(optional string)。
3. `system_exempt` 覆写为 `True`（不受白名单约束）。
4. `execute(name, params="")`：`manager.load_skill(name, params)` → 返回 rendered 字符串；抛 `SkillError` → 转 `ToolResult(error=...)`。
5. `tools/__init__.py` 导入并导出 `LoadSkillTool`。
6. `tests/test_skills_tool.py`：假 manager 断言 execute 返回渲染正文；system_exempt==True；未知 name 返回 error。

**验证：** `python -m pytest tests/test_skills_tool.py -v` 绿；`python -c "from codepilot.tools import LoadSkillTool"`。

---

## T7: prompt 层 Skill 注入

**文件：** `codepilot/prompt/sections.py`、`codepilot/prompt/builder.py`
**依赖：** T3
**步骤：**
1. `sections.py`：新增 `SKILL_TAG = "skill_instruction"`；新增 `format_skills(name_pairs) -> str`（生成「可用 Skill」块，含 `<SKILL_TAG>` 包裹）。
2. `builder.py`：`build_system_prompt` 增 `skill_blocks: Sequence[str] = ()`；在 `volatile` 中置于 env 之后、plan / memory 之前（"最显眼"位），每块用 `_prompt_tag`（SESSION_TAG）包装。现有调用不传则行为不变。

**验证：** `python -c "from codepilot.prompt.builder import build_system_prompt; build_system_prompt(cwd='',date='',plan_mode=False,round_num=1,refresh_every=3,skill_blocks=['x']) 不报错"`。

---

## T8: `agent.py` 注入 + 白名单交集

**文件：** `codepilot/agent.py`、`tests/test_skills_injection.py`
**依赖：** T3、T7
**步骤：**
1. `__init__` 增 `skill_injector: Callable[[], list[str]] | None = None`、`skill_whitelist: Callable[[], set[str] | None] | None = None`。
2. `run()` 每轮：`skill_blocks = skill_injector() if skill_injector else []` → 传入 `build_system_prompt(..., skill_blocks=skill_blocks)`。
3. `_build_tools()` 改为：全量 → 若 `skill_whitelist()` 非 None 则过滤为「白名单 ∪ system_exempt」→ plan 时再滤只读。
4. `tests/test_skills_injection.py`：用假 registry + 假 skill_injector/whitelist，跑 `run`（mock provider 单轮直接 done），断言 `build_system_prompt` 收到 skill_blocks；断言 `_build_tools` 在无白名单/有白名单/白名单∩plan 三种下返回正确的工具子集；断言 system_exempt（load_skill）在任何白名单下都在。

**验证：** `python -m pytest tests/test_skills_injection.py -v` 绿。

---

## T9: `commands/registry.py` 支持 replace

**文件：** `codepilot/commands/registry.py`
**依赖：** 无（独立于 skills，但供 T11 用）
**步骤：**
1. `register(spec, *, replace: bool = False)`：`replace=True` 时，若 `spec.name` 或任一别名与已注册者相撞，先移除旧 spec 再注册（供热更新 + skill 覆盖内置）。
2. 保持 `assert_no_conflicts` 启动校验语义不变。

**验证：** `python -c "from codepilot.commands import CommandRegistry, CommandSpec"` 后构造注册一个 spec 再以 replace 覆盖同名，不抛错（可在 T13 单测覆盖）。

---

## T10: 内置样板数据 + 打包

**文件：** `codepilot/skills/data/{commit.md,review/SKILL.md,review/template.md,test.md}`、`pyproject.toml`
**依赖：** 无（可与 T9/T11 并行）
**步骤：**
1. 写 `commit.md`：frontmatter（name=commit, desc, mode=shared, tools=[read,grep,run,edit,write], 说明）+ 正文 SOP（读 git status/diff → 按变更写提交信息 → 遵守 .rules 中文 Commit）。
2. 写 `review/SKILL.md`（目录型示范，name=review, desc, mode=isolated, bring_history, tools=[...], model 可选）+ 正文 SOP（含 `{input}` 占位，审查未提交改动）。写 `review/template.md` 作为辅助模板（供正文引用示意）。
3. 写 `test.md`：frontmatter（name=test, mode=shared, tools=[run,read,grep]）+ 正文 SOP（定位并运行相关测试，报告结果，红则修）。
4. `pyproject.toml`：`[tool.setuptools.package-data]` 加 `codepilot = ["skills/data/**/*", "skills/data/*"]`（或 `include-package-data`）。

**验证：** 每个文件能被 `parse_skill_file` 解析（T11 的全量回归会覆盖）；先手动 `python -c` 解析其中一个确认无 frontmatter 语法错。

---

## T11: `ui/app.py` 装配 + isolated 编排

**文件：** `codepilot/ui/app.py`、`codepilot/commands/builtins.py`
**依赖：** T5、T8、T9、T10（T6 的 tool 已在 registry）
**步骤：**
1. `builtins.py`：移除 `/review` PROMPT 命令（其载荷并入 review skill 正文；`REVIEW_PROMPT` 常量删除或标记废弃）。
2. `CodePilotApp.__init__` 增入参：`skill_manager`、`skill_tool`（或由 App 构造）。
3. 装配：注册 `skill_tool` 进现有 ToolRegistry（系统工具）；把 `skill_injector`/`skill_whitelist` 传给 Agent。
4. 命令注册：对 manager 已知的**内置** skill，启动时 `registry.register(..., replace=True)` 注册其命令（review 即 `/review`，覆盖/取代旧的）；用户/项目 skill 由 `on_register_command` 回调在其 `load` 时注册。
5. `_CommandContextImpl` 增 `run_isolated_skill(skill, params)`：拷贝最近 `bring_history` 条 `_msgs` → 用 skill 正文为独立 system、按 skill.model 覆盖 provider（缺省沿用当前）→ 开一轮 agent 跑 → 取最终文本 → `ctx.show(摘要/结论)` 落主历史（不污染内部 `_msgs`）。
6. skill 命令 handler：shared → 渲染 + 返回 params（转发 AI，SOP 已钉住）；isolated → `ctx.run_isolated_skill(...)` 返回 None（run_worker 编排）。
7. `/clear` 现有逻辑前调 `skill_manager.deactivate_all()`。

**验证：** `python -c "import codepilot.ui.app"` 成功；`python -c "from codepilot.commands import build_registry; r=build_registry(); assert any(s.name=='review' for s in r.all())"`（无旧冲突）。

---

## T12: `main.py` 装配

**文件：** `codepilot/main.py`
**依赖：** T11
**步骤：**
1. 构造 `SkillManager(workspace, user_root=~/.skills, builtin_root=codepilot/skills/data, check_tools=lambda n: registry.get(n) is not None, on_register_command=...)`。
2. `_build_registry` 后 `registry.register(LoadSkillTool(skill_manager))`。
3. 把 `skill_manager` 注入 `CodePilotApp`。
4. 兜底：注册回调缺省为 no-op（App 内实际接线）。

**验证：** `python -c "import codepilot.main"` 成功（不跑 UI）。启动 CI/冒烟见 T13 全量回归。

---

## T13: 命令测试 + 全量回归

**文件：** `tests/test_skills_commands.py`、`tests/test_commands.py`
**依赖：** T9、T11
**步骤：**
1. `tests/test_skills_commands.py`：断言——已知 skill 命令注册可解析；撞名（skill 命令 vs 内置，如 `/review`）skill 胜出；`register(replace=True)` 热更新重注册同名不重复；`deactivate_all` 复位白名单但命令保留。
2. `tests/test_commands.py`：移除/调整 `/review` PROMPT 相关断言（已并入 review skill）。
3. `pytest -q` 全量绿（既有 338 + state15 新增）；`ruff check .` 无新增告警（既有 30 基线不动）。

**验证：** `python -m pytest -q` 全绿；`ruff check .` 仅既有基线。

---

## T14: 文档收尾 + 提交

**文件：** `SHARED_PROGRESS.md`、`docs/decisions.md`
**依赖：** T13
**步骤：**
1. `SHARED_PROGRESS.md` 顶部追加 state15 段落（两条决策记录、真机登记：/review 统一、/clear 联动的注意）。
2. `docs/decisions.md` 增 ADR（如必要，记「skill 命令 replace 胜出」「/review 并入 review skill」）。
3. 按 CLAUDE.md 分步提交（docs → 各功能模块 → test → 快照）。

**验证：** 文档与代码一致；提交信息用中文 + Conventional Commits 前缀。

---

> 注：每个任务完成后必须跑该任务「验证」步骤，先有证据再下结论；被阻塞时停下来问，不猜。