# state15 Skill 系统 Plan

## 架构概览

在**既有三条通道上叠加** Skill 能力，不另起炉灶：

1. **存储/解析层（新包 `codepilot/skills/`）**：仿 `memory/resolver.py` 做三级发现 + 优先级覆盖；frontmatter+Markdown 解析；正文占位符渲染。
2. **两阶段注入（复用 `prompt/builder.py` 的 volatile 尾块）**：阶段一产「名字+一句说明」块；阶段二激活后产「完整指令」钉住块。Agent 侧仿 state13 的 `memory_injector`，新增 `skill_injector` 每轮重建。
3. **工具白名单（复用 `agent._build_tools()` 的过滤）**：新增 `skill_whitelist` 回调，在既有 plan 只读过滤上再叠加白名单交集。
4. **执行分发（复用 state14 命令注册 + `provider`）**：Skill 加载后动态注册斜杠短命令；独立模式用现有非流式/独立对话能力开新对话跑完回流摘要。

```
┌─────────────────────────────────────────────────────────────────────┐
│ UI/app.py                                                            │
│   handler(命令→执行skill)  ·  isolated独立对话编排  ·  /clear联动     │
│   CommandRegistry(动态注册, skill命令胜出)                            │
└──────────────┬───────────────────────────────┬──────────────────────┘
               │ 动态注册命令                    │ 实现 CommandContext（state14）
       ┌───────▼───────────────────┐   ┌───────▼──────────────────────┐
       │ commands/ (state14现有)    │   │ SkillManager                 │
       │  registrregister(支持replace)│◄──│  known→phase1块            │
       └───────────┬───────────────┘   │  load→激活+白名单+命令注册   │
                   │                   │  active_blocks→钉住           │
                   │                   └───────┬──────────────────────┘
                   │                 读取      │
       ┌───────────▼───────────────────────────▼──────────┐
       │ Agent.run                                         │
       │  _build_tools(): 全量 ∩(白名单∪系统工具) ∩(plan只读)│
       │  build_system_prompt(env, plan, memory, skill)     │
       └───────────┬────────────────────────────────────────┘
                   │ stream / load_skill 工具
       ┌───────────▼───────────────────┐      ┌───────────────┐
       │ provider.stream / complete    │      │ skills/ (包)  │
       │ (shared当前对话 / isolated独立)│      │ resolver→data │
       └───────────────────────────────┘      └───────────────┘
```

- **stages：** 阶段一（`manager.phase1_block()`，全部已知 skill 的名字+一句说明）；阶段二（`load_skill` 工具 → `manager.load()`，激活并钉住完整指令 + 白名单 + 注册命令）。
- **两种执行：** shared 走模型当前对话（SOP 作为上下文钉住）；isolated 由命令 handler 调 App 开独立 agent 跑，`provider` 结果回传摘要。
- **白名单：** build 时主对话可见工具 = 全量 ∩（激活 skill 白名单 ∪ 系统工具）∩（plan 时仅只读）；`load_skill` 属系统工具恒可见。

## 核心数据结构

### `Skill`（dataclass，解析结果）
```python
@dataclass(frozen=True)
class Skill:
    name: str                    # 唯一名（= frontmatter name，也是命令名默认来源）
    description: str             # 一句话说明（阶段一）
    body: str                    # SOP 正文（解析后，含占位符）
    tools: frozenset[str]        # 工具白名单（可为空 = 白名单不约束？见决策）
    mode: Literal["shared", "isolated"]
    bring_history: int = 0       # 仅 isolated 有效
    model: str | None = None     # 可选指定模型（仅 isolated 生效）
    path: Path | None = None     # 来源文件（内置/用户/项目）
    params_desc: str = ""        # 参数说明（供命令用法/提示）
```

### `SkillManager`
```python
class SkillManager:
    def __init__(self, workspace, *, user_root, builtin_root,
                 check_tools: Callable[[str], bool],   # 校验工具名存在(白名单fail-fast)
                 on_register_command: Callable[[str, Callable], None] | None,
                 on_unregister_command: Callable[[str], None] | None):
        ...
    def known(self) -> dict[str, Skill]: ...       # 三级覆盖后最高优先级版本
    def phase1_block(self) -> str: ...             # 全部已知 skill 的名字+一句说明
    def load(self, name, params) -> Skill: ...     # 激活+渲染正文，返回该 Skill
    def record_active(self, skill, rendered_body): # 加入激活集（含钉住正文+白名单）
    def active_blocks(self) -> list[str]: ...      # 已激活完整指令块（每轮钉住）
    def active_whitelist(self) -> set[str] | None: # 当前生效白名单（无激活=None）
    def deactivate_all(self) -> None: ...          # 清空激活±白名单（/clear联动）
    def require(self, name) -> Skill: ...          # 取 skill，缺失抛 SkillError
```

### `LoadSkillTool(Tool)`（系统级工具）
- `name="load_skill"`，`system_exempt=True`（不受白名单约束）。
- `execute(name, params)`：`manager.load(name, params)` → 渲染正文 → `record_active` → 注册命令回调 → 返回渲染后完整指令字符串作为工具结果（当场可见，下轮钉住）。
- 命令注册回调由 App 注入（把 Skill 命令注册进 state14 registry）。

## 模块设计

### `skills/model.py`
**职责：** `Skill` 数据类 + `SkillError`。
**依赖：** 无。

### `skills/loader.py`
**职责：** frontmatter+Markdown 解析、占位符渲染（F1）。
**对外接口：** `parse_skill_file(path) -> Skill`、`render_body(body, params) -> str`。
**依赖：** pyyaml。
- 拆 `---\n...\n---` 前段为 YAML、后段为正文；缺 `name`/`description`/`mode` 抛 `SkillError`（调用方捕获跳过）。
- `mode` 仅 `shared`/`isolated`，否则抛错；`tools` 读逗号/列表均可。
- `render_body`: 把正文里的 `{input}` 替换为用户传入文本（去首尾空白）；未提供则保留占位或留空——细节见 task。

### `skills/resolver.py`
**职责：** 三级发现 + 优先级覆盖 + 单文件故障隔离（F2/F3/N2/N3）。
**对外接口：** `discover_skill_dirs(workspace, user_root, builtin_root) -> list[Path]`、`load_all(workspace, *, user_root, builtin_root, check?) -> dict[str, Skill]`。
**依赖：** loader。
- 目录序：`[内置, 用户, *项目递归]`（**优先级高的后覆盖**，最终 `dict` 里同名以后者为胜 → 项目胜出）。镜像 `memory/resolver.py` 的深度升序、剪枝 `.git/node_modules/__pycache__/.venv`。
- 单文件：files（`.md` 或目录型入口）逐个解析，单个 `SkillError`/OSError 捕获记日志跳过，不阻断整体（N2）。
- 目录型：目录内 `SKILL.md` 为入口，其余 `.md/.py/.css 等` 作为辅助资源一并随目录解析（路径记录供引用）。
- 启动白名单校验：加载后对每个 skill 的 `tools` 调 `check_tools`，命中不存在工具 → `SkillError`（fail-fast，N1）。

### `skills/manager.py`
**职责：** 组合 resolver + 激活状态 + 两阶段产出（F4/F5/F6/F7/F8 状态面）。
**对外接口：** 见 `SkillManager`。
**依赖：** resolver、model、context（命令回调仅类型标注）。
- `phase1_block()`：`## 可用 Skill（名字 — 说明）` 逐条列，供阶段一注入。
- `load()`：校验 + `render_body` + `record_active` + 触发 `on_register_command(name, handler)`。
- `active_whitelist()`：多激活时取**并集**（各 skill 白名单合并）；空集即 = 无白名单约束？→ 见决策（`tools` 为空 = 该 skill 不约束可选工具，不参与收窄；但为保证逃生门，系统工具恒在）。

### `skills/tool.py`
**职责：** 模型侧两阶段「按需加载」入口（F4）。
**对外接口：** `LoadSkillTool(manager)`。
**依赖：** manager、tools.base。
- 这是唯一让模型触发加载的工具；`system_exempt=True`。

### `skills/__init__.py`
导出 `Skill`、`SkillManager`、`LoadSkillTool`、`render_body`、`load_all`、内置 skill 数据目录常量 `BUILTIN_DIR`。

### `skills/data/{commit.md, review/SKILL.md, test.md}`（内置样板，F9）
- 三个内置 Skill 作为随包数据（`package-data` 打包）。commit、test 为单文件；review 用目录型示范 `SKILL.md + 模板`。
- review 的 `name=review`，命令 `/review`；state14 内置 `/review` PROMPT 命令**移除**，载荷并入此 skill 正文（统一入口，F8）。

### `prompt/builder.py`（改）
- `build_system_prompt(..., skill_blocks: Sequence[str] = ())`：把 skill 块（阶段一已知列表 + 激活钉住块）放进 volatile 尾块的**固定区段**，用专用标签 `SKILL_TAG`，置于 env 之后、memory 之前（"最显眼"位）。
- 兼容既定签名（`memory_blocks` 不动）。

### `prompt/sections.py`（改）
- 新增 `SKILL_TAG = "skill_instruction"` 与格式化常量 `format_skills(names)`。

### `agent.py`（改）
- `__init__` 增 `skill_injector: Callable[[], list[str]] | None = None`（产阶段一+激活钉住块）与 `skill_whitelist: Callable[[], set[str] | None] | None`。
- `run()` 每轮：`skill_blocks = skill_injector()` 并入 `build_system_prompt`。
- `_build_tools()`：`全量 → 白名单∩系统工具 → (plan?仅只读)`。`sys_tool = registry 中 system_exempt 的工具`。
- `tools/base.py` 增 `system_exempt: bool = False`（property，LoadSkillTool 覆写为 True）。

### `commands/registry.py`（改，最小扩展）
- `register(spec, *, replace: bool = False)`：`replace=True` 时若 name 或别名撞现有，先删旧 spec 再注册——供热更新与 skill 命令覆盖内置（不破坏 `assert_no_conflicts` 启动校验语义，replace 只用于动态路径）。
- 类型层面 `handler` 与 `CommandSpec` 不变。

### `ui/app.py`（改）
- 装配 `SkillManager`；构造 `LoadSkillTool(manager)` 并 `registry.register`（系统工具）。
- 把 `skill_injector`/`skill_whitelist` 传给 Agent；命令注册回调把 Skill 命令注册进现有 `CommandRegistry`（`register(..., replace=True)`）。
- `_CommandContextImpl` 新增 `run_isolated_skill(skill, params)`：拷贝最近 `bring_history` 条 `_msgs`、以 skill 正文为独立 system、`provider`/临时 agent 跑完 → 摘要回写正文 → `ctx.show(摘要)`（结果作为一条用户可见结论落主历史，不污染内部历史）。模型覆盖：isolated 用 `provider.factory` 按 skill.model 建临时 provider（如缺省沿用当前）。
- `on_prompt_input_submitted` 收到的 skill 命令 handler：shared → `ctx.set`+提交参数（转发 AI，SOP 已钉住）；isolated → `ctx.run_isolated_skill(...)` 返回 None（run_worker 编排）。
- `/clear` 现有逻辑前先调 `manager.deactivate_all()`（清激活±白名单复位；已注册的 skill 命令保留，F8）。

### `main.py`（改）
- 构建 `SkillManager(workspace, user_root=~/.skills, builtin_root=codepilot/skills/data, check_tools=registry.get)`。
- 工具注册 `_build_registry` 后 `registry.register(LoadSkillTool(manager))`。
- 把 manager 注入 App（App 内完成命令注册回调接线）。

## 模块交互

**两阶段加载**
```
启动: main → SkillManager(扫三级) → known{name→Skill}
      agent.skill_injector → manager.phase1_block()(名字+说明)   每轮注入
运行: 模型调 load_skill(name, params) → tool.execute → manager.load
        ├ 渲染正文 render_body(body, params)
        ├ record_active(skill, rendered) → active_blocks 含之, 白名单并入
        ├ 触发 on_register_command(name, handler) → /name 进入 registry
        └ 返回 rendered 字符串(本轮当场可见) → 下轮钉住
      agent._build_tools() = 全量 ∩(active_whitelist ∪ system) ∩(plan只读)
```

**命令执行**
```
/commit(shared) → handler → manager.record/渲染 → 返回 params → _start_ai(params)   # SOP钉住,共享历史
/review(isolated)→ handler → ctx.run_isolated_skill(review, params)
                     ├ 拷贝最近 bring_history 条 _msgs
                     ├ 独立 agent: skill.model(可选) + skill.body 为 system
                     ├ 跑完 → 取最终文本
                     └ 摘要回写 → ctx.show(摘要)(落主历史, 不污染内部)
/clear → manager.deactivate_all() → active清空 + 白名单复位(命令保留)
```

## 文件组织

```
codepilot/
  skills/                      # 新包
    __init__.py
    model.py                   # Skill, SkillError
    loader.py                  # parse_skill_file, render_body
    resolver.py                # discover_skill_dirs, load_all(三级覆盖+隔离)
    manager.py                 # SkillManager
    tool.py                    # LoadSkillTool
    data/                      # 内置样板(随包 package-data)
      commit.md
      review/SKILL.md
      review/template.md        # 目录型示范辅助资源
      test.md
  prompt/
    builder.py                  # 改: build_system_prompt + skill_blocks
    sections.py                 # 改: SKILL_TAG + format_skills
  agent.py                      # 改: skill_injector/skill_whitelist + _build_tools交集
  tools/base.py                 # 改: Tool.system_exempt
  tools/__init__.py            # 改: 导出 LoadSkillTool
  commands/registry.py          # 改: register(replace=)
  ui/app.py                     # 改: 装配manager + 命令注册 + isolated编排 + /clear联动
  main.py                       # 改: 装配 SkillManager + 注册 LoadSkillTool
tests/
  test_skills_loader.py         # F1: frontmatter/占位符/非法跳过
  test_skills_resolver.py       # F2/F3: 三级优先级/覆盖/目录型/单文件隔离
  test_skills_manager.py        # F4/F5/F7: phase1/激活/钉住/白名单并集/清空
  test_skills_tool.py           # F4: load_skill 工具行为(假 manager+假 ctx)
  test_skills_injection.py      # F5/F7: prompt+agent 集成(钉住块+白名单∩plan)
  test_skills_commands.py       # F8: 动态注册/撞名胜出/热更新replace//clear复位
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 存储包位置 | 新包 `codepilot/skills/`，内置数据在 `skills/data/` 走 package-data | 代码与数据分离；`.skills` 是用户/项目目录，包内是内置模板；非 `.py` 需 `package-data` 否则 wheel 不带 |
| 内置打包 | `[tool.setuptools]` 补 `package-data`/`include-package-data` 收录 `skills/data/**` | 随包分发三样板（F9），否则 installed 后内置为空 |
| 三级覆盖实现 | resolver 从「最不具体→最具体」，同名以后者覆盖进 dict | 镜像 state13 `memory/resolver.py`，层级判定确定可测 |
| 工具白名单 | 硬收窄 + `system_exempt` 系统工具恒在 + plan 交集 | 降选择熵提准确率；`load_skill` 逃生门不锁死（F7） |
| 白名单为空语义 | `tools` 留空 = 该 skill 不约束工具（不强行收窄到只剩系统工具） | 避免「忘了写 tools 就把工具全藏了」的意外 |
| 多激活白名单 | active_whitelist = 并集 | 多 skill 同时激活时每个所需工具都可得（F5 多开） |
| 两阶段注入位 | volatile 尾块新区段 `SKILL_TAG`，置于 env 后、memory 前 | 「最显眼」+ 不破坏 state13 memory 机制（N6） |
| load_skill 生效时机 | 工具本次调用当场返回正文；白名单/钉住影响**下一轮** build | 工具结果本轮即见，钉住随轮生效，符合两阶段；不引入跨轮状态机复杂度 |
| 独立模式模型覆盖 | 仅 isolated 生效，shared 沿用当前模型 | 共享对话不能按轮切模型；isolated 是独立调用，天然可换 |
| 命令撞名 | skill 命令用 `register(..., replace=True)` 胜出；state14 `/review` PROMPT 移除并入 review skill | Q4 判定：统一入口、一个概念一份代码（F8） |
| 命令注册表扩展 | 仅给 `register` 加 `replace` 开关，不重构 | 满足热更新与 skill 覆盖，保持 `assert_no_conflicts` 启动校验不变 |
| 占位符 | 正文 `{input}` 替换为用户文本；缺省不强制 | 简单、够用；不引入结构化表单（spec 不做的事） |
| 配置 | 暂不加 config 段（目录名/根路径由常量+约定） | 与 `.rules` 一致，YAGNI |

**接口完整性自检**：`build_system_prompt` 新增可选 `skill_blocks` 不破坏现调用；`TheRegistry.register` 仅加参数；`Agent` 增两个可选注入；`CommandRegistry`/`CommandSpec` 语义不变。既有 state13 memory、state14 命令、state12 restore、plan 只读、MCP 的 `list_all` 均不被破坏（N6）。

**spec 覆盖核对**：F1↔loader · F2↔resolver(目录型) · F3↔resolver · F4↔tool+manager+injector · F5↔manager.active_blocks+builder · F6↔Isolated 编排+共享受享 →F7↔build_tools+system_exempt+fail-fast · F8↔registry.replace+App · F9↔data/*.md。全部有归属。