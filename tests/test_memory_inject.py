"""state13 F5 注入测试 —— MemoryInjector 组装 + build_system_prompt 承接。"""
from pathlib import Path

from codepilot.memory.inject import MemoryInjector
from codepilot.memory.store import NoteStore
from codepilot.prompt import EnvContext, build_system_prompt


def _injector(tmp_path: Path, ws_name: str = "ws") -> MemoryInjector:
    ws = tmp_path / ws_name
    store = NoteStore(ws, user_dir=tmp_path / "user", proj_dir=tmp_path / "proj")
    return MemoryInjector(ws, store=store)


def test_build_two_blocks(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / ".mewcode").mkdir(parents=True)
    (ws / ".mewcode/general.md").write_text("回复务必附验收清单", encoding="utf-8")
    inj = _injector(tmp_path)
    inj.store.append("preferences", ["喜欢中文回复"])
    blocks = inj.build()
    assert len(blocks) == 2
    assert "验收清单" in blocks[0]
    assert "中文回复" in blocks[1]


def test_build_empty_returns_none(tmp_path: Path):
    inj = _injector(tmp_path)
    assert inj.build() == []


def test_build_only_instructions(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / ".mewcode").mkdir(parents=True)
    (ws / ".mewcode/general.md").write_text("别用缩写", encoding="utf-8")
    inj = _injector(tmp_path)
    blocks = inj.build()  # 无笔记
    assert len(blocks) == 1
    assert blocks[0].startswith("【项目指令】")


def test_call_alias_and_volatile_tail(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / ".mewcode").mkdir(parents=True)
    (ws / ".mewcode/general.md").write_text("MEM", encoding="utf-8")
    inj = _injector(tmp_path)
    blocks = inj()
    sp = build_system_prompt(
        EnvContext(cwd=str(ws), date="2026-01-02"),
        plan_mode=False, round_num=1, refresh_every=0,
        memory_blocks=blocks,
    )
    # memory 块在 volatile 末位
    assert sp.volatile[-1] == blocks[-1]
    assert any("MEM" in b for b in sp.volatile)


def test_size_cap_respected(tmp_path: Path):
    inj = _injector(tmp_path)
    inj.store.append("references", ["x" * 200] * 100)
    idx = inj.index_block(max_lines=5, max_chars=900)
    assert len(idx) <= 900
    assert len(idx.splitlines()) <= 5