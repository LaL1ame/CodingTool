"""测试规则加载器 — 三级文件读取 + 跨层级判定确定性。"""

from codepilot.permissions import Verdict
from codepilot.permissions.loader import load_rules_from_files
from codepilot.permissions.rules import Rule


def _yml(path, content):
    path.write_text(content, encoding="utf-8")
    return path


def test_load_multiple_files_merge(tmp_path):
    a = _yml(tmp_path / "user.yaml", "rules:\n  - 'run(git *): allow'\n")
    b = _yml(tmp_path / "proj.yaml", "rules:\n  - 'write(**): ask'\n")
    rules = load_rules_from_files([a, b])
    assert len(rules) == 2
    assert Rule("run", "git *", Verdict.ALLOW) in rules
    assert Rule("write", "**", Verdict.ASK) in rules


def test_missing_file_ignored(tmp_path):
    assert load_rules_from_files([tmp_path / "nope.yaml"]) == []


def test_layer_independent_rule_set(tmp_path):
    """AC8：同一组规则放不同层级文件，合并出的规则集合一致（判定与层级无关）。"""
    f1 = _yml(tmp_path / "l1.yaml",
              "rules:\n  - 'run(git *): allow'\n  - 'run(git push *): deny'\n")
    f2 = _yml(tmp_path / "l2.yaml",
              "rules:\n  - 'run(git push *): deny'\n  - 'run(git *): allow'\n")
    set1 = {(r.tool, r.pattern, r.verdict) for r in load_rules_from_files([f1])}
    set2 = {(r.tool, r.pattern, r.verdict) for r in load_rules_from_files([f2])}
    assert set1 == set2