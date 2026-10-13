"""state13 F3/F5 单元测试 —— 四类笔记 IO + 索引。"""
from pathlib import Path

from codepilot.memory.store import NoteStore


def _store(tmp_path: Path) -> NoteStore:
    return NoteStore(tmp_path / "ws", user_dir=tmp_path / "user",
                     proj_dir=tmp_path / "proj")


def test_categories_land_correct_side(tmp_path: Path):
    s = _store(tmp_path)
    assert s.append("preferences", ["喜欢中文回复"])[0] == "user"
    assert s.append("corrections", ["别用GPT-4o缩写"])[0] == "user"
    assert s.append("project_knowledge", ["MewCode 是 CLI 助手"])[0] == "project"
    assert s.append("references", ["https://docs.example.xyz"])[0] == "project"

    notes = s.load_notes()
    assert "喜欢中文回复" in notes["preferences"]
    assert "MewCode 是 CLI 助手" in notes["project_knowledge"]
    # 文件落盘位置正确
    assert (tmp_path / "user/preferences.md").exists()
    assert (tmp_path / "proj/project_knowledge.md").exists()


def test_append_exact_dedup(tmp_path: Path):
    s = _store(tmp_path)
    s.append("project_knowledge", ["同一行"])
    s.append("project_knowledge", ["同一行"])   # 重复
    notes = s.load_notes()
    assert notes["project_knowledge"].count("同一行") == 1


def test_append_empty_and_unknown_cat(tmp_path: Path):
    s = _store(tmp_path)
    assert s.append("project_knowledge", ["", "  "]) == ("", 0)
    assert s.append("nope", ["x"]) == ("", 0)


def test_delete_keyword_across_sides_idempotent(tmp_path: Path):
    s = _store(tmp_path)
    s.append("preferences", ["喜欢使用 vim 键位"])
    s.append("project_knowledge", ["必须使用 ruff 检查"])
    assert s.delete("vim") == 1
    notes = s.load_notes()
    assert "喜欢使用 vim 键位" not in notes["preferences"]
    assert s.delete("vim") == 0  # 幂等
    # 无关关键词不动
    assert s.delete("ruff") == 1
    assert "必须使用 ruff 检查" not in s.load_notes()["project_knowledge"]


def test_delete_empty_keyword(tmp_path: Path):
    s = _store(tmp_path)
    s.append("preferences", ["内容"])
    assert s.delete("   ") == 0
    assert len(s.load_notes()["preferences"]) == 1


def test_to_index_grouping_and_caps(tmp_path: Path):
    s = _store(tmp_path)
    s.append("preferences", ["中文输出"])
    s.append("project_knowledge", ["依赖 txta"])
    idx = s.to_index()
    assert "[用户偏好]" in idx and "中文输出" in idx
    assert "[项目知识]" in idx and "依赖 txta" in idx
    # 字符上限优先
    s2 = _store(tmp_path)
    s2.append("references", ["x" * 200] * 200)
    big = s2.to_index(max_lines=50, max_chars=5_000)
    assert len(big) <= 5_000
    assert len(big.splitlines()) <= 50
    # 行数上限
    assert len(s2.to_index(max_lines=5).splitlines()) <= 5


def test_to_index_empty(tmp_path: Path):
    assert _store(tmp_path).to_index() == ""