# state15 Skill 系统 Checklist

> 每一项通过运行代码或观察行为验证，聚焦系统行为。

## 实现完整性

- [ ] `Skill` 数据结构完整（name/description/body/tools/mode/bring_history/model/path/params_desc，frozen）（验证：`tests/test_skills_loader.py` 解析断言）。
- [ ] 单个文件 frontmatter+Markdown 解析正确；缺 `name`/`description`/`mode` 或 mode 非法抛错（验证：`test_skills_loader.py`：临时坏文件抛 `SkillError`）。
- [ ] 目录型 Skill：目录内 `SKILL.md` 被正确识别为入口，辅助资源随目录可用（验证：`test_skills_resolver.py` 目录型断言）。
- [ ] 占位符渲染：正文 `{input}` 被用户传入内容替换（验证：`test_skills_loader.py` render_body 断言）。

## 集成

- [ ] 三级存放同名按 项目>用户>内置 覆盖、只取高者；单个坏文件被跳过不阻断整体（验证：`test_skills_resolver.py` 三级覆盖 + 坏文件隔离断言）。
- [ ] 启动白名单 fail-fast：skill 的 `tools` 含不存在工具名时抛 `SkillError`（验证：`test_skills_resolver.py` check_tools 断言）。
- [ ] **两阶段**：阶段一 `phase1_block()` 只含名字+一句说明；调用 `load_skill` 后才含完整正文（验证：`test_skills_manager.py` + `test_skills_injection.py`）。
- [ ] 激活后完整正文每轮重建、出现在 volatile 尾块固定区段（skill 块在 env 后、memory 前）；多 skill 可同时激活（验证：`test_skills_injection.py` 钉住块断言）。
- [ ] **白名单交集**：激活时可见工具 = 全量 ∩(白名单∪系统工具)；plan 下再滤只读；`load_skill`(system_exempt) 任何情况可见；停用恢复全量（验证：`test_skills_injection.py` + `test_skills_commands.py`）。
- [ ] **命令注册**：已加载/内置 skill 注册成斜杠短命令可解析；撞名（如 `/review`）skill 胜出；`register(replace=True)` 热更新重注册同名不重复（验证：`test_skills_commands.py`）。
- [ ] state14 `/review` PROMPT 载荷已并入 review skill、该命令移除，无重复（验证：`test_commands.py` 调整 + `test_skills_commands.py` 撞名断言）。
- [ ] `/clear` 联动：清空对话时激活 Skill 与白名单一起复位（验证：`test_skills_commands.py` deactivate_all + 命令保留断言）。
- [ ] 独立模式：`bring_history` 条历史被带入独立对话、内部历史不污染主对话、摘要回流（验证：isolated 编排单测/真机观察）。

## 验证与测试

- [ ] 项目无语法错误、模块可导入（验证：`python -c "import codepilot.skills, codepilot.ui.app, codepilot.main"`）。
- [ ] 全量单测通过（验证：`pytest -q` —— 既有 338 + state15 新增全绿）。
- [ ] lint 无回归（验证：`ruff check .` —— 仅既有 30 基线，state15 文件无新增告警）。

## 端到端场景（阶段六真机 / TUI）

- [ ] 场景 1（两阶段）：TUI 输入 `/help` 或观察启动注入，能看到内置 **commit / review / test** 的名字+一句说明（无全文）；问模型「有哪些可复用操作」能列出（验证：启动观察 + `/help` 分组含 skill）。
- [ ] 场景 2（shared 执行）：输入触发 `commit` skill（如 `/commit`），模型按该 SOP 执行并留在主历史（验证：真机跑 `/commit`，观察模型先读 git 状态再产出，结果在对话里）。
- [ ] 场景 3（isolated 执行 + 摘要回流）：触发 `review` skill（`/review`），独立对话跑完只把审查结论回主对话，内部历史不出现（验证：真机 `/review`，主对话只收到一份结论、无中间工具过程）。
- [ ] 场景 4（白名单收窄）：激活某个只有只读工具的 skill 后，模型本轮只见 read/glob/grep，看不到 write/edit/run（验证：真机激活 + 观察工具可见性/状态；或 `_build_tools` 单测覆盖）。
- [ ] 场景 5（/clear 联动）：激活某 skill，`/clear` 后激活态与白名单复位，但 skill 命令仍在（可再次触发）（验证：真机 `/clear` 后 `/commit` 仍可用）。
- [ ] 场景 6（hot update）：修改某 skill 文件内容后重新加载，命令正文/白名单随之更新（验证：真机改 `test.md` 的 frontmatter tools 后重新加载，行为变化；或单测覆盖）。
- [ ] 场景 7（fail-fast）：构造一个 frontmatter 白名单含不存在工具的 skill，启动/加载时报明确错误（验证：单测覆盖 + 可选真机）。

## 验收口径

- [ ] spec AC1–AC8 均有对应可观测条目（AC1↔loader/resolver · AC2↔resolver覆盖 · AC3↔phase1 vs 全文 · AC4↔钉住块 · AC5↔shared/isolated · AC6↔白名单交集+逃生门 · AC7↔命令注册+replace+/clear · AC8↔内置三样板可覆盖）。
- [ ] 人工真机清单（阶段五交付后对照）逐项登记实际结果，与预期不符如实记录为差异。