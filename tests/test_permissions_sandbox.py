"""测试路径沙箱 — .. / 软链 越界。"""

import os

from codepilot.permissions.sandbox import check_escape


def test_inside_ok(tmp_path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    assert check_escape("a.py", tmp_path) is None


def test_dotdot_escape(tmp_path):
    assert check_escape("../outside.txt", tmp_path) is not None


def test_absolute_outside(tmp_path):
    assert check_escape(str(tmp_path.parent / "out.txt"), tmp_path) is not None


def test_symlink_outside(tmp_path):
    outside = tmp_path.parent / "outside_target.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        return  # 平台不支持软链则跳过
    assert check_escape("link.txt", tmp_path) is not None


def test_subdir_ok(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.py").write_text("", encoding="utf-8")
    assert check_escape("sub/b.py", tmp_path) is None