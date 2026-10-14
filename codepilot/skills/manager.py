"""SkillManager — 组合 resolver 结果，管理激活态与两阶段产出。

状态面：
  · known      —— 三级覆盖后生效的 Skill 集（name -> Skill）
  · active     —— 已激活（共享对话内钉住）的 Skill（name -> Skill）+ 渲染正文
两阶段产出：
  · phase1_block() = 全部已知 skill 的「名字 — 说明」（阶段一，每轮注入）
  · active_blocks() = 已激活 skill 的完整指令（阶段二钉住，每轮重建）
白名单：
  · active_whitelist() = 已激活 shared 型 skill 的 tools 并集；任一激活 shared
    skill 的 tools 为空（不约束）→ 整体返回 None（不收窄）。隔离型 skill 不钉入
    主对话、不参与主对话白名单（其 SOP 只在独立对话内生效）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from codepilot.skills.loader import render_body
from codepilot.skills.model import Skill, SkillError


class SkillManager:
    def __init__(
        self,
        known: dict[str, Skill],
        *,
        on_register_command: Callable[[str, Callable[..., Any]], None] | None = None,
        on_unregister_command: Callable[[str], None] | None = None,
    ) -> None:
        self._known = dict(known)
        self._active: dict[str, Skill] = {}
        self._rendered: dict[str, str] = {}
        self._on_register_command = on_register_command
        self._on_unregister_command = on_unregister_command

    # —— 查询 ——
    @property
    def known(self) -> dict[str, Skill]:
        return self._known

    def require(self, name: str) -> Skill:
        skill = self._known.get(name)
        if skill is None:
            raise SkillError(f"未知 Skill: {name!r}（输入 /help 或查阅可用 Skill）")
        return skill

    def phase1_block(self) -> str:
        """阶段一：全部已知 Skill 的名字 + 一句说明。"""
        if not self._known:
            return "当前没有可用 Skill。"
        lines = ["## 可复用技能（Skill）：名字 — 说明"]
        lines += [f"- {s.name} — {s.description}"
                  for s in sorted(self._known.values(), key=lambda s: s.name)]
        return "\n".join(lines)

    @property
    def active(self) -> dict[str, Skill]:
        return dict(self._active)

    def active_blocks(self) -> list[str]:
        """已激活 Skill 的完整指令（渲染后），每轮钉住。"""
        return [self._rendered[name] for name in self._active]

    def skill_blocks(self) -> list[str]:
        """Agent 每轮注入的全部 skill volatile 块：阶段一 + 已激活钉住。"""
        return [self.phase1_block(), *self.active_blocks()]

    def active_whitelist(self) -> set[str] | None:
        """当前生效白名单（并集）；任一激活 shared 型 skill 不限工具 → None。"""
        tools: set[str] = set()
        for skill in self._active.values():
            if skill.mode == "isolated":
                continue  # 隔离型不进主对话白名单
            if not skill.tools:
                return None  # 有不约束的 skill → 整体不收窄（安全）
            tools |= set(skill.tools)
        return tools or None

    # —— 变更 ——
    def load(self, name: str, params: str = "") -> str:
        """加载并激活一个 Skill（阶段二）。返回渲染后完整指令。

        触发 on_register_command(name, handler)——由调用方注册对应的斜杠短命令。
        """
        skill = self.require(name)
        rendered = render_body(skill.body, params)
        self.activate(skill, rendered)
        if self._on_register_command is not None:
            self._on_register_command(name, self.command_handler(name))
        return rendered

    def activate(self, skill: Skill, rendered: str) -> None:
        """把已渲染 Skill 钉为激活态（共享对话可见 + 白名单参与）。"""
        self._active[skill.name] = skill
        self._rendered[skill.name] = rendered

    def deactivate_all(self) -> None:
        """清空激活态与白名单（/clear 联动）。known 与已注册命令保留。"""
        self._active.clear()
        self._rendered.clear()

    def success_message(self, name: str) -> str:
        """「加载成功」的明确反馈文本，供工具/命令展示——用户可据以判断成败。"""
        skill = self.require(name)
        tools = "、".join(sorted(skill.tools)) if skill.tools else "不设限"
        return (f"✅ 已加载并激活 Skill「{name}」"
                f"（{skill.mode} 模式，工具白名单：{tools}）")

    def command_handler(self, name: str):
        """把命令触发接到 Skill 执行；加载/激活时给用户明确成功与模式反馈。"""
        def _handler(ctx, params: str):
            skill = self.require(name)
            rendered = render_body(skill.body, params)
            if skill.mode == "isolated":
                # isolated 由 ctx 开独立对话跑完回流摘要，不钉入主对话
                ctx_exe = getattr(ctx, "run_isolated_skill", None)
                if ctx_exe is not None:
                    ctx.show(f"▶ 开始独立执行 Skill「{name}」…")
                    ctx_exe(skill, params)
                    return None
            self.activate(skill, rendered)
            ctx.show(self.success_message(name))
            return params.strip() or None  # shared：转发给 AI（SOP 已钉住）
        return _handler